"""
Operational endpoints (T126).

``/health``, ``/ready`` and ``/metrics`` are the three things an operator reads
when the service is misbehaving, and they are the three things nobody is
watching while the service is healthy. So each is tested against the state it
would rather not report: an unreachable database, stale market data, a missing
provider key, metrics switched off.

The distinction the tests keep pressing on is liveness versus readiness. A
liveness probe that touches a dependency converts a slow database into a
restart of every replica, which is not a recovery. That is the single most
expensive mistake available on this surface, so it is asserted directly rather
than assumed.
"""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from src.core.config import settings
from src.main import app, dependency_checks


class FakeResult:
    def __init__(self, scalar_value):
        self._scalar = scalar_value

    def scalar(self):
        return self._scalar


class FakeSession:
    """
    Enough of an ``AsyncSession`` for ``dependency_checks``.

    Records the statements it was asked to run so a test can assert that
    liveness issued none.
    """

    def __init__(self, market_max=None, database_error=None, market_error=None):
        self.market_max = market_max
        self.database_error = database_error
        self.market_error = market_error
        self.statements: list[str] = []

    async def execute(self, statement):
        rendered = str(statement)
        self.statements.append(rendered)
        if "market_indices" in rendered.lower():
            if self.market_error is not None:
                raise self.market_error
            return FakeResult(self.market_max)
        if self.database_error is not None:
            raise self.database_error
        return FakeResult(1)


@pytest.fixture
def client():
    """
    The real application, without the lifespan.

    Entered as a context manager, the startup hook would try to build a database
    engine and the fixture would fail for a reason that has nothing to do with
    what is being tested. ``raise_server_exceptions=False`` so an escaped
    exception surfaces as the 500 it would be in production rather than as a
    test error that tells us nothing about what the caller receives.
    """
    return TestClient(app, raise_server_exceptions=False)


# --------------------------------------------------------------------------- #
# /health
# --------------------------------------------------------------------------- #
class TestLiveness:
    def test_it_answers(self, client):
        response = client.get("/health")

        assert response.status_code == 200

    def test_it_reports_the_version_and_environment(self, client):
        body = client.get("/health").json()

        assert body["status"] == "healthy"
        assert body["version"] == settings.VERSION
        assert body["environment"] == settings.ENVIRONMENT

    def test_it_touches_no_dependency(self, client, monkeypatch):
        """
        The whole reason it is separate from ``/ready``.

        A liveness probe that opens a database connection fails when the
        connection pool is exhausted, and the orchestrator's response to a
        failed liveness probe is to kill the pod. A slow database would then
        restart every replica at once - the outage amplifying itself.
        """
        from src.main import _check_database

        async def explode():
            raise AssertionError("liveness must not touch the database")

        monkeypatch.setattr("src.main._check_database", explode)

        assert client.get("/health").status_code == 200

    def test_it_is_answered_while_a_dependency_is_down(self, client, monkeypatch):
        """A dependency outage must not become a restart storm."""

        async def explode(*args, **kwargs):
            raise ConnectionError("database is gone")

        monkeypatch.setattr("src.main.dependency_checks", explode)

        assert client.get("/health").status_code == 200

    def test_it_carries_the_security_headers(self, client):
        headers = client.get("/health").headers

        assert "x-content-type-options" in headers
        assert "content-security-policy" in headers

    def test_it_is_not_exempted_from_the_throttler_by_prefix(self):
        """
        Exact-match only. A prefix rule would exempt ``/healthz-fake`` too, but
        the real risk is the other direction: a scraper polling once a second
        must never be refused, because a refused liveness probe restarts the
        service.
        """
        from src.core.rate_limit import is_exempt

        assert is_exempt("/health")
        assert is_exempt("/healthz")
        assert is_exempt("/ready")
        assert is_exempt("/metrics")
        assert not is_exempt("/healthz-fake")
        assert not is_exempt("/health/deep")


