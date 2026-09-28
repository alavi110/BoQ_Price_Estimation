"""
Prometheus metric primitives and the text exposition format (T118).

Why this is hand-rolled
-----------------------
``prometheus-client`` is listed in ``requirements.txt`` but is not installed in
every environment this runs in, and an import that raises at module load takes
the whole application down rather than degrading observability. The exposition
format is a small, fully specified text format, so it is implemented here
directly. The behaviours that make it faithful rather than convenient are
enforced, not assumed:

* counters are monotonic, so ``rate()`` cannot go negative;
* ``# HELP``/``# TYPE`` are emitted once per metric family, because a scraper
  rejects the whole scrape on a duplicate;
* label values are escaped, so a route containing a quote or a newline cannot
  corrupt the series after it;
* output is sorted, so two scrapes of an unchanged process are byte-identical
  and a change to a dashboard's inputs is visible in review.

A histogram's name is a prefix: ``foo_seconds`` also emits ``foo_seconds_count``
and ``foo_seconds_sum``, plus one ``foo_seconds_bucket`` series per label set
per bucket bound. The synthetic ``le`` label is always rendered last.
"""
import math
import re
import threading
from typing import Dict, List, Sequence, Tuple

#: The content type Prometheus expects from a scrape endpoint.
CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

_METRIC_NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_LABEL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

#: Latency buckets in seconds. The upper end is deliberate: the slowest
#: documented operation (a 100-item forecast) is budgeted at 120s, so the tail
#: is resolved by a real bucket rather than collapsing into ``+Inf``.
DEFAULT_BUCKETS: Tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0,
)

#: One rendered series: its name, its ``(label, value)`` pairs, and its value.
Series = Tuple[str, Tuple[Tuple[str, str], ...], float]


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def escape_label_value(value: str) -> str:
    """
    Escape a label value for the wire format.

    Backslash first, then quote, then newline. The order matters: escaping the
    backslashes this function has just introduced for the quote would double
    them, and a doubled backslash unescapes to one backslash followed by
    whatever came next.
    """
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def format_value(value: float) -> str:
    """
    Render a float as the format specifies.

    ``+Inf``, ``-Inf`` and ``NaN`` are literal tokens. Everything else goes
    through ``repr`` on the float, which round-trips exactly and is what a
    scraper will read back - and which keeps a zero reading as ``0.0`` rather
    than ``0``, matching what the reference implementation emits.
    """
    if math.isnan(value):
        return "NaN"
    if value == math.inf:
        return "+Inf"
    if value == -math.inf:
        return "-Inf"
    return repr(float(value))


def _validate_metric_name(name: str) -> None:
    if not isinstance(name, str) or not _METRIC_NAME_RE.match(name):
        raise ValueError(
            f"invalid metric name {name!r}: expected [a-zA-Z_:][a-zA-Z0-9_:]*"
        )


def _validate_label_names(labelnames: Sequence[str]) -> None:
    for name in labelnames:
        if not isinstance(name, str) or not _LABEL_NAME_RE.match(name):
            raise ValueError(
                f"invalid label name {name!r}: expected [a-zA-Z_][a-zA-Z0-9_]*"
            )


def _render(pairs: Sequence[Tuple[str, str]]) -> str:
    if not pairs:
        return ""
    body = ",".join(
        f'{name}="{escape_label_value(value)}"' for name, value in pairs
    )
    return "{" + body + "}"


