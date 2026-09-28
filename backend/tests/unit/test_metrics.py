"""
Prometheus metric primitives and the exposition format (T118).

`prometheus_client` is declared in ``requirements.txt`` but is not installed in
this environment, and the exposition format it renders is a small, fully
specified text format. So the primitives are implemented here directly rather
than leaving an import that raises at module load. The two things that make this
a faithful implementation rather than a convenient one are covered below: the
output has to parse as the real wire format, and the monotonicity rules have to
be enforced, because both are what a scraper and an alerting rule rely on.
"""
import math

import pytest

from src.core.metrics import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    Registry,
    default_registry,
    register_default_metrics,
)


def render(metric) -> str:
    """One metric's block, without a registry."""
    return "\n".join(metric.render()) + "\n"


@pytest.fixture(autouse=True)
def pristine_registry():
    """
    Every test starts from an empty registry with only the built-ins.

    Without this, a counter a previous test incremented would still carry that
    value, and an assertion of the form ``value >= 1`` would pass for the wrong
    reason once enough tests shared a series.
    """
    for metric in list(default_registry.metrics()):
        metric.reset()
    yield
    for metric in list(default_registry.metrics()):
        metric.reset()
    default_registry.clear()
    register_default_metrics()


# --------------------------------------------------------------------------- #
# Parsing the output the way a scraper would
# --------------------------------------------------------------------------- #
def parse_exposition(text: str):
    """
    Parse the text exposition format into ``(name, labels, value)`` samples.

    Deliberately written against the published grammar rather than against
    ``render()``'s own output, so that a change in spacing or ordering is caught
    here instead of being assumed harmless by both sides at once.
    """
    samples = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        head, _, raw_value = line.rpartition(" ")
        assert raw_value, f"line has no value: {line!r}"
        if head.endswith("}"):
            name, _, label_text = head.partition("{")
            assert label_text.endswith("}"), line
            label_text = label_text[:-1]
            labels = {}
            for pair in split_labels(label_text):
                key, _, value = pair.partition("=")
                labels[key] = unescape(value.strip()[1:-1])
        else:
            name, labels = head, {}
        samples.append((name, labels, float(raw_value)))
    return samples


def split_labels(text: str):
    """Split ``a="1",b="2"`` on commas that are not inside a quoted value."""
    parts, current, in_quotes, escaped = [], "", False, False
    for char in text:
        if escaped:
            current += char
            escaped = False
        elif char == "\\" and in_quotes:
            current += char
            escaped = True
        elif char == '"':
            in_quotes = not in_quotes
            current += char
        elif char == "," and not in_quotes:
            parts.append(current)
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current)
    return parts


def unescape(value: str) -> str:
    return (
        value.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
    )