# --------------------------------------------------------------------------- #
# /ready
# --------------------------------------------------------------------------- #
class TestReadiness:
    def test_it_is_ready_when_everything_is_ok(self, client, monkeypatch):
        async def checks(db=None):
            return {
                "database": {"status": "ok"},
                "market_data": {"status": "ok"},
                "openrouter": {"status": "ok"},
            }

        monkeypatch.setattr("src.main.dependency_checks", checks)

        response = client.get("/ready")

        assert response.status_code == 200
        assert response.json()["status"] == "ready"

    def test_a_failing_dependency_takes_it_out_of_rotation(self, client, monkeypatch):
        async def checks(db=None):
            return {"database": {"status": "error", "error": "refused"}}

        monkeypatch.setattr("src.main.dependency_checks", checks)

        response = client.get("/ready")

        assert response.status_code == 503
        assert response.json()["status"] == "degraded"

    def test_stale_market_data_does_not_take_it_out_of_rotation(
        self, client, monkeypatch
    ):
        """
        Stale, not broken.

        The price path falls back to the last good reading, so the service can
        still produce a correct, visibly dated answer. Refusing to serve turns a
        stale index into a total outage - the opposite of what a fallback is for.
        """
        async def checks(db=None):
            return {"market_data": {"status": "stale", "age_hours": 500}}

        monkeypatch.setattr("src.main.dependency_checks", checks)

        assert client.get("/ready").status_code == 200

    def test_degraded_does_not_take_it_out_of_rotation(self, client, monkeypatch):
        async def checks(db=None):
            return {
                "market_data": {"status": "degraded"},
                "openrouter": {"status": "degraded"},
            }

        monkeypatch.setattr("src.main.dependency_checks", checks)

        assert client.get("/ready").status_code == 200

    def test_an_unexpected_check_name_does_not_outrank_a_real_failure(
        self, client, monkeypatch
    ):
        """
        ``status == "error"`` is the blocking test, not "any status that is not
        ok". A new dependency reporting ``"unknown"`` must not silently start
        blocking traffic, and a typo'd status must not silently stop blocking.
        """
        async def checks(db=None):
            return {
                "database": {"status": "error"},
                "brand_new_dependency": {"status": "err0r"},
            }

        monkeypatch.setattr("src.main.dependency_checks", checks)

        assert client.get("/ready").status_code == 503

    def test_it_returns_json_rather_than_prose(self, client):
        response = client.get("/ready")

        assert response.headers["content-type"].startswith("application/json")