# --------------------------------------------------------------------------- #
# Series
# --------------------------------------------------------------------------- #
class _Series:
    """One label-set's worth of a metric family."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    @property
    def value(self) -> float:  # pragma: no cover - overridden
        raise NotImplementedError

    def inc(self, amount: float = 1.0) -> None:
        if amount < 0:
            raise ValueError(f"increment must not be negative, got {amount}")
        with self._lock:
            self._store(self._read() + amount)

    def dec(self, amount: float = 1.0) -> None:
        if amount < 0:
            raise ValueError(f"decrement must not be negative, got {amount}")
        with self._lock:
            self._store(self._read() - amount)

    def _read(self) -> float:  # pragma: no cover - overridden
        raise NotImplementedError

    def _store(self, value: float) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class _NumberSeries(_Series):
    def __init__(self) -> None:
        super().__init__()
        self._value = 0.0

    def _read(self) -> float:
        return self._value

    def _store(self, value: float) -> None:
        self._value = value

    @property
    def value(self) -> float:
        with self._lock:
            return self._value


class _CounterSeries(_NumberSeries):
    def set(self, value: float) -> None:
        """
        Refuse a decrease.

        The whole vocabulary built on counters - ``rate()``, ``increase()``,
        ``resets()`` - assumes a series only ever goes up. A counter that can go
        down reports a negative rate and every alerting rule written as
        ``rate(...) > 0`` silently stops firing.
        """
        with self._lock:
            if value < self._value:
                raise ValueError(
                    f"counter is monotonic: cannot go from {self._value} to {value}"
                )
            self._value = float(value)


class _GaugeSeries(_NumberSeries):
    """
    A gauge may be negative - a queue depth and a temperature delta are both
    ordinary signed values - so unlike a counter it never refuses one.
    """

    def set(self, value: float) -> None:
        with self._lock:
            self._value = float(value)


class _HistogramSeries(_Series):
    def __init__(self, bounds: Sequence[float]) -> None:
        super().__init__()
        self._bounds = list(bounds)
        # Raw, *non*-cumulative counts: an observation lands in exactly one
        # bucket, the first whose bound it does not exceed. The prefix sum is
        # taken at render time, which is what the format asks for. Counting an
        # observation into every bucket it fits would make the cumulative
        # buckets disagree with ``_count`` - the +Inf bucket would report more
        # observations than were actually made.
        self._counts = [0.0] * len(self._bounds)
        self._overflow = 0.0
        self._sum = 0.0
        self._count = 0.0

    def observe(self, value: float) -> None:
        if value < 0:
            raise ValueError(
                f"histogram observation must not be negative, got {value}"
            )
        with self._lock:
            self._sum += value
            self._count += 1
            for index, bound in enumerate(self._bounds):
                if value <= bound:
                    self._counts[index] += 1
                    return
            # Above the largest finite bound, so only +Inf will count it.
            self._overflow += 1

    def rows(self) -> List[Tuple[float, float]]:
        """``(le, cumulative count)`` - the format wants cumulative."""
        cumulative = 0.0
        out = []
        for bound, count in zip(self._bounds, self._counts):
            cumulative += count
            out.append((bound, cumulative))
        out.append((math.inf, cumulative + self._overflow))
        return out

    def total(self) -> float:
        with self._lock:
            return self._sum

    def count(self) -> float:
        with self._lock:
            return self._count

    @property
    def value(self) -> float:
        return self.count()


# --------------------------------------------------------------------------- #
# Metric families
# --------------------------------------------------------------------------- #
class _Metric:
    """Label handling and rendering shared by every family."""

    _type = "untyped"

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str] = (),
    ):
        _validate_metric_name(name)
        _validate_label_names(labelnames)
        if not documentation:
            raise ValueError(f"metric {name!r} needs a non-empty description")
        self.name = name
        self.documentation = documentation
        self.labelnames: Tuple[str, ...] = tuple(labelnames)
        self._lock = threading.Lock()
        self._children: Dict[Tuple[str, ...], _Series] = {}
        if not self.labelnames:
            # An unlabelled metric has exactly one series, created up front. A
            # dashboard querying it before the first event should read 0, not
            # "no data" - which reads as though the metric were never wired up.
            self._children[()] = self._new_series()

    def _new_series(self) -> _Series:
        return _NumberSeries()

    # -- labels ------------------------------------------------------------ #
    def labels(self, *values: str, **named: str) -> _Series:
        """
        The series for these label values.

        Labels are addressed by name. A misspelling is an error rather than a
        second series, which is the difference between noticing ``methd="GET"``
        in review and shipping a metric that has both ``method`` and ``methd``
        and cannot be queried by either.
        """
        if values and named:
            raise ValueError(
                f"{self.name}: pass label values positionally or by name, not both"
            )
        if not self.labelnames:
            if values or named:
                raise ValueError(f"{self.name} has no labels")
            resolved: Tuple[str, ...] = ()
        elif values:
            if len(values) != len(self.labelnames):
                raise ValueError(
                    f"{self.name} expects {len(self.labelnames)} label values, "
                    f"got {len(values)}"
                )
            resolved = tuple(str(value) for value in values)
        else:
            unknown = [name for name in named if name not in self.labelnames]
            if unknown:
                raise ValueError(f"{self.name} has no label {unknown[0]!r}")
            missing = [name for name in self.labelnames if name not in named]
            if missing:
                raise ValueError(f"{self.name} is missing label {missing[0]!r}")
            resolved = tuple(str(named[name]) for name in self.labelnames)

        with self._lock:
            child = self._children.get(resolved)
            if child is None:
                child = self._new_series()
                self._children[resolved] = child
            return child

    def reset(self) -> None:
        """Drop every series, then re-create the unlabelled one. Used between tests."""
        with self._lock:
            self._children.clear()
            if not self.labelnames:
                self._children[()] = self._new_series()

    def _pairs(self, key: Tuple[str, ...]) -> Tuple[Tuple[str, str], ...]:
        """
        The label pairs for a series, sorted by label name.

        Canonical rather than declaration-ordered so that two families written
        with the same labels in a different order render identically. The format
        does not require an order, but a stable one is what makes a change to
        the output visible in review.
        """
        return tuple(sorted(zip(self.labelnames, key)))

    def _keys(self) -> List[Tuple[str, ...]]:
        with self._lock:
            return sorted(self._children)

    # -- rendering --------------------------------------------------------- #
    def series(self) -> List[Series]:
        """Every series in this family, in label order."""
        return [
            (self.name, self._pairs(key), self._children[key].value)
            for key in self._keys()
        ]

    def render(self) -> List[str]:
        """
        The ``# HELP``, ``# TYPE`` and value lines for this family.

        Exactly one of each header regardless of how many series exist: a
        duplicate makes a scraper reject the entire scrape, not just the metric.
        """
        lines = [
            f"# HELP {self.name} {self.documentation}",
            f"# TYPE {self.name} {self._type}",
        ]
        for name, pairs, value in self.series():
            lines.append(f"{name}{_render(pairs)} {format_value(value)}")
        return lines


class Counter(_Metric):
    """
    A monotonically increasing total.

    Decreases are refused rather than allowed, because the alerting vocabulary
    built on counters assumes a series only ever goes up.
    """

    _type = "counter"

    def _new_series(self) -> _Series:
        return _CounterSeries()

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        self.labels(**labels).inc(amount)

    def get(self, **labels: str) -> float:
        return self.labels(**labels).value

    def set(self, value: float, **labels: str) -> None:
        self.labels(**labels).set(value)


class Gauge(_Metric):
    """A value that can move in both directions."""

    _type = "gauge"

    def _new_series(self) -> _Series:
        return _GaugeSeries()

    def set(self, value: float, **labels: str) -> None:
        self.labels(**labels).set(value)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        self.labels(**labels).inc(amount)

    def dec(self, amount: float = 1.0, **labels: str) -> None:
        self.labels(**labels).dec(amount)

    def get(self, **labels: str) -> float:
        return self.labels(**labels).value


class Histogram(_Metric):
    """
    Observations bucketed into a cumulative distribution.

    ``+Inf`` is appended by the implementation and may not be supplied: a
    caller-provided ``+Inf`` would sort into the middle of the list and make
    ``histogram_quantile`` return nonsense rather than fail.
    """

    _type = "histogram"

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str] = (),
        buckets: Sequence[float] = DEFAULT_BUCKETS,
    ):
        # Validated before the base constructor runs, because that constructs
        # the unlabelled series and it needs the bounds.
        self.buckets = self._validate_buckets(name, buckets)
        super().__init__(name, documentation, labelnames)

    @staticmethod
    def _validate_buckets(
        name: str, buckets: Sequence[float]
    ) -> Tuple[float, ...]:
        bounds = [float(bound) for bound in buckets]
        for bound in bounds:
            if not math.isfinite(bound):
                raise ValueError(
                    f"bucket bounds must be finite (+Inf is added by the "
                    f"implementation), got {bound} for {name!r}"
                )
        for previous, current in zip(bounds, bounds[1:]):
            if current < previous:
                raise ValueError(
                    f"bucket bounds for {name!r} must be ascending, got "
                    f"{previous} then {current}"
                )
            if current == previous:
                raise ValueError(
                    f"duplicate bucket bound {current} for {name!r}"
                )
        return tuple(bounds)

    def _new_series(self) -> _HistogramSeries:
        return _HistogramSeries(self.buckets)

    def observe(self, value: float, **labels: str) -> None:
        self.labels(**labels).observe(value)

    def series(self) -> List[Series]:
        rows: List[Series] = []
        for key in self._keys():
            child = self._children[key]
            pairs = self._pairs(key)
            for bound, count in child.rows():
                rows.append(
                    (
                        f"{self.name}_bucket",
                        pairs + (("le", format_value(bound)),),
                        count,
                    )
                )
            rows.append((f"{self.name}_sum", pairs, child.total()))
            rows.append((f"{self.name}_count", pairs, child.count()))
        return rows

    def render(self) -> List[str]:
        """
        One ``# HELP``/``# TYPE`` pair for the family.

        The three expanded names are *not* given their own header lines: the
        format says the ``_bucket``/``_sum``/``_count`` series belong to the
        family named by ``# TYPE``.
        """
        lines = [
            f"# HELP {self.name} {self.documentation}",
            f"# TYPE {self.name} histogram",
        ]
        for name, pairs, value in self.series():
            lines.append(f"{name}{_render(pairs)} {format_value(value)}")
        return lines


class Registry:
    """A set of metric families, rendered on demand."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._metrics: Dict[str, _Metric] = {}

    def register(self, metric: _Metric) -> _Metric:
        with self._lock:
            if metric.name in self._metrics:
                raise ValueError(f"metric {metric.name!r} is already registered")
            self._metrics[metric.name] = metric
        return metric

    def unregister(self, name: str) -> None:
        with self._lock:
            self._metrics.pop(name, None)

    def metrics(self) -> List[_Metric]:
        with self._lock:
            return [self._metrics[name] for name in sorted(self._metrics)]

    def render(self) -> str:
        """
        The scrape body, newline-terminated.

        Sorted by metric name, and by label values within a metric, so two
        scrapes of an unchanged process are byte-identical. Registration order
        must not leak into the output, or a metric added later would appear at
        the end and every existing diff would move.
        """
        chunks = [metric.render() for metric in self.metrics()]
        if not chunks:
            return ""
        return "\n".join("\n".join(chunk) for chunk in chunks) + "\n"

    def clear(self) -> None:
        with self._lock:
            self._metrics.clear()