# --------------------------------------------------------------------------- #
# Counters
# --------------------------------------------------------------------------- #
class TestCounter:
    def test_starts_at_zero_and_only_goes_up(self):
        counter = Counter("test_requests_total", "Requests served")

        assert counter.labels().value == 0.0
        counter.labels().inc()
        counter.labels().inc(4)
        assert counter.labels().value == 5.0

    def test_a_counter_refuses_to_decrease(self):
        """
        The defining property. A scraper computing ``rate()`` over a counter that
        went down reports a negative rate, and an alerting rule written as
        ``rate(...) > 0`` then never fires again.
        """
        counter = Counter("test_requests_total", "Requests served")
        counter.labels().inc(10)

        with pytest.raises(ValueError, match="monotonic"):
            counter.labels().set(5)

    def test_a_counter_refuses_a_negative_increment(self):
        """
        Refused at the increment rather than applied. ``inc(-1)`` is how a
        "this request didn't count" decision gets written by mistake, and
        letting it through would make the counter report fewer requests than
        were served.
        """
        counter = Counter("test_requests_total", "Requests served")
        with pytest.raises(ValueError, match="negative"):
            counter.labels().inc(-1)

    def test_a_counter_accepts_a_monotonic_set(self):
        """
        Repairing after a process-level recount is legitimate, so ``set`` is not
        simply banned - it is guarded.
        """
        counter = Counter("test_requests_total", "Requests served")
        counter.labels().set(100)
        assert counter.labels().value == 100.0

    def test_series_are_kept_apart_by_labels(self):
        counter = Counter("test_requests_total", "Requests served", ["method"])
        counter.labels(method="GET").inc(3)
        counter.labels(method="POST").inc()

        samples = parse_exposition(render(counter))
        by_method = {labels["method"]: value for _, labels, value in samples}

        assert by_method == {"GET": 3.0, "POST": 1.0}

    def test_an_unlabelled_metric_will_not_take_labels(self):
        counter = Counter("test_requests_total", "Requests served")
        with pytest.raises(ValueError, match="no labels"):
            counter.labels(method="GET")

    def test_an_unknown_label_name_is_rejected(self):
        """
        A typo would otherwise create a *second* series with one label instead of
        raising, and the two would sit side by side in the output with the same
        metric name.
        """
        counter = Counter("test_requests_total", "Requests served", ["method"])
        with pytest.raises(ValueError, match="methd"):
            counter.labels(methd="GET")

    def test_a_label_may_not_be_left_empty(self):
        """
        A missing label silently changes the series cardinality rather than
        failing, which is how ``.labels(path=route)`` starts producing one series
        per path that nobody intended to track.
        """
        counter = Counter("test_requests_total", "Requests served", ["path"])
        with pytest.raises(ValueError, match="missing label"):
            counter.labels()


# --------------------------------------------------------------------------- #
# Gauges
# --------------------------------------------------------------------------- #
class TestGauge:
    def test_can_move_in_both_directions(self):
        gauge = Gauge("test_inflight", "Requests in flight")

        gauge.set(5)
        gauge.dec(2)
        gauge.inc(1)

        assert gauge.labels().value == 4.0

    def test_a_gauge_may_be_negative(self):
        """
        Unlike a counter. ``queue_depth -1`` and ``temperature_c`` are both
        ordinary values, and refusing them would mean the metric cannot be used
        for the thing it exists to measure.
        """
        gauge = Gauge("test_depth", "Queue depth")
        gauge.set(-3)
        assert gauge.labels().value == -3.0

    def test_set_replaces_rather_than_accumulates(self):
        gauge = Gauge("test_inflight", "Requests in flight")
        gauge.set(4)
        gauge.set(9)
        assert gauge.labels().value == 9.0


