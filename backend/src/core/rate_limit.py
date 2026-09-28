"""
In-process rate limiting for the API (T123).

A fixed-window counter, keyed per client. Chosen over a sliding window because
the arithmetic is small enough to reason about and a refusal has to be
explainable: "you made 100 requests in the 60 seconds since 14:32:00" is a
sentence an operator can say out loud.

Two things the implementation is careful about, both of which are the ways a
rate limiter quietly stops working:

* the key is derived from the authenticated user when there is one, and from the
  peer address otherwise - and ``X-Forwarded-For`` is only consulted when a
  proxy is actually in front, because that header is set by the client and
  trusting it unconditionally is a bypass;
* a rejected request does not reset the window, so hammering a closed door does
  not extend the lockout indefinitely.
"""
import math
import threading
import time
from typing import Callable, Dict, NamedTuple, Optional, Tuple

from src.core.logging import get_logger

logger = get_logger(__name__)

#: Paths served without counting against the budget.
#:
#: A throttled health check is a health check that flaps, which pages somebody.
#: A throttled metrics scrape is a gap in the series, which hides the very
#: incident the metrics would have explained.
EXEMPT_PATHS = frozenset(
    {
        "/health",
        "/healthz",
        "/ready",
        "/metrics",
        "/openapi.json",
        "/docs",
        "/redoc",
    }
)

#: Above this many tracked clients, entries whose window has elapsed are dropped.
DEFAULT_PRUNE_THRESHOLD = 10_000


def is_exempt(path: str, method: str = "GET") -> bool:
    """
    Whether a request skips the limiter.

    An exact match, never a prefix: ``/metrics-debug`` and ``/healthz-fake`` are
    not the endpoints they are named after, and a prefix match is how an
    expensive endpoint gets a free pass by being named after a cheap one.

    ``OPTIONS`` is always exempt. A preflight carries no credentials and no
    body, so throttling one breaks the browser's preflight cache and turns an
    ordinary page load into a CORS failure - a worse outcome than the one being
    prevented.
    """
    if method.upper() == "OPTIONS":
        return True
    return path in EXEMPT_PATHS


def client_key(request, trust_proxy: bool = False) -> str:
    """
    The bucket a request counts against.

    Prefers the authenticated user, so a whole office behind one NAT does not
    share a single budget and one user's export run does not throttle everyone
    else in the building. Falls back to the peer address.

    ``X-Forwarded-For`` is client-controlled, so it is only read when
    ``trust_proxy`` says a proxy is in front. The *leftmost* entry is the
    original client: each proxy appends, and taking the last would key every
    caller on the load balancer's own address.
    """
    user_id = getattr(getattr(request, "state", None), "user_id", None)
    if user_id:
        return f"user:{user_id}"

    peer = getattr(request, "client", None)
    host = peer[0] if peer else None

    if trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            leftmost = forwarded.split(",")[0].strip()
            # A malformed entry is discarded rather than used as a key: two
            # distinct clients sending the same garbage would otherwise share a
            # budget, and an attacker could collide deliberately.
            if _is_plausible_address(leftmost):
                host = leftmost

    if not host:
        # A unix socket or a synthetic transport has no peer. Keying on ``None``
        # would make every such caller share one global bucket.
        return "unknown"
    return f"ip:{host}"


def _is_plausible_address(value: str) -> bool:
    """A conservative IPv4/IPv6 check, used to reject forged header values."""
    if not value or len(value) > 45:
        return False
    if ":" in value:
        return all(part and all(c in "0123456789abcdefABCDEF" for c in part)
                   for part in value.split(":") if part) and not value.startswith(":")
    parts = value.split(".")
    return len(parts) == 4 and all(
        part.isdigit() and 0 <= int(part) <= 255 for part in parts
    )


def parse_retry_after(value: Optional[str]) -> Optional[float]:
    """
    Read a ``Retry-After`` header, in seconds.

    Advisory, so anything unparseable is discarded rather than raised on. A
    provider that sends an HTTP-date, or a proxy that rewrites the header, must
    not turn a retry policy into an exception on the error path.
    """
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


class Decision(NamedTuple):
    """The outcome of one limiter consultation."""

    allowed: bool
    limit: int
    remaining: int
    #: Seconds until the budget returns. Only meaningful when refused.
    retry_after: int
    #: Seconds until the current window rolls over. Sent as a hint either way.
    reset_after: int


class RateLimiter:
    """
    A fixed-window counter per client key.

    The clock is injected so the window arithmetic can be tested at its
    boundaries - a window that resets a fraction of a second early lets a client
    issue twice its budget while every individual request still looks allowed.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
        prune_after: int = DEFAULT_PRUNE_THRESHOLD,
    ):
        self.limit = int(limit)
        self.window_seconds = int(window_seconds)
        self._clock = clock
        self._prune_after = max(1, int(prune_after))
        self._lock = threading.Lock()
        # key -> (window started at, requests spent)
        self._entries: Dict[str, Tuple[float, int]] = {}

    @property
    def enabled(self) -> bool:
        """A non-positive limit means "off", not "refuse everything"."""
        return self.limit > 0 and self.window_seconds > 0

    def check(self, key: str) -> Decision:
        """
        Spend one unit of ``key``'s budget.

        A refused request does not extend the window. If it did, a client that
        kept hammering would stay locked out forever, and a well-behaved client
        that backed off for exactly ``Retry-After`` would come back one request
        early and be refused again.
        """
        if not self.enabled:
            return Decision(True, self.limit, self.limit, 0, self.window_seconds)

        now = self._clock()
        with self._lock:
            self._prune(now)
            started, spent = self._entries.get(key, (now, 0))
            elapsed = now - started
            if elapsed >= self.window_seconds or elapsed < 0:
                # A backwards clock step must not hand every client a fresh
                # budget, so an implausible elapsed time is treated as a rollover.
                started, spent = now, 0
                elapsed = 0

            reset_after = max(0, self.window_seconds - elapsed)
            if spent >= self.limit:
                return Decision(
                    False,
                    self.limit,
                    0,
                    # Never zero: a client told to retry immediately becomes the
                    # load the limiter is meant to shed.
                    max(1, math.ceil(reset_after)),
                    math.ceil(reset_after),
                )

            spent += 1
            self._entries[key] = (started, spent)
            return Decision(
                True,
                self.limit,
                self.limit - spent,
                0,
                math.ceil(reset_after),
            )

    def peek(self, key: str) -> int:
        """
        Budget remaining, without spending any.

        A status endpoint that reports "how much is left" must not change the
        answer; otherwise reading the limit spends it.
        """
        if not self.enabled:
            return self.limit
        now = self._clock()
        with self._lock:
            started, spent = self._entries.get(key, (None, 0))
            if started is None or now - started >= self.window_seconds:
                return self.limit
            return max(0, self.limit - spent)

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()

    def tracked_clients(self) -> int:
        with self._lock:
            return len(self._entries)

    def _prune(self, now: float) -> None:
        """
        Drop entries whose window has fully elapsed, once the table is large.

        A long-running process with many short-lived clients would otherwise
        accumulate one entry per client for the process's lifetime. Only fully
        elapsed windows are dropped - an entry still inside its window is
        dropped never, because doing so would hand that client a fresh budget.
        """
        if len(self._entries) <= self._prune_after:
            return
        stale = [
            key
            for key, (started, _) in self._entries.items()
            if now - started >= self.window_seconds
        ]
        for key in stale:
            del self._entries[key]
