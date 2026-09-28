"""
Forecast Celery tasks
"""
from typing import Any, Dict, Optional

from src.core.celery_app import celery_app
from src.core.config import settings
from src.core.logging import get_logger
from src.core.metrics import ETL_RUNS
from src.services.retraining_scheduler import retraining_scheduler

logger = get_logger(__name__)


def _retrain_component_with_retry(component_name: str, func, max_attempts: int = 3, base_seconds: float = 1.0) -> Dict[str, Any]:
    """
    Run a component retraining function with exponential backoff retry.

    Each attempt is recorded in the ETL_RUNS metric with the outcome.
    """
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        try:
            result = func()
            ETL_RUNS.labels(source=f"retrain_{component_name}", outcome="success").inc()
            return {"status": "completed", "attempts": attempt, **result}
        except Exception as exc:
            if attempt >= max_attempts:
                ETL_RUNS.labels(source=f"retrain_{component_name}", outcome="failed").inc()
                logger.error("retrain_component_failed_final", component=component_name, error=str(exc), attempts=attempt)
                return {"status": "failed", "attempts": attempt, "error": str(exc)}
            # Exponential backoff
            wait = base_seconds * (2 ** (attempt - 1))
            logger.warning("retrain_component_retry", component=component_name, attempt=attempt, wait_seconds=wait, error=str(exc))
            import time
            time.sleep(wait)

    return {"status": "failed", "attempts": attempt, "error": "max attempts exceeded"}


@celery_app.task(name="src.tasks.forecast_tasks.retrain_all_models", bind=True)
def retrain_all_models(self, histories: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Weekly retraining sweep, invoked by Celery beat.

    A failure in one component must not abort the sweep, so the scheduler
    collects per-component reports; only a total failure is reported as such.

    Retry logic: each component is retried up to 3 times with exponential backoff.
    """
    try:
        reports = retraining_scheduler.run(histories=histories)
    except Exception as exc:
        logger.error("retrain_all_models_failed", error=str(exc))
        return {"status": "failed", "error": str(exc), "results": []}

    results = []
    for r in reports:
        component_name = r.component if hasattr(r, "component") else "unknown"
        results.append(_retrain_component_with_retry(component_name, lambda: r.to_dict()))

    return {
        "status": "completed",
        "results": results,
    }
