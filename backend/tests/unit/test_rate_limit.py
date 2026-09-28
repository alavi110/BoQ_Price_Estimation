"""
Rate limiting and client-key derivation (T123).

The limiter is tested at two levels on purpose. The counter arithmetic is
tested directly, because that is where a fixed-window bug lives and it is
invisible from outside: a window that resets a second early lets a client issue
2x the budget while every individual request still looks allowed. The HTTP
behaviour on top is tested through a real client, because *which* requests are
exempt and *what* a 429 says are the parts a client actually depends on.
"""
import time

import pytest

from src.core.rate_limit import (
    EXEMPT_PATHS,
    RateLimiter,
    client_key,
    parse_retry_after,
)


class FakeClock:
    """A monotonic clock the test drives by hand."""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --------------------------------------------------------------------------- #
# The window arithmetic
# --------------------------------------------------------------------------- #
class TestTheWindow:
    def test_requests_up_to_the_budget_are_allowed(self):
        limiter = RateLimiter(limit=3, window_seconds=60, clock=FakeClock())

        for attempt in range(3):
            decision = limiter.check("a")
            assert decision.allowed, attempt
            assert decision.remaining == 2 - attempt

    def test_the_request_past_the_budget_is_refused(self):
        limiter = RateLimiter(limit=3, window_seconds=60, clock=FakeClock())
        for _ in range(3):
            limiter.check("a")

        decision = limiter.check("a")

        assert not decision.allowed
        assert decision.remaining == 0

    def test_a_refused_request_does_not_keep_extending_the_window(self):
        """
        A sliding penalty. If a rejected request reset the countdown, a client
        that kept hammering would stay locked out forever - and a client that
        backed off for exactly ``Retry-After`` would still be refused.
        """
        clock = FakeClock()
        limiter = RateLimiter(limit=2, window_seconds=60, clock=clock)
        limiter.check("a")
        limiter.check("a")

        clock.advance(30)
        assert not limiter.check("a").allowed
        assert not limiter.check("a").allowed

        clock.advance(30)
        assert limiter.check("a").allowed

    def test_the_budget_returns_when_the_window_elapses(self):
        clock = FakeClock()
        limiter = RateLimiter(limit=2, window_seconds=60, clock=clock)
        limiter.check("a")
        limiter.check("a")
        assert not limiter.check("a").allowed

        clock.advance(59.9)
        assert not limiter.check("a").allowed

        clock.advance(0.1)
        assert limiter.check("a").allowed

    def test_clients_are_counted_separately(self):
        limiter = RateLimiter(limit=1, window_seconds=60, clock=FakeClock())
        assert limiter.check("a").allowed
        assert not limiter.check("a").allowed
        assert limiter.check("b").allowed

    def test_retry_after_says_when_the_budget_returns(self):
        """
        A 429 with no ``Retry-After`` is a 429 every client handles differently,
        and the common handling is to retry immediately - which is how a
        throttled client turns into a load generator.
        """
        clock = FakeClock()
        limiter = RateLimiter(limit=1, window_seconds=60, clock=clock)
        limiter.check("a")
        clock.advance(20)

        decision = limiter.check("a")

        assert decision.retry_after == 40
        assert decision.retry_after >= 1

    def test_retry_after_is_never_zero(self):
        """
        ``Retry-After: 0`` invites an immediate retry, and a client that retries
        immediately is exactly the load the limiter is meant to shed. Rounded up
        to at least one second, so the instruction can never be "come back
        before the window has turned over".
        """
        clock = FakeClock()
        limiter = RateLimiter(limit=1, window_seconds=60, clock=clock)
        limiter.check("a")
        clock.advance(59.999)

        decision = limiter.check("a")

        assert not decision.allowed
        assert decision.retry_after == 1

    def test_peek_does_not_consume_budget(self):
        """
        A dashboard that asks "how much is left" must not spend any. Otherwise
        reading the limit changes the limit.
        """
        limiter = RateLimiter(limit=3, window_seconds=60, clock=FakeClock())
        limiter.check("a")

        assert limiter.peek("a") == 2
        assert limiter.peek("a") == 2
        assert limiter.check("a").remaining == 1

    def test_peek_on_an_unknown_client_reports_the_whole_budget(self):
        limiter = RateLimiter(limit=5, window_seconds=60, clock=FakeClock())
        assert limiter.peek("nobody") == 5

    def test_reset_clears_every_client(self):
        limiter = RateLimiter(limit=1, window_seconds=60, clock=FakeClock())
        limiter.check("a")
        limiter.check("b")

        limiter.reset()

        assert limiter.peek("a") == 1
        assert limiter.peek("b") == 1


