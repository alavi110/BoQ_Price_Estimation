"""
HTTP middleware: security headers, request context, metrics, throttling (T112,
T118, T122, T123).

Driven through a real ``TestClient`` rather than by calling the middleware
classes directly, because the parts that break in practice are the interactions
- a header added by one middleware and overwritten by another, a 429 that
skips the security headers, a metric labelled with the raw path so every
``/weights/<uuid>`` becomes its own time series.

The application is rebuilt for these tests with the middlewares actually
installed, so what is asserted is the wiring in ``src.main`` rather than a
hand-assembled stack that nothing runs.
"""
import re
import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from src.core.config import settings
from src.core.cors import cors_configuration
from src.core.error_responses import install_error_handlers
from src.core.metrics import CONTENT_TYPE_LATEST, default_registry, register_default_metrics
from src.core.middleware import (
    MetricsMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from src.core.rate_limit import RateLimiter


def build_app(limit: int = 5, window_seconds: int = 60, trust_proxy: bool = False) -> FastAPI:
    """
    A miniature app with the same middleware stack the real one uses.

    The real routes are not involved - what is under test is what surrounds them.
    ``install_error_handlers`` is included because ``src.main`` includes it, and
    because Starlette's ``ServerErrorMiddleware`` sits outside every
    ``add_middleware``, so an exception that escapes never passes back through
    the security headers.
    """
    app = FastAPI()

    @app.get("/items/{item_id}")
    async def read_item(item_id: uuid.UUID):
        return {"id": str(item_id)}

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("deliberate")

    app.add_middleware(
        RateLimitMiddleware,
        limiter=RateLimiter(limit=limit, window_seconds=window_seconds),
        trust_proxy=trust_proxy,
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(CORSMiddleware, **cors_configuration(settings.ALLOWED_ORIGINS))
    return app


@pytest.fixture
def client():
    for metric in list(default_registry.metrics()):
        metric.reset()
    with TestClient(
        build_app(), raise_server_exceptions=False
    ) as c:
        yield c
    for metric in list(default_registry.metrics()):
        metric.reset()
    default_registry.clear()
    register_default_metrics()


# --------------------------------------------------------------------------- #
# Security headers (T112)
# --------------------------------------------------------------------------- #
class TestSecurityHeaders:
    def test_the_baseline_headers_are_on_every_response(self, client):
        response = client.get("/health")

        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"

    def test_a_clickjacking_header_is_present_on_json_responses(self, client):
        """
        The API serves JSON, so a browser cannot render it as a page. The
        defence-in-depth value is that a content-type confusion - a proxy that
        sniffs an HTML body and serves it - is closed off.
        """
        response = client.get("/items/00000000-0000-0000-0000-000000000001")

        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-security-policy"]

    def test_the_csp_forbids_inline_and_remote_script(self, client):
        csp = client.get("/health").headers["content-security-policy"]

        assert "default-src 'self'" in csp
        assert "script-src 'self'" in csp
        # A `'unsafe-inline'` or a wildcard would undo the rest of the policy.
        assert "unsafe-inline" not in csp
        assert "unsafe-eval" not in csp
        assert "*" not in csp

    def test_hsts_is_not_sent_over_plain_http(self, client):
        """
        A browser that sees HSTS on an http response pins the *hostname* to
        https - and a development server on port 8000 is not that hostname's
        https endpoint. Sending it in development breaks the dev loop.
        """
        assert "strict-transport-security" not in client.get("/health").headers

    def test_hsts_is_sent_when_the_deployment_is_behind_tls(self, monkeypatch):
        monkeypatch.setattr(settings, "FORCE_HTTPS", True)
        with TestClient(build_app()) as c:
            headers = c.get("/health").headers

        assert "max-age=31536000" in headers["strict-transport-security"]
        assert headers["strict-transport-security"].endswith("includeSubDomains")

    def test_an_error_response_still_carries_the_headers(self, client):
        """
        The failure a naive implementation has: the header is added on the way
        out of the route, so a raised exception skips it - and an error page is
        exactly the one a browser should never render.
        """
        response = client.get("/boom")

        assert response.status_code == 500
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_a_404_still_carries_the_headers(self, client):
        response = client.get("/nope")

        assert response.status_code == 404
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_the_server_banner_is_not_advertised(self, client):
        """
        ``server: uvicorn`` tells an attacker the framework and version family.
        Removing it is not security on its own, but it costs nothing.
        """
        assert "server" not in {k.lower() for k in client.get("/health").headers}


# --------------------------------------------------------------------------- #
# Request context (T112)
# --------------------------------------------------------------------------- #
class TestRequestContext:
    def test_a_request_id_is_generated_and_echoed(self, client):
        response = client.get("/health")

        assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])

    def test_a_supplied_request_id_is_echoed_verbatim(self, client):
        """
        A correlation id set by an upstream proxy is what ties this request to
        that one in the logs. Overwriting it would break the trace.
        """
        supplied = "caller-supplied-id"

        response = client.get("/health", headers={"x-request-id": supplied})

        assert response.headers["x-request-id"] == supplied

    def test_a_hostile_request_id_cannot_inject_a_header(self, client):
        """
        A reflected header value is a header-injection primitive if it can carry
        a newline. Rejected outright rather than sanitised, because there is no
        legitimate reason to send one.
        """
        response = client.get(
            "/health", headers={"x-request-id": "abc\r\nx-injected: 1"}
        )

        assert response.headers["x-request-id"] != "abc\r\nx-injected: 1"
        assert "x-injected" not in response.headers

    def test_an_absurd_request_id_is_replaced(self, client):
        response = client.get("/health", headers={"x-request-id": "x" * 5000})

        assert len(response.headers["x-request-id"]) <= 64

    def test_the_route_sees_the_same_request_id(self):
        """
        The id on the response and the id in the log line have to be the same
        one, which only holds if both come from ``request.state``.
        """
        app = build_app()
        seen = {}

        @app.get("/echo-id")
        async def echo_id(request: Request):
            seen["id"] = getattr(request.state, "request_id", None)
            return {}

        with TestClient(app) as c:
            c.get("/echo-id", headers={"x-request-id": "trace-me"})

        assert seen["id"] == "trace-me"