# --------------------------------------------------------------------------- #
# The application's own metrics
# --------------------------------------------------------------------------- #
#: The registry the ``/metrics`` endpoint reads.
default_registry = Registry()

# The application's metrics are module-level singletons rather than rebuilt on
# every registration. A caller holds a reference to one of these, and a rebuilt
# object would silently stop receiving the records it was asked to keep - a
# metric that stops counting without erroring.
HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests handled, by method, route template and status code.",
    ["method", "path", "status"],
)
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds, by method and route template.",
    ["method", "path"],
)
LLM_REQUESTS = Counter(
    "llm_requests_total",
    "LLM attribution requests by model and outcome.",
    ["model", "outcome"],
)
LLM_TOKENS = Counter(
    "llm_tokens_total",
    "Tokens billed to the LLM provider, by model and direction.",
    ["model", "direction"],
)
WEIGHT_ANALYSIS_DURATION = Histogram(
    "weight_analysis_duration_seconds",
    "Weight attribution run duration in seconds.",
)
ETL_RUNS = Counter(
    "etl_runs_total",
    "Market-data ETL attempts by source and outcome.",
    ["source", "outcome"],
)
EXTERNAL_CALL_RETRIES = Counter(
    "external_call_retries_total",
    "Retries of an outbound call by target and reason.",
    ["target", "reason"],
)
MARKET_DATA_STALENESS = Gauge(
    "market_data_staleness_hours",
    "Age in hours of the newest market reading, per component.",
    ["component"],
)
RATE_LIMIT_REJECTIONS = Counter(
    "rate_limit_rejections_total",
    "Requests rejected by the rate limiter, by client key.",
    ["client"],
)
EXPORT_JOBS = Counter(
    "export_jobs_total",
    "Export jobs by format and outcome.",
    ["format", "outcome"],
)

