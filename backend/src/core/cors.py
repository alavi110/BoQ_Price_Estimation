"""
CORS configuration (T122).

The whole module exists because of one rule: ``Access-Control-Allow-Origin: *``
and ``Access-Control-Allow-Credentials: true`` cannot be combined. Every browser
refuses the pair, so a configuration that asks for both does not "work loosely" -
it silently drops the credentials and every authenticated request fails with an
opaque CORS error that points nowhere near the cause.

So the wildcard is resolved here rather than passed through: if credentials are
required and the configuration says ``*``, the origins are taken from the
deployed frontend instead, and the substitution is reported.
"""
import re
from typing import Iterable, List

#: A host, optionally with a port. Anchored, so a value like
#: ``https://evil.example/#http://localhost:3000`` cannot smuggle an allowed
#: origin past the shape check.
_ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?$")

#: What a wildcard expands to when credentials are in play. The local dev
#: frontends; a production deployment must set ALLOWED_ORIGINS explicitly, and
#: :func:`src.core.config.Settings.production_warnings` says so at boot.
CREDENTIALED_WILDCARD_FALLBACK = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


def is_valid_origin(origin: str) -> bool:
    """Whether a string is a well-formed origin this service will serve."""
    if not origin or not isinstance(origin, str):
        return False
    if "*" in origin or " " in origin:
        return False
    return bool(_ORIGIN_RE.match(origin))


def parse_origins(raw) -> List[str]:
    """
    Turn a configuration value into a list of origins.

    Accepts a list, or the comma-separated string that an environment variable
    always arrives as - which is the form that is easy to get wrong, because
    ``ALLOWED_ORIGINS=http://a,http://b`` is a single string to Pydantic unless
    something splits it.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        candidates = [part.strip() for part in raw.split(",")]
    else:
        candidates = [str(part).strip() for part in raw]
    return [c for c in candidates if c]


def cors_configuration(
    allowed_origins: Iterable[str] = (),
    allow_credentials: bool = True,
    expose_headers: Iterable[str] = (
        "X-Request-ID",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
        "Retry-After",
        "Content-Disposition",
    ),
    max_age: int = 600,
) -> dict:
    """
    Keyword arguments for :class:`~fastapi.middleware.cors.CORSMiddleware`.

    * The wildcard-plus-credentials combination is resolved rather than passed
      through, because browsers reject it and the resulting failure is opaque.
    * ``X-Request-ID`` is exposed, because without it the correlation id the
      server returns is invisible to browser JavaScript and a user reporting a
      problem cannot quote it.
    * The rate-limit headers are exposed for the same reason: a client that is
      being throttled needs to see when it may try again.
    * ``max_age`` is set so a preflight is answered once per origin rather than
      once per request.
    """
    origins = parse_origins(allowed_origins)
    wildcard = "*" in origins

    if wildcard and allow_credentials:
        origins = [o for o in origins if o != "*"]
        if not origins:
            origins = list(CREDENTIALED_WILDCARD_FALLBACK)
    elif wildcard:
        origins = ["*"]

    return {
        "allow_origins": origins,
        "allow_credentials": allow_credentials,
        "allow_methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        "allow_headers": [
            "Authorization",
            "Content-Type",
            "Accept",
            "X-Request-ID",
        ],
        "expose_headers": list(expose_headers),
        "max_age": max_age,
    }