# --------------------------------------------------------------------------- #
# Histograms
# --------------------------------------------------------------------------- #
class TestHistogram:
    def test_cumulative_buckets_cover_every_observation(self):
        histogram = Histogram(
            "test_seconds", "Elapsed", buckets=[0.1, 0.5, 1.0]
        )
        for value in (0.05, 0.3, 0.7, 5.0):
            histogram.labels().observe(value)

        buckets = {
            labels["le"]: value
            for name, labels, value in parse_exposition(render(histogram))
            if name.endswith("_bucket")
        }

        # 0.05 <= 0.1; 0.3 lands in the 0.5 bucket; 0.7 in the 1.0 bucket; 5.0
        # in none of them, so only +Inf counts it.
        assert buckets["0.1"] == 1.0
        assert buckets["0.5"] == 2.0
        assert buckets["1.0"] == 3.0
        # The last bucket is +Inf and must equal the total observation count.
        assert buckets["+Inf"] == 4.0

    def test_sum_and_count_are_emitted(self):
        histogram = Histogram("test_seconds", "Elapsed")
        histogram.labels().observe(2.0)
        histogram.labels().observe(3.0)

        samples = {name: value for name, _, value in parse_exposition(render(histogram))}

        assert samples["test_seconds_count"] == 2.0
        assert samples["test_seconds_sum"] == 5.0

    def test_the_inf_bucket_agrees_with_the_count(self):
        """
        The identity a scraper relies on: every observation is in exactly one
        bucket, so the cumulative ``+Inf`` bucket equals ``_count``. Counting an
        observation into every bucket it fits still leaves the individual buckets
        plausible, so only this cross-check catches it.
        """
        histogram = Histogram("test_seconds", "Elapsed", buckets=[1.0, 10.0])
        for value in (0.5, 1.0, 5.0, 10.0, 11.0, 1000.0):
            histogram.labels().observe(value)

        samples = parse_exposition(render(histogram))
        inf = next(
            value for name, labels, value in samples
            if name == "test_seconds_bucket" and labels["le"] == "+Inf"
        )
        count = next(
            value for name, _, value in samples if name == "test_seconds_count"
        )

        assert inf == count == 6.0

    def test_buckets_must_be_ascending(self):
        """
        A non-ascending bucket list makes ``histogram_quantile`` return nonsense
        rather than fail, so the ordering is enforced at construction.
        """
        with pytest.raises(ValueError, match="ascending"):
            Histogram("test_seconds", "Elapsed", buckets=[1.0, 0.5])

    def test_duplicate_bucket_bounds_are_rejected(self):
        with pytest.raises(ValueError, match="duplicate"):
            Histogram("test_seconds", "Elapsed", buckets=[1.0, 1.0])

    def test_a_non_finite_bucket_bound_is_rejected(self):
        """
        ``+Inf`` is appended by the implementation. Allowing a caller to supply
        it as well would put it in the middle of the list.
        """
        with pytest.raises(ValueError, match="finite"):
            Histogram("test_seconds", "Elapsed", buckets=[1.0, math.inf])

    def test_a_negative_observation_is_rejected(self):
        """
        Latency histograms have no meaning below zero, and a negative
        observation would corrupt the running ``_sum`` for the whole process
        lifetime rather than just for that scrape.
        """
        histogram = Histogram("test_seconds", "Elapsed")
        with pytest.raises(ValueError, match="negative"):
            histogram.labels().observe(-0.1)


# --------------------------------------------------------------------------- #
# Naming
# --------------------------------------------------------------------------- #
class TestNaming:
    @pytest.mark.parametrize("name", ["", "1leading_digit", "has-dash", "has space"])
    def test_an_illegal_metric_name_is_rejected(self, name):
        with pytest.raises(ValueError, match="metric name"):
            Counter(name, "Bad")

    def test_an_illegal_label_name_is_rejected(self):
        with pytest.raises(ValueError, match="label name"):
            Counter("test_ok_total", "Ok", ["bad-label"])

    def test_a_colliding_name_is_rejected(self):
        registry = Registry()
        registry.register(Counter("test_dup_total", "First"))

        with pytest.raises(ValueError, match="already registered"):
            registry.register(Gauge("test_dup_total", "Second"))

    def test_the_metric_type_is_declared_once_per_family(self):
        """
        A duplicate ``# TYPE`` line makes a scraper reject the whole scrape, so
        one metric renders one ``# HELP`` and one ``# TYPE`` however many series
        it has.
        """
        counter = Counter("test_requests_total", "Requests", ["method"])
        counter.labels(method="GET").inc()
        counter.labels(method="POST").inc()

        text = render(counter)

        assert text.count("# HELP test_requests_total") == 1
        assert text.count("# TYPE test_requests_total counter") == 1


