"""
HTTP middleware (T112, T118, T122, T123).

Four concerns, deliberately in separate classes, in this outermost-to-innermost
order when installed with ``add_middleware`` in reverse:

``RequestContextMiddleware``
    Assigns a correlation id, echoes it back, and decodes the bearer token's
    subject onto ``request.state``. It has to run before the throttler, because
    that is the only point at which a per-user budget can be keyed on anything
    but a shared NAT address - the auth dependency itself runs later, inside the
    route.

``SecurityHeadersMiddleware``
    Adds the response headers. Outside the metrics middleware deliberately: a
    request that is refused with a 429, or that raises, still passes through here.

``RateLimitMiddleware``
    Refuses over-budget requests with a 429 that says when to come back.

``MetricsMiddleware``
    Innermost, so it sees the real status code and the matched route.

The ordering matters and is the kind of thing that breaks silently: security
headers added inside the metrics middleware would be absent from any response
the metrics middleware short-circuits, and a rate limiter installed outside the
context middleware could never key on a user.
"""
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.core.logging import get_logger
from src.core.metrics import (
    RATE_LIMIT_REJECTIONS,
    HTTP_REQUESTS,
    HTTP_REQUEST_DURATION,
)
from src.core.rate_limit import Decision, RateLimiter, client_key, is_exempt

logger = get_logger(__name__)

#: Longest correlation id accepted from a caller. Anything longer is discarded:
#: it is a client bug or an attempt to make a log line unparseable.
MAX_REQUEST_ID_LENGTH = 64

#: Characters permitted in a caller-supplied correlation id. Deliberately narrow
#: - enough for a UUID, a trace id or a short name, and nothing that could carry a
#: newline into a log line or a header.
_REQUEST_ID_RE = None  # built lazily below; see _clean_request_id


def _clean_request_id(raw):
    """Return a safe correlation id, or ``None`` if the input is not usable."""
    import re

    global _REQUEST_ID_RE
    if _REQUEST_ID_RE is None:
        _REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:@/-]{1,%d}$" % MAX_REQUEST_ID_LENGTH)

    if not raw:
        return None
    candidate = raw.strip()
    if not candidate or not _REQUEST_ID_RE.match(candidate):
        return None
    return candidate


class ErrorBoundaryMiddleware(BaseHTTPMiddleware):
    """
    Turns an unhandled exception into a JSON 500 *from inside the chain*.

    Outermost of the user middlewares, and it has to be. Starlette assembles
    ``ServerErrorMiddleware -> user middlewares -> router``, so anything
    ``ServerErrorMiddleware`` renders never passes back out through the layers
    that add security headers, request ids and metrics. A 500 raised inside a
    route would then be the one response in the system with no correlation id
    and no ``X-Content-Type-Options`` - the response an operator is most likely
    to need to read.

    Catching here means the response is an ordinary one as far as everything
    downstream is concerned, so all of them run on it.
    """

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            from src.core.error_responses import unhandled_response

            return unhandled_response(request, exc)


#: Returned for a path that matches no route. A constant on purpose - see
#: :func:`route_template`.
UNMATCHED_ROUTE = "__unmatched__"


def route_template(request: Request) -> str:
    """
    The matched route pattern, e.g. ``/items/{item_id}``.

    Read from the scope when routing has already happened, and otherwise resolved
    by asking the router directly.

    That second case is not an edge case: a request refused by the throttler, or
    one that matched no route, never reaches the router, so ``scope["route"]`` is
    unset at the time the outermost middleware records it. Falling back to the
    raw path there would be the mistake this whole function exists to prevent -
    every ``/weights/<uuid>`` becomes a permanent time series, the scrape grows
    without bound, and the service falls over under the monitoring meant to help
    it. A path that genuinely matches nothing gets one constant series instead.
    """
    route = request.scope.get("route")
    path_format = getattr(route, "path_format", None)
    if path_format:
        return path_format

    path = request.scope.get("path") or request.url.path
    resolved = _match_route(request, path)
    return resolved or UNMATCHED_ROUTE


def _match_route(request: Request, path: str) -> str:
    """
    Ask the router which route owns ``path``, without running it.

    ``Route.matches`` does the same matching the router would, including the
    host- and path-formatting the path went through, so the answer is the
    template the request *would* have been counted under had it not been
    short-circuited. It runs on the fallback path only, and over tens of routes,
    which is not a cost worth optimising away from correctness.
    """
    scope = request.scope
    router = getattr(request.app, "router", None)
    for route in getattr(router, "routes", ()) or ():
        match, _ = route.matches(scope)
        if match.name != "FULL":
            continue
        path_format = getattr(route, "path_format", None)
        if path_format:
            return path_format
    return ""


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Correlation id, and the caller's subject when the token decodes."""

    async def dispatch(self, request: Request, call_next):
        request_id = _clean_request_id(request.headers.get("x-request-id")) or uuid.uuid4().hex
        request.state.request_id = request_id
        request.state.user_id = _subject_from_token(request)
        request.state.started_at = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            # The id is logged, not returned: a response cannot be produced
            # here, and a trace that vanishes on the error path is exactly the
            # trace somebody will want.
            logger.exception(
                "request_failed",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
            )
            raise

        response.headers["x-request-id"] = request_id
        return response


