"""
Exception handlers for the HTTP boundary (T112).

An unhandled exception in a JSON API should still produce a JSON response with a
correlation id, for three reasons:

* a browser that receives an HTML error page for a JSON request reports an
  opaque CORS or parse failure that points nowhere near the cause;
* without the request id in the body, the id in the ``X-Request-ID`` header is
  the only handle, and a caller who cannot read headers - a log aggregator
  posting the body, say - has nothing to quote;
* the message must not leak. An exception string can contain a SQL fragment, a
  file path or a parameter value, so production gets a generic message and the
  detail goes to the log, keyed by the same request id.

Registering handlers is not enough on its own for the unhandled case, and that
is a structural fact rather than a style choice. Starlette builds the stack as
``ServerErrorMiddleware -> user middleware -> ExceptionMiddleware -> router``, so
a 500 produced by ``ServerErrorMiddleware`` never passes back out through the
security-headers layer and would ship with no headers at all.
:class:`~src.core.middleware.ErrorBoundaryMiddleware` therefore catches first,
from inside the chain, and calls :func:`unhandled_response` here - one
definition of the body, reachable from both places.
"""
import uuid
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or uuid.uuid4().hex


def _error_body(request: Request, detail: str, **extra: Any) -> Dict[str, Any]:
    body: Dict[str, Any] = {"detail": detail, "request_id": _request_id(request)}
    body.update({key: value for key, value in extra.items() if value is not None})
    return body


def install_error_handlers(app: FastAPI) -> FastAPI:
    """
    Register the handlers on ``app``. Idempotent enough for a test fixture.

    Deliberately does not override the handlers FastAPI installs for
    ``HTTPException`` and ``RequestValidationError`` beyond the shape: their
    behaviour is already correct, and a 422 listing every offending field is more
    useful than a summary.
    """

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(request, str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
        return JSONResponse(
            status_code=422,
            content=_error_body(
                request,
                "Request validation failed.",
                errors=_safe_errors(exc),
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        return unhandled_response(request, exc)

    return app


def unhandled_response(request: Request, exc: Exception) -> JSONResponse:
    """
    The 500 for an exception nothing else caught.

    Registered as a handler *and* called directly by
    :class:`~src.core.middleware.ErrorBoundaryMiddleware`, because Starlette's
    ``ServerErrorMiddleware`` is always the outermost layer: a response it
    generates never passes back out through anything added with
    ``add_middleware``, so a 500 produced there would carry no security headers
    at all. The boundary middleware catches the exception first and produces the
    response from inside the chain, where the headers still apply; this function
    is the single definition of what that response looks like.

    The message is generic in production because the detail can carry a SQL
    fragment, a file path or a parameter value. The request id is the join
    between what the caller sees and what the log says.
    """
    request_id = _request_id(request)
    logger.exception("unhandled_exception", request_id=request_id, error=str(exc))
    detail = (
        "Internal server error."
        if settings.is_production
        else f"{type(exc).__name__}: {exc}"
    )
    return JSONResponse(status_code=500, content=_error_body(request, detail))


def _safe_errors(exc: RequestValidationError) -> list:
    """
    Validation errors in a shape a client can act on.

    Pydantic's own ``ctx`` can hold non-serialisable objects, and FastAPI's
    default handler passes it straight into ``jsonable_encoder``; keeping only
    the fields means one bad payload cannot turn a 422 into a 500.
    """
    fields = []
    for error in exc.errors():
        fields.append(
            {
                "loc": [str(part) for part in error.get("loc", ())],
                "msg": str(error.get("msg", "")),
                "type": str(error.get("type", "")),
            }
        )
    return fields