class TestDependencyChecks:
    """The function behind ``/ready``, tested directly against each verdict."""

    @pytest.mark.asyncio
    async def test_a_healthy_database_is_ok(self):
        checks = await dependency_checks(FakeSession())

        assert checks["database"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_an_unreachable_database_is_an_error_not_an_exception(self):
        """
        Never raises. A probe that throws reports nothing, which reads exactly
        like a probe that succeeded.
        """
        checks = await dependency_checks(
            FakeSession(database_error=ConnectionError("refused"))
        )

        assert checks["database"]["status"] == "error"
        assert "refused" in checks["database"]["error"]

    @pytest.mark.asyncio
    async def test_an_error_message_is_bounded(self):
        """
        The detail is echoed into a body. A driver error can carry a connection
        string with a password, and an unbounded string is also an unbounded
        response for an endpoint polled every few seconds.
        """
        checks = await dependency_checks(
            FakeSession(database_error=RuntimeError("x" * 5000))
        )

        assert len(checks["database"]["error"]) <= 200

    @pytest.mark.asyncio
    async def test_fresh_market_data_is_ok(self):
        today = dt.datetime.now(dt.timezone.utc).date()
        checks = await dependency_checks(FakeSession(market_max=today))

        assert checks["market_data"]["status"] == "ok"
        assert checks["market_data"]["age_hours"] == 0

    @pytest.mark.asyncio
    async def test_stale_market_data_is_reported_with_its_age(self):
        old = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=9)
        checks = await dependency_checks(FakeSession(market_max=old))

        assert checks["market_data"]["status"] == "stale"
        assert checks["market_data"]["age_hours"] == 216
        assert checks["market_data"]["staleness_budget_hours"] == (
            settings.MARKET_DATA_STALENESS_HOURS
        )

    @pytest.mark.asyncio
    async def test_the_staleness_boundary_is_inclusive(self):
        """
        Exactly at the budget is still fresh. One day past it is not. A test
        either side of the edge, because ``>`` versus ``>=`` is the whole
        difference and neither boundary is obvious from the code.

        The implementation truncates to whole days: age_hours = days * 24.
        So at 24h (1 day) it's not stale (24 > 24 is False).
        At 48h (2 days) it IS stale (48 > 24 is True).
        """
        budget = settings.MARKET_DATA_STALENESS_HOURS
        now = dt.datetime.now(dt.timezone.utc).date()

        # Exactly at the budget: 24h = 1 day, age_hours = 24, not stale
        on_the_line = await dependency_checks(
            FakeSession(market_max=now - dt.timedelta(hours=budget))
        )
        # One day past: 48h = 2 days, age_hours = 48, stale
        just_past = await dependency_checks(
            FakeSession(market_max=now - dt.timedelta(days=budget // 24 + 1))
        )

        assert on_the_line["market_data"]["status"] == "ok"
        assert just_past["market_data"]["status"] == "stale"

    @pytest.mark.asyncio
    async def test_no_market_data_at_all_is_degraded_not_stale(self):
        """
        ``max()`` over an empty table is ``None``, and ``None`` has no ``.days``
        - a freshness check that raises here would report the dependency as
        broken when the honest answer is "nothing has arrived yet".
        """
        checks = await dependency_checks(FakeSession(market_max=None))

        assert checks["market_data"]["status"] == "degraded"
        assert "falls back" in checks["market_data"]["reason"]

    @pytest.mark.asyncio
    async def test_a_failing_market_query_does_not_erase_the_database_verdict(self):
        checks = await dependency_checks(
            FakeSession(market_error=RuntimeError("no such column"))
        )

        assert checks["market_data"]["status"] == "error"
        assert checks["database"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_every_check_carries_when_it_was_taken(self):
        """
        Without a timestamp, a readiness body polled every five seconds is
        indistinguishable from one rendered from a stale cache, and the first
        question an operator asks - "is this live?" - cannot be answered.
        """
        checks = await dependency_checks(FakeSession())

        for name, check in checks.items():
            assert "checked_at" in check, name
            dt.datetime.fromisoformat(check["checked_at"])

    @pytest.mark.asyncio
    async def test_all_the_checks_share_one_timestamp(self):
        """
        A body whose parts were measured minutes apart describes a system that
        never existed: reachable for the database, unreachable for the provider,
        in the same instant.
        """
        checks = await dependency_checks(FakeSession())

        stamps = {check["checked_at"] for check in checks.values()}
        assert len(stamps) == 1

    @pytest.mark.asyncio
    async def test_redis_is_not_checked(self):
        """
        It backs Celery. This process serves requests without it, so including it
        would drain every replica during a queue backlog - requests that never
        touch the queue would be refused because a background job is slow.
        """
        checks = await dependency_checks(FakeSession())

        assert not any("redis" in name.lower() for name in checks)

    @pytest.mark.asyncio
    async def test_a_missing_provider_key_is_degraded_with_a_reason(self, monkeypatch):
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")

        checks = await dependency_checks(FakeSession())

        assert checks["openrouter"]["status"] == "degraded"
        assert "OPENROUTER_API_KEY" in checks["openrouter"]["reason"]

    @pytest.mark.asyncio
    async def test_a_present_provider_key_is_ok_without_calling_it(self, monkeypatch):
        """
        Presence, not reachability. A provider outage should not drain the pool -
        the fallback is keyword matching, which is the designed behaviour, and
        the request latency to check it would be the provider's, not ours.
        """
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-or-v1-x")

        checks = await dependency_checks(FakeSession())

        assert checks["openrouter"]["status"] == "ok"
        assert checks["openrouter"]["reason"] is None

    @pytest.mark.asyncio
    async def test_no_session_means_it_falls_back_to_its_own_connection(
        self, monkeypatch
    ):
        """
        ``/ready`` is not given a request-scoped session - it is polled by the
        orchestrator on a timer and may be answered by a replica mid-request.
        The check has to be able to open its own.
        """
        opened = []

        async def record():
            opened.append(True)

        monkeypatch.setattr("src.main._check_database", record)

        checks = await dependency_checks()

        assert opened == [True]
        assert checks["database"]["status"] == "ok"
        assert checks["market_data"]["status"] == "unknown"


# --------------------------------------------------------------------------- #
# /metrics
# --------------------------------------------------------------------------- #
class TestMetricsEndpoint:
    def test_it_serves_the_exposition_when_enabled(self, client, monkeypatch):
        monkeypatch.setattr(settings, "METRICS_ENABLED", True)

        response = client.get("/metrics")

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]

    def test_it_is_hidden_when_disabled(self, client, monkeypatch):
        """
        The exposition discloses route names, error rates and the shape of the
        deployment to anything that can reach the port.
        """
        monkeypatch.setattr(settings, "METRICS_ENABLED", False)

        response = client.get("/metrics")

        assert response.status_code == 404

    def test_it_is_not_in_the_public_schema(self, client):
        """
        Off the published API surface even when it is on, so the contract
        cannot come to depend on it.
        """
        paths = client.get("/openapi.json").json()["paths"]

        assert "/metrics" not in paths

    def test_it_counts_its_own_scrape(self, client, monkeypatch):
        """
        Self-observability is real and occasionally embarrassing: an endpoint
        that is not counted is an endpoint that can fail for months unnoticed.
        """
        from src.core.metrics import HTTP_REQUESTS

        monkeypatch.setattr(settings, "METRICS_ENABLED", True)
        series = HTTP_REQUESTS.labels(method="GET", path="/metrics", status="200")
        before = series.value

        client.get("/metrics")

        assert series.value == before + 1