class TestTheStoreCannotGrowForever:
    def test_expired_entries_are_pruned(self):
        """
        A long-running process with many short-lived clients would otherwise
        accumulate one entry per client for the process's lifetime - a slow
        memory leak that only shows up in the one deployment nobody watches.
        """
        clock = FakeClock()
        limiter = RateLimiter(
            limit=10, window_seconds=1, clock=clock, prune_after=10
        )
        for n in range(50):
            limiter.check(f"client-{n}")
            # Each client leaves the table before the next arrives.
            clock.advance(1.5)

        assert len(limiter._entries) < 50

    def test_a_live_client_is_not_pruned_away(self):
        """
        The failure the pruning is most likely to introduce. If pruning dropped
        an entry that was still inside its window, the client would get a fresh
        budget and could exceed the limit indefinitely - while every individual
        request still looked allowed.
        """
        clock = FakeClock()
        limiter = RateLimiter(
            limit=2, window_seconds=3600, clock=clock, prune_after=2
        )
        limiter.check("busy")
        for n in range(20):
            limiter.check(f"churn-{n}")
            clock.advance(0.01)

        # One left in its budget, then the budget is spent and stays spent.
        assert limiter.check("busy").allowed
        assert not limiter.check("busy").allowed
        assert not limiter.check("busy").allowed

    def test_a_non_positive_limit_disables_the_limiter(self):
        """
        Turning the limiter off has to be expressible in configuration, and it
        has to mean "off" rather than "reject everything" or "divide by zero".
        """
        limiter = RateLimiter(limit=0, window_seconds=60, clock=FakeClock())

        for _ in range(100):
            assert limiter.check("a").allowed


# --------------------------------------------------------------------------- #
# Who is being limited
# --------------------------------------------------------------------------- #
class FakeRequest:
    """Just enough of a Starlette request for the key derivation."""

    def __init__(self, client=None, headers=None, user_id=None):
        self.client = client
        self.headers = {
            key.lower(): value for key, value in (headers or {}).items()
        }
        self.state = type("State", (), {"user_id": user_id})()


class TestClientKey:
    def test_the_authenticated_user_wins_over_the_address(self):
        """
        Otherwise a whole office behind one NAT shares a single budget, and one
        user's export run throttles everyone else in the building.

        ``state.user_id`` is set by :class:`RequestContextMiddleware` from the
        bearer token's subject, before the limiter runs - the auth dependency
        itself runs later, inside the route.
        """
        request = FakeRequest(client=("10.0.0.7", 1234), user_id="u-42")
        assert client_key(request) == "user:u-42"

    def test_an_unauthenticated_request_is_keyed_by_address(self):
        request = FakeRequest(client=("10.0.0.7", 1234))
        assert client_key(request) == "ip:10.0.0.7"

    def test_the_port_is_not_part_of_the_key(self):
        """
        A client's source port changes on every new connection, so including it
        would give every request its own budget and limit nothing at all.
        """
        first = FakeRequest(client=("10.0.0.7", 1000))
        second = FakeRequest(client=("10.0.0.7", 6000))
        assert client_key(first) == client_key(second)

    def test_a_forwarded_address_is_ignored_by_default(self):
        """
        ``X-Forwarded-For`` is set by the client. Trusting it without a proxy in
        front means the limiter is defeated by rotating the header - the one
        mistake that makes a rate limiter worse than none, because it looks like
        it is working.
        """
        request = FakeRequest(
            client=("10.0.0.7", 1), headers={"x-forwarded-for": "1.2.3.4"}
        )
        assert client_key(request) == "ip:10.0.0.7"

    def test_a_forwarded_address_is_used_when_a_proxy_is_trusted(self):
        request = FakeRequest(
            client=("10.0.0.7", 1), headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8"}
        )
        assert client_key(request, trust_proxy=True) == "ip:1.2.3.4"

    def test_the_leftmost_forwarded_address_wins(self):
        """
        The leftmost entry is the original client; each proxy appends. Taking
        the rightmost - the naive split-and-last - would key every client on
        the load balancer's own address and make the whole system one bucket.
        """
        request = FakeRequest(
            client=("10.0.0.7", 1),
            headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8, 9.9.9.9"},
        )
        assert client_key(request, trust_proxy=True) == "ip:1.2.3.4"

    def test_a_forged_forwarded_chain_does_not_confuse_the_trusted_case(self):
        request = FakeRequest(
            client=("10.0.0.7", 1), headers={"x-forwarded-for": "not-an-ip"}
        )
        assert client_key(request, trust_proxy=True) == "ip:10.0.0.7"

    def test_a_request_with_no_peer_falls_back_to_a_shared_key(self):
        """
        A unix socket or a test transport has no ``client``. Keying that on
        ``None`` would be a single global bucket shared by every such caller.
        """
        assert client_key(FakeRequest(client=None)) == "unknown"


