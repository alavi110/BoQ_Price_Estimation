"""
ETL Celery tasks
"""
from datetime import datetime
from typing import Any, Dict

from src.core.celery_app import celery_app
from src.core.config import settings
from src.core.logging import get_logger
from src.core.metrics import ETL_RUNS

logger = get_logger(__name__)


def _run_source_with_retry(name: str, func, max_attempts: int = None, base_seconds: float = None) -> Dict[str, Any]:
    """
    Run a source ETL function with exponential backoff retry.

    Each attempt is recorded in the ETL_RUNS metric with the outcome.
    """
    if max_attempts is None:
        max_attempts = settings.ETL_MAX_ATTEMPTS
    if base_seconds is None:
        base_seconds = settings.ETL_RETRY_BASE_SECONDS

    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        try:
            result = func()
            ETL_RUNS.labels(source=name, outcome="success").inc()
            return {"status": "completed", "attempts": attempt, **result}
        except Exception as exc:
            if attempt >= max_attempts:
                ETL_RUNS.labels(source=name, outcome="failed").inc()
                logger.error("etl_source_failed_final", source=name, error=str(exc), attempts=attempt)
                return {"status": "failed", "attempts": attempt, "error": str(exc)}
            # Exponential backoff
            wait = base_seconds * (2 ** (attempt - 1))
            logger.warning("etl_source_retry", source=name, attempt=attempt, wait_seconds=wait, error=str(exc))
            import time
            time.sleep(wait)

    return {"status": "failed", "attempts": attempt, "error": "max attempts exceeded"}


@celery_app.task(name="src.tasks.etl_tasks.run_nightly_etl", bind=True)
def run_nightly_etl(self) -> Dict[str, Any]:
    """
    Nightly market-data refresh (02:00 Tehran).

    Each source is refreshed independently so a single outage degrades
    freshness reporting instead of aborting the whole run.

    Retry logic: each source is retried up to ETL_MAX_ATTEMPTS times with
    exponential backoff starting at ETL_RETRY_BASE_SECONDS.
    """
    started = datetime.utcnow()
    logger.info("nightly_etl_started")

    def fetch_commodity():
        # TODO: Implement actual connector
        return {"source": "commodity", "records": 0}

    def fetch_currency():
        # TODO: Implement actual connector
        return {"source": "currency", "records": 0}

    def fetch_labor():
        # TODO: Implement actual connector
        return {"source": "labor", "records": 0}

    source_funcs = {
        "commodity": fetch_commodity,
        "currency": fetch_currency,
        "labor": fetch_labor,
    }

    sources: Dict[str, Dict[str, Any]] = {}
    for name, func in source_funcs.items():
        sources[name] = _run_source_with_retry(name, func)

    return {
        "status": "completed",
        "started_at": started.isoformat(),
        "finished_at": datetime.utcnow().isoformat(),
        "staleness_hours": settings.MARKET_DATA_STALENESS_HOURS,
        "sources": sources,
    }
