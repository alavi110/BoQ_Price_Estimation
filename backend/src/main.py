"""
BoQ Price Forecast - Main Application Entry Point
"""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes import admin, boq, commercial, exports, forecasts, prices, weights
from src.core.celery_app import celery_app  # noqa: F401  (registers the task modules)
from src.core.config import settings
from src.core.cors import cors_configuration
from src.core.database import close_db, get_db, init_db
from src.core.logging import get_logger, setup_logging
from src.core.metrics import CONTENT_TYPE_LATEST, default_registry
from src.core.middleware import (
    MetricsMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from src.core.rate_limit import RateLimiter

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Reported rather than raised. Refusing to boot on a missing environment
    # variable turns a configuration mistake into an outage, and the operator
    # learns less from a traceback than from a list of what to change.
    for problem in settings.production_problems():
        logger.error("unsafe_production_configuration", problem=problem)

    missing = settings.undocumented_settings()
    if missing:
        logger.warning(
            "settings_missing_from_env_template",
            names=missing,
            template=".env.example",
        )

    await init_db()
    yield
    await close_db()


app = FastAPI(
    title="BoQ Price Forecast API",
    description="AI-powered BoQ price adjustment and forecasting system",
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    openapi_url="/openapi.json" if not settings.is_production else None,
)

# --------------------------------------------------------------------------- #
# Middleware
#
# Starlette runs the most recently added middleware outermost, so these are
# added in reverse of the order they must run:
#
#   request context -> security headers -> rate limit -> metrics -> routes
#
# Context first so the throttler can key on a user rather than a shared NAT
# address; security headers outside the throttler so a 429 is not served without
# them; metrics inside both so it records the status they actually produced.
# --------------------------------------------------------------------------- #
app.add_middleware(MetricsMiddleware)
if settings.RATE_LIMIT_ENABLED:
    app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)

if settings.ALLOWED_HOSTS:
    # Only when configured. With the list empty, TrustedHostMiddleware rejects
    # everything, and in development there is no Host to configure it with.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)

app.add_middleware(CORSMiddleware, **cors_configuration(settings.ALLOWED_ORIGINS))

# Include routers
app.include_router(boq.router, prefix="/boq", tags=["BoQ Management"])
app.include_router(weights.router, prefix="/weights", tags=["Weight Attribution"])
app.include_router(prices.router, prefix="/prices", tags=["Price Calculation"])
app.include_router(forecasts.router, prefix="/forecasts", tags=["Forecasting"])
app.include_router(exports.router, prefix="/exports", tags=["Exports"])
app.include_router(commercial.router, prefix="/commercial", tags=["Commercial Adjustments"])
app.include_router(admin.router, prefix="/admin", tags=["Administration"])


# --------------------------------------------------------------------------- #
# Operational endpoints (T126)
# --------------------------------------------------------------------------- #
@app.get("/health")
async def health_check():
    """
    Liveness.

    Reports the process and nothing else, deliberately. A check that touches the
    database, Redis and the LLM provider fails whenever any of them is slow, and
    a liveness probe that fails takes the pod out of rotation - so a dependency
    blip becomes a full restart of every replica.
    """
    return {
        "status": "healthy",
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
    }


@app.get("/ready")
async def readiness_check():
    """
    Dependency-aware readiness.

    Separate from liveness for the reason above: this is the one that should
    report a degraded dependency, so that traffic drains while the price is
    still correct from the last good market data rather than being served by
    replicas that cannot reach anything.

    The body carries a per-dependency verdict; the status code is 200 when the
    process can serve and 503 when it cannot. Every check degrades rather than
    raising, so one unreachable dependency still produces a body an operator can
    read.

    No request-scoped session is requested. The orchestrator polls this on a
    timer and may be answered by a replica in the middle of serving something
    else; borrowing a connection from the request pool to answer a probe is how
    a probe takes the pool down with it.
    """
    checks = await dependency_checks()
    blocking = [name for name, check in checks.items() if check["status"] == "error"]
    return JSONResponse(
        status_code=200 if not blocking else 503,
        content={
            "status": "ready" if not blocking else "degraded",
            "version": settings.VERSION,
            "checks": checks,
        },
    )


async def dependency_checks(db: AsyncSession = None) -> dict:
    """
    One verdict per dependency, each stamped with the time it was taken.

    Never raises. A readiness probe that throws reports nothing, which is the
    same as reporting "ok".

    Redis is deliberately absent: it backs Celery, and this process serves
    requests without it. Including it would take every replica out of rotation
    when a queue is unhealthy, for requests that never touch the queue.
    """
    from datetime import datetime, timezone

    from sqlalchemy import func, select

    from src.models.market_index import MarketIndex

    checked_at = datetime.now(timezone.utc).isoformat()
    checks: dict = {}

    def stamp(verdict: dict) -> dict:
        return {**verdict, "checked_at": checked_at}

    # -- database ---------------------------------------------------------- #
    try:
        if db is not None:
            await db.execute(text("SELECT 1"))
        else:
            await _check_database()
        checks["database"] = stamp({"status": "ok"})
    except Exception as exc:
        checks["database"] = stamp({"status": "error", "error": str(exc)[:200]})

    # -- market data ------------------------------------------------------- #
    # Stale data is a warning, not a block. The price service is built to fall
    # back to the last good reading, so refusing to serve would turn a stale
    # index into an outage rather than a visibly dated price.
    try:
        if db is None:
            checks["market_data"] = stamp({"status": "unknown"})
        else:
            newest = await db.execute(select(func.max(MarketIndex.date)))
            latest = newest.scalar()
            if latest is None:
                checks["market_data"] = stamp(
                    {
                        "status": "degraded",
                        "reason": "no market readings yet; attribution falls back "
                        "to keyword matching",
                    }
                )
            else:
                if hasattr(latest, "date"):
                    latest = latest.date()
                age_hours = (datetime.now(timezone.utc).date() - latest).days * 24
                stale = age_hours > settings.MARKET_DATA_STALENESS_HOURS
                checks["market_data"] = stamp(
                    {
                        "status": "stale" if stale else "ok",
                        "latest_reading": latest.isoformat(),
                        "age_hours": age_hours,
                        "staleness_budget_hours": settings.MARKET_DATA_STALENESS_HOURS,
                    }
                )
    except Exception as exc:
        checks["market_data"] = stamp({"status": "error", "error": str(exc)[:200]})

    # -- LLM provider ------------------------------------------------------ #
    # An absent key is a configuration state, not a failure: attribution falls
    # back to keyword matching and reports 0.3 confidence, which says so.
    checks["openrouter"] = stamp(
        {
            "status": "ok" if settings.OPENROUTER_API_KEY else "degraded",
            "reason": None
            if settings.OPENROUTER_API_KEY
            else "OPENROUTER_API_KEY is not set; attribution falls back to keywords",
        }
    )

    return checks


async def _check_database() -> None:
    """One round trip, so the check is about reachability and not a long query."""
    from sqlalchemy import text

    from src.core.database import get_engine

    async with get_engine().connect() as connection:
        await connection.execute(text("SELECT 1"))


@app.get("/metrics", include_in_schema=False)
async def metrics():
    """
    Prometheus scrape endpoint (T118).

    Off in production unless ``METRICS_ENABLED`` says otherwise: the exposition
    discloses route names, error rates and the shape of the deployment to
    anything that can reach the port.
    """
    if not settings.METRICS_ENABLED:
        return JSONResponse(
            status_code=404,
            content={"detail": "Metrics are disabled on this deployment."},
        )
    return Response(content=default_registry.render(), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