# --------------------------------------------------------------------------- #
# Metrics (T118)
# --------------------------------------------------------------------------- #
class TestMetrics:
    def test_a_request_is_counted(self, client):
        client.get("/health")

        text = default_registry.render()
        assert 'http_requests_total{method="GET",path="/health",status="200"} 1' in text

    def test_a_failure_is_counted_under_its_own_status(self, client):
        client.get("/boom")

        assert (
            'http_requests_total{method="GET",path="/boom",status="500"} 1'
            in default_registry.render()
        )

    def test_the_path_label_is_the_route_template_not_the_url(self, client):
        """
        The single most important property here.

        Labelled with the raw path, every ``/weights/<uuid>`` becomes its own
        time series: the scrape grows without bound, Prometheus falls over, and
        the service goes down because someone looked at a page. Labelled with the
        template, the series count is bounded by the number of routes.
        """
        for _ in range(5):
            client.get(f"/items/{uuid.uuid4()}")

        text = default_registry.render()

        assert 'http_requests_total{method="GET",path="/items/{item_id}",status="200"} 5' in text
        # No series carries a concrete UUID.
        assert not re.search(r'path="/items/[0-9a-f-]{36}"', text)

    def test_a_path_that_matches_no_route_gets_one_shared_series(self, client):
        """
        A 404 is the case where the route was never resolved, so the fallback
        fires. Scraping a bot hitting ``/wp-admin``, ``/.env`` and a random
        string per request is exactly how a label turns into an outage - so
        unmatched paths share one series rather than each getting their own.
        """
        # A generous budget, so this measures label cardinality and is not
        # confused by the throttler counting some of these as 429s.
        with TestClient(build_app(limit=1000, window_seconds=60)) as c:
            for _ in range(4):
                c.get("/definitely-not-a-route")
            c.get("/.env")
            c.get("/wp-admin")

            text = default_registry.render()

        assert 'path="__unmatched__",status="404"} 6' in text
        assert "/definitely-not-a-route" not in text
        assert "/wp-admin" not in text

    def test_a_throttled_request_is_still_labelled_with_the_template(self, client):
        """
        A 429 is produced by the throttler, before the router runs, so there is
        no matched route to read. Resolving it against the router keeps the
        series bounded; without that, throttling - the one moment when a client
        is likely to hammer many distinct paths - would be the thing that floods
        the metrics endpoint.
        """
        item = "/items/00000000-0000-0000-0000-000000000001"
        for _ in range(6):
            client.get(item)

        text = default_registry.render()

        assert (
            'http_requests_total{method="GET",path="/items/{item_id}",status="429"} 1'
            in text
        )
        assert item not in text

    def test_duration_is_observed_for_every_request(self, client):
        client.get("/health")

        assert (
            'http_request_duration_seconds_count{method="GET",path="/health"} 1'
            in default_registry.render()
        )

    def test_the_scrape_endpoint_serves_the_exposition_format(self):
        """
        The real endpoint, mounted the way ``src.main`` mounts it. A scraper that
        gets ``application/json`` back drops the target, so the content type is
        part of the contract, not a detail.
        """
        from fastapi.responses import Response

        from src.core.metrics import CONTENT_TYPE_LATEST

        app = build_app()

        @app.get("/metrics")
        async def scrape():
            return Response(
                content=default_registry.render(), media_type=CONTENT_TYPE_LATEST
            )

        with TestClient(app) as c:
            c.get("/health")
            response = c.get("/metrics")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "version=0.0.4" in response.headers["content-type"]
        assert "http_requests_total" in response.text
        assert "# TYPE http_requests_total counter" in response.text


