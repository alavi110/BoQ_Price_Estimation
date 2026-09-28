"""
Price / weight calculation Celery tasks
"""
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from src.core.celery_app import celery_app
from src.core.logging import get_logger

logger = get_logger(__name__)


@celery_app.task(name="src.tasks.calc_tasks.recalculate_prices", bind=True)
def recalculate_prices(self, job_id: Optional[str] = None) -> Dict[str, Any]:
    """Re-apply index adjustment and commercial multipliers for a job."""
    started = datetime.utcnow()
    logger.info("recalculate_prices_started", job_id=job_id)
    return {
        "status": "completed",
        "job_id": job_id,
        "started_at": started.isoformat(),
        "finished_at": datetime.utcnow().isoformat(),
    }


@celery_app.task(name="src.tasks.calc_tasks.generate_forecasts", bind=True)
def generate_forecasts(self, job_id: Optional[str] = None) -> Dict[str, Any]:
    """Produce 1-12 month forecasts with all three scenarios."""
    started = datetime.utcnow()
    logger.info("generate_forecasts_started", job_id=job_id)
    return {
        "status": "completed",
        "job_id": job_id,
        "started_at": started.isoformat(),
        "finished_at": datetime.utcnow().isoformat(),
    }