def _subject_from_token(request: Request):
    """
    The bearer token's ``user_id`` claim, or ``None``.

    Decoded here rather than taken from the auth dependency because this
    middleware runs before routing. A token that does not decode is simply not a
    user as far as throttling is concerned - the route's own dependency will
    reject it with a 401, which is the right place for that decision. Nothing is
    verified against a database, so this is a hint about *which* bucket, never an
    authorisation.
    """
    from jose import JWTError

    from src.core.config import settings
    from src.core.security import decode_token

    header = request.headers.get("authorization")
    if not header or not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    if not token:
        return None
    try:
        return decode_token(token).user_id
    except Exception:
        return None


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    The response headers a browser needs in order not to do something dangerous
    with a JSON API's response.

    ``FORCE_HTTPS`` rather than ``ENVIRONMENT`` gates HSTS, because the two are
    not the same question: a production deployment that terminates TLS somewhere
    this process cannot see still has to send the header, and a staging
    deployment on plain http must not. Sending HSTS over http pins the hostname
    and breaks the next plain-http request to it.
    """

    #: Baseline headers for every response. ``setdefault`` semantics: a route or
    #: a proxy that has already made a decision keeps it. The values here are the
    #: safe ones, so a route can only strengthen them, never weaken them -
    #: except by setting the key to something else, which is why each is
    #: checked rather than overwritten.
    BASELINE_HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Permissions-Policy": (
            "camera=(), microphone=(), geolocation=(), payment=()"
        ),
    }

    #: Conservative for an API: everything same-origin, nothing inline, nothing
    #: remote. The defence in depth is that a JSON body rendered as a page - a
    #: sniffing proxy, a content-type confusion - has no script to run.
    CONTENT_SECURITY_POLICY = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'"
    )

    def __init__(self, app, force_https: bool = None, hsts_max_age: int = 31536000):
        super().__init__(app)
        from src.core.config import settings

        self._force_https = (
            settings.FORCE_HTTPS if force_https is None else force_https
        )
        self._hsts = f"max-age={hsts_max_age}; includeSubDomains"

    async def dispatch(self, request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception as exc:
            # The one place a 500 can be turned into a response that is *inside*
            # this layer's reach. Every other middleware is bypassed by an
            # exception, so a 500 built further out would be the one response in
            # the system without these headers - and without a request id, since
            # the response is produced after the header block below has already
            # been skipped.
            from src.core.error_responses import unhandled_response

            response = unhandled_response(request, exc)

        return self._apply(request, response)

    def _apply(self, request: Request, response: Response) -> Response:
        # ``MutableHeaders`` is not a MutableMapping in this Starlette version,
        # so there is no ``setdefault``/``pop``; the membership test is the
        # equivalent.
        headers = response.headers
        for name, value in self.BASELINE_HEADERS.items():
            if name not in headers:
                headers[name] = value
        if "Content-Security-Policy" not in headers:
            headers["Content-Security-Policy"] = self.CONTENT_SECURITY_POLICY
        if self._force_https and "Strict-Transport-Security" not in headers:
            headers["Strict-Transport-Security"] = self._hsts
        # The framework banner costs nothing to remove and tells an attacker
        # which server to try next.
        if "server" in headers:
            del headers["server"]
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Refuses over-budget requests.

    Refusal is a JSON ``429`` with ``Retry-After``, and it is produced here
    rather than by an exception handler so that it passes back out through the
    security-headers middleware on the way - an error page served without those
    headers is worse than the throttle it came from.
    """

    def __init__(
        self,
        app,
        limiter: RateLimiter = None,
        trust_proxy: bool = None,
    ):
        super().__init__(app)
        from src.core.config import settings

        self.limiter = limiter or RateLimiter(
            limit=settings.RATE_LIMIT_REQUESTS,
            window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
        )
        self._trust_proxy = (
            settings.TRUST_PROXY_HEADERS if trust_proxy is None else trust_proxy
        )

    async def dispatch(self, request: Request, call_next):
        if is_exempt(request.url.path, request.method):
            return await call_next(request)

        key = client_key(request, trust_proxy=self._trust_proxy)
        decision = self.limiter.check(key)

        if not decision.allowed:
            RATE_LIMIT_REJECTIONS.labels(client=key).inc()
            logger.warning(
                "rate_limited",
                request_id=getattr(request.state, "request_id", None),
                key=key,
                path=request.url.path,
                retry_after=decision.retry_after,
            )
            return self._refusal(decision)

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        response.headers["X-RateLimit-Reset"] = str(decision.reset_after)
        return response

    def _refusal(self, decision: Decision) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={
                "detail": (
                    f"Rate limit exceeded: at most {decision.limit} requests "
                    f"per window. Retry after {decision.retry_after}s."
                ),
                "limit": decision.limit,
                "retry_after": decision.retry_after,
            },
            headers={
                "Retry-After": str(decision.retry_after),
                "X-RateLimit-Limit": str(decision.limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(decision.reset_after),
            },
        )


class MetricsMiddleware(BaseHTTPMiddleware):
    """
    Counts requests and observes their duration.

    Innermost, so the status code is the one the route actually produced - a 404
    that never reached a route and a 500 raised inside one are different events
    and an alert on "errors" needs to tell them apart.

    A request that raises is still counted, with the status the exception
    handler turned it into if there is one and 500 otherwise. Silently dropping
    it would make an outage look like a quiet period, which is the one thing a
    request counter must never do.
    """

    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # The template is read after the call on the success path, because
            # the route is matched by the router *below* this middleware - at
            # entry, ``scope["route"]`` does not exist yet.
            self._record(request, route_template(request), 500, started)
            raise
        self._record(request, route_template(request), response.status_code, started)
        return response

    def _record(
        self, request: Request, template: str, status: int, started: float
    ) -> None:
        method = request.method
        elapsed = time.perf_counter() - started
        HTTP_REQUESTS.labels(
            method=method, path=template, status=str(status)
        ).inc()
        HTTP_REQUEST_DURATION.labels(method=method, path=template).observe(elapsed)
        logger.debug(
            "request",
            request_id=getattr(request.state, "request_id", None),
            method=method,
            path=template,
            status=status,
            duration_seconds=round(elapsed, 6),
        )