# --------------------------------------------------------------------------- #
# Label escaping
# --------------------------------------------------------------------------- #
class TestEscaping:
    def test_quotes_newlines_and_backslashes_survive_a_round_trip(self):
        """
        A route containing a query string, or a label value carrying a path, is
        the ordinary case, not an exotic one. An unescaped quote would split the
        label pair and corrupt every series after it in the scrape.
        """
        counter = Counter("test_route_total", "Routes", ["path"])
        nasty = 'a"b\\c\nd,e=f'
        counter.labels(path=nasty).inc()

        samples = parse_exposition(render(counter))

        assert samples == [("test_route_total", {"path": nasty}, 1.0)]

    def test_persian_label_values_survive(self):
        """
        The values this service actually records: component codes with Persian
        names attached. The exposition is UTF-8, and the declared content type
        says so.
        """
        counter = Counter("test_component_total", "Components", ["name_fa"])
        counter.labels(name_fa="مصارف عمومی").inc()

        assert "مصارف عمومی" in render(counter)
        assert "charset=utf-8" in CONTENT_TYPE_LATEST


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #
class TestRegistry:
    def test_renders_every_registered_metric(self):
        registry = Registry()
        registry.register(Counter("test_a_total", "A"))
        registry.register(Gauge("test_b", "B"))
        registry.register(Histogram("test_c_seconds", "C"))

        text = registry.render()

        assert "# TYPE test_a_total counter" in text
        assert "# TYPE test_b gauge" in text
        assert "# TYPE test_c_seconds histogram" in text

    def test_an_empty_registry_renders_nothing(self):
        assert Registry().render() == ""

    def test_output_is_ordered_so_two_renders_compare_equal(self):
        """
        Determinism is what makes an output change visible in review. Without it
        every scrape of an unchanged process looks like a diff.

        Sorted by metric name, and by label values within a metric - so a set
        rendered twice is byte-identical however the series were created.
        """
        registry = Registry()
        registry.register(Counter("test_z_total", "Z", ["m"]))
        registry.register(Counter("test_a_total", "A", ["m"]))
        registry.register(Gauge("test_a_total_extra", "A2"))

        first = registry.render()

        assert first == registry.render()

        emitted = [
            line for line in first.splitlines()
            if line and not line.startswith("#")
        ]
        names = [line.split("{")[0].split(" ")[0] for line in emitted]
        assert names == sorted(names)

    def test_scrape_is_stable_across_calls(self):
        registry = Registry()
        counter = Counter("test_s_total", "S", ["m"])
        counter.labels(m="GET").inc(2)
        registry.register(counter)

        assert registry.render() == registry.render()

    def test_a_metric_created_after_another_still_sorts_by_name(self):
        """
        Registration order must not leak into the output, or a metric added
        later would appear at the end and every existing diff would move.
        """
        registry = Registry()
        registry.register(Counter("test_mmm_total", "M"))
        registry.register(Counter("test_aaa_total", "A"))

        body = [
            line for line in registry.render().splitlines()
            if line and not line.startswith("#")
        ]
        assert body == ["test_aaa_total 0.0", "test_mmm_total 0.0"]


# --------------------------------------------------------------------------- #
# The application's own metrics
# --------------------------------------------------------------------------- #
class TestApplicationMetrics:
    def test_the_http_metrics_are_registered(self):
        from src.core import metrics as m

        assert m.http_requests_total is not None
        assert m.http_request_duration_seconds is not None
        assert m.llm_requests_total is not None
        assert m.etl_runs_total is not None
        assert m.weight_analysis_duration_seconds is not None

    def test_the_default_registry_renders_the_http_metrics(self):
        from src.core import metrics as m

        m.http_requests_total.labels(method="GET", path="/health", status="200").inc()

        text = default_registry.render()

        assert 'http_requests_total{method="GET",path="/health",status="200"}' in text
        assert CONTENT_TYPE_LATEST.endswith("charset=utf-8")

    def test_a_histogram_renders_le_last_so_the_format_parses(self):
        """
        The ``le`` label is synthetic, and the format requires it after the
        family's own labels. Rendered in the wrong position, a scraper reads
        ``method="1.0"`` and a latency query silently returns nothing.
        """
        from src.core import metrics as m

        m.http_request_duration_seconds.labels(
            method="GET", path="/health"
        ).observe(0.3)

        buckets = [
            line for line in default_registry.render().splitlines()
            if line.startswith("http_request_duration_seconds_bucket")
        ]

        assert buckets
        for line in buckets:
            assert 'method="GET"' in line
            assert "le=" in line
            assert line.index("le=") > line.index("path=")

    def test_etl_outcomes_are_recordable_per_source(self):
        from src.core import metrics as m

        m.etl_runs_total.labels(source="ime_copper", outcome="failed").inc()
        m.etl_runs_total.labels(source="steel", outcome="success").inc()

        text = default_registry.render()

        assert 'etl_runs_total{outcome="failed",source="ime_copper"}' in text
        assert 'etl_runs_total{outcome="success",source="steel"}' in text