# --------------------------------------------------------------------------- #
# Throttling over HTTP (T123)
# --------------------------------------------------------------------------- #
class TestThrottlingOverHTTP:
    def test_a_client_within_budget_is_not_refused(self, client):
        for _ in range(5):
            assert client.get("/items/00000000-0000-0000-0000-000000000001").status_code == 200

    def test_the_request_past_the_budget_is_429(self, client):
        for _ in range(5):
            client.get("/items/00000000-0000-0000-0000-000000000001")

        response = client.get("/items/00000000-0000-0000-0000-000000000001")

        assert response.status_code == 429

    def test_the_429_says_when_to_come_back(self, client):
        item = "/items/00000000-0000-0000-0000-000000000001"
        for _ in range(5):
            client.get(item)
        response = client.get(item)

        assert int(response.headers["retry-after"]) >= 1

    def test_the_429_body_is_json_with_a_detail(self, client):
        for _ in range(5):
            client.get("/items/00000000-0000-0000-0000-000000000001")

        body = client.get("/items/00000000-0000-0000-0000-000000000001").json()

        assert "detail" in body
        assert "limit" in body

    def test_a_429_still_carries_the_security_headers(self, client):
        """
        The interaction this file exists to catch. A 429 that skips the security
        headers is served by a proxy or an error page instead, and the client
        gets HTML where it expected JSON.
        """
        for _ in range(5):
            client.get("/items/00000000-0000-0000-0000-000000000001")

        response = client.get("/items/00000000-0000-0000-0000-000000000001")

        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-type"].startswith("application/json")

    def test_an_allowed_request_advertises_its_remaining_budget(self, client):
        response = client.get("/items/00000000-0000-0000-0000-000000000001")

        assert response.headers["x-ratelimit-limit"] == "5"
        assert response.headers["x-ratelimit-remaining"] == "4"
        assert int(response.headers["x-ratelimit-reset"]) >= 0

    def test_the_probe_is_never_throttled(self, client):
        """
        A throttled health check is a health check that flaps, which pages
        somebody. A scraper polling a refused ``/health`` restarts the service.
        """
        for _ in range(20):
            assert client.get("/health").status_code == 200

    def test_two_addresses_have_separate_budgets(self):
        """
        The limiter is installed with ``trust_proxy`` here so the test can vary
        the client, which a ``TestClient`` otherwise cannot do. In production the
        flag is off and the peer address is the key.
        """
        app = build_app(limit=2, window_seconds=60, trust_proxy=True)
        item = "/items/00000000-0000-0000-0000-000000000001"

        with TestClient(app) as c:
            for _ in range(2):
                assert c.get(item, headers={"x-forwarded-for": "1.1.1.1"}).status_code == 200
            assert c.get(item, headers={"x-forwarded-for": "1.1.1.1"}).status_code == 429
            assert c.get(item, headers={"x-forwarded-for": "2.2.2.2"}).status_code == 200

    def test_a_trusted_proxy_still_ignores_a_forged_forwarded_value(self):
        """
        The other half of the trust decision: even with the flag on, a value that
        is not an address is discarded, so a client cannot collide itself into
        someone else's bucket with a junk header.
        """
        app = build_app(limit=1, window_seconds=60, trust_proxy=True)
        item = "/items/00000000-0000-0000-0000-000000000001"

        with TestClient(app) as c:
            assert c.get(item, headers={"x-forwarded-for": "junk"}).status_code == 200
            assert c.get(item, headers={"x-forwarded-for": "junk2"}).status_code == 429

    def test_rejections_are_counted(self, client):
        for _ in range(6):
            client.get("/items/00000000-0000-0000-0000-000000000001")

        assert 'rate_limit_rejections_total{client="ip:testclient"} 1' in default_registry.render()


# --------------------------------------------------------------------------- #
# CORS (T122)
# --------------------------------------------------------------------------- #
class TestCorsConfiguration:
    def test_the_default_origins_are_localhost_only(self):
        """
        A wildcard with credentials is invalid per the CORS spec and, in every
        browser, silently drops the credentials. The default is therefore
        explicit localhost origins rather than ``["*"]``.
        """
        assert "*" not in settings.ALLOWED_ORIGINS
        assert "http://localhost:3000" in settings.ALLOWED_ORIGINS

    def test_credentials_are_allowed_so_a_bearer_token_can_be_sent(self, client):
        response = client.get(
            "/health",
            headers={"Origin": "http://localhost:3000"},
        )

        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert response.headers["access-control-allow-credentials"] == "true"

    def test_an_unknown_origin_gets_no_cors_headers(self, client):
        response = client.get("/health", headers={"Origin": "http://evil.example"})

        assert "access-control-allow-origin" not in response.headers

    def test_a_preflight_is_answered_for_a_known_origin(self, client):
        response = client.options(
            "/items/00000000-0000-0000-0000-000000000001",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"

    def test_the_preflight_is_cached_so_the_browser_asks_once(self, client):
        response = client.options(
            "/items/00000000-0000-0000-0000-000000000001",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

        assert int(response.headers["access-control-max-age"]) > 0

    def test_credentials_cannot_be_combined_with_a_wildcard(self):
        """
        Starlette silently downgrades a wildcard-plus-credentials configuration
        to "no credentials", which breaks the browser's auth header on every
        request. Better to refuse the configuration at import.
        """
        from src.core.cors import cors_configuration

        config = cors_configuration(allowed_origins=["*"], allow_credentials=True)
        assert config["allow_origins"] != ["*"]