# --------------------------------------------------------------------------- #
# Retry-After
# --------------------------------------------------------------------------- #
class TestParseRetryAfter:
    def test_numeric_seconds_are_read(self):
        assert parse_retry_after("30") == 30.0

    def test_a_non_numeric_value_is_ignored(self):
        """
        The header is advisory. A provider that sends a date, or a proxy that
        rewrites it, must not turn a retry policy into an exception.
        """
        assert parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
        assert parse_retry_after("") is None
        assert parse_retry_after(None) is None

    def test_a_negative_value_is_ignored(self):
        assert parse_retry_after("-5") is None


# --------------------------------------------------------------------------- #
# Exemptions
# --------------------------------------------------------------------------- #
class TestExemptions:
    def test_the_probe_and_scrape_endpoints_are_exempt(self):
        """
        A rate-limited ``/health`` is a health check that flaps, which pages
        someone. A rate-limited ``/metrics`` is a gap in the series, which hides
        the very incident that would have explained it.
        """
        for path in ("/health", "/metrics", "/openapi.json"):
            assert path in EXEMPT_PATHS, path

    def test_the_docs_are_exempt_in_development(self):
        assert "/docs" in EXEMPT_PATHS

    def test_a_prefix_is_not_enough(self):
        """
        ``/healthz-fake`` and ``/metrics-debug`` must not inherit the exemption.
        A prefix match on a path is how an attacker gets a free pass on the
        expensive endpoints by naming one after a cheap one.
        """
        from src.core.rate_limit import is_exempt

        assert not is_exempt("/healthz-fake")
        assert not is_exempt("/metrics-debug")
        assert not is_exempt("/healthy")
        assert is_exempt("/health")

    def test_the_root_is_not_exempt(self):
        from src.core.rate_limit import is_exempt

        assert not is_exempt("/")


def test_a_preflight_is_never_throttled():
    """
    CORS preflights carry no credentials and no body. Throttling them breaks
    the browser's preflight cache and makes an ordinary page load fail with a
    CORS error, which is a far worse failure than the one being prevented.
    """
    from src.core.rate_limit import is_exempt

    assert is_exempt("/prices/recalculate", method="OPTIONS")


def test_the_default_settings_are_sane():
    """
    Guard against a configuration change that turns the limiter into a denial
    of service: a zero window would expire between every request.
    """
    from src.core.config import settings

    assert settings.RATE_LIMIT_REQUESTS > 0
    assert settings.RATE_LIMIT_WINDOW_SECONDS > 0


def test_time_is_monotonic_in_the_test_limiter():
    """
    The limiter's clock must be monotonic, not wall-clock. An NTP step backwards
    mid-window would otherwise reset every client's budget at once.
    """
    limiter = RateLimiter(limit=1, window_seconds=60, clock=time.monotonic)
    assert limiter.check("a").allowed
    assert not limiter.check("a").allowed