#: Every metric the application records into by name.
DEFAULT_METRICS: Dict[str, _Metric] = {
    metric.name: metric
    for metric in (
        HTTP_REQUESTS,
        HTTP_REQUEST_DURATION,
        LLM_REQUESTS,
        LLM_TOKENS,
        WEIGHT_ANALYSIS_DURATION,
        ETL_RUNS,
        EXTERNAL_CALL_RETRIES,
        MARKET_DATA_STALENESS,
        RATE_LIMIT_REJECTIONS,
        EXPORT_JOBS,
    )
}


def register_default_metrics(registry: Registry = None) -> Dict[str, _Metric]:
    """
    (Re)register the application's metrics into a registry.

    Called at import, and again by anything that empties the registry - a config
    reload, or the test suite's reset. Registers the same objects every time, so
    a held reference keeps working across a re-registration.
    """
    target = default_registry if registry is None else registry
    for metric in DEFAULT_METRICS.values():
        target.register(metric)
    return dict(DEFAULT_METRICS)


register_default_metrics()

# Short aliases for the call sites.
http_requests_total = HTTP_REQUESTS
http_request_duration_seconds = HTTP_REQUEST_DURATION
llm_requests_total = LLM_REQUESTS
llm_tokens_total = LLM_TOKENS
weight_analysis_duration_seconds = WEIGHT_ANALYSIS_DURATION
etl_runs_total = ETL_RUNS
external_call_retries_total = EXTERNAL_CALL_RETRIES
market_data_staleness_hours = MARKET_DATA_STALENESS
rate_limit_rejections_total = RATE_LIMIT_REJECTIONS
export_jobs_total = EXPORT_JOBS


def get_metric(name: str) -> _Metric:
    """Look up a metric by name. Works whether or not it is registered."""
    try:
        return DEFAULT_METRICS[name]
    except KeyError:
        raise KeyError(name) from None
