"""
Weekly forecast model retraining schedule

Registers the Celery beat entry that re-trains every component's model once a
week, plus the callable used by the worker and by the admin endpoint.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from src.core.celery_app import celery_app
from src.core.config import settings
from src.core.logging import get_logger
from src.services.model_retrainer import ModelRetrainer, RetrainingReport

logger = get_logger(__name__)

#: default lookback window handed to the retrainer
DEFAULT_HISTORY_DAYS = 365


def _default_history(days: int = DEFAULT_HISTORY_DAYS):
    """Placeholder history source; replaced by the ETL-backed query in prod."""
    raise NotImplementedError(
        "No market history source configured - pass histories= explicitly"
    )


class RetrainingScheduler:
    """
    Drives the weekly retrain pass.

    ``run`` is intentionally tolerant: one failing component must not abort the
    whole sweep, so every report is collected and returned to the caller.
    """

    def __init__(self, retrainer: Optional[ModelRetrainer] = None):
        self.retrainer = retrainer or ModelRetrainer()
        self.logger = logger
        self.last_run: Optional[datetime] = None
        self.last_reports: List[RetrainingReport] = []

    # ------------------------------------------------------------------ #
    def run(
        self,
        histories: Optional[Dict[str, Any]] = None,
        model_type: str = "prophet",
        days: int = DEFAULT_HISTORY_DAYS,
        **kwargs,
    ) -> List[RetrainingReport]:
        """
        Retrain every component that has history available.

        Args:
            histories: ``{component_id: history}``. When omitted the configured
                history source is used.
            model_type: Backend to train.
        """
        if histories is None:
            histories = _default_history(days)
        if not histories:
            raise ValueError("No component histories supplied for retraining")

        reports: List[RetrainingReport] = []
        for component_id, history in histories.items():
            report = self.retrainer.retrain(
                component_id=component_id,
                history=history,
                model_type=model_type,
                **kwargs,
            )
            reports.append(report)
            if report.status == "failed":
                self.logger.error(
                    "component_retrain_failed", component_id=component_id, error=report.error
                )

        self.last_run = datetime.utcnow()
        self.last_reports = reports
        self.logger.info(
            "retraining_sweep_complete",
            components=len(reports),
            succeeded=sum(1 for r in reports if r.status == "completed"),
        )
        return reports

    # ------------------------------------------------------------------ #
    def next_run_at(self, from_time: Optional[datetime] = None) -> datetime:
        """Next weekly Sunday 03:00 (Tehran) boundary, in naive UTC terms."""
        now = from_time or datetime.utcnow()
        days_until_sunday = (6 - now.weekday()) % 7
        candidate = (now + timedelta(days=days_until_sunday)).replace(
            hour=3, minute=0, second=0, microsecond=0
        )
        if candidate <= now:
            candidate += timedelta(days=7)
        return candidate

    def describe(self) -> Dict[str, Any]:
        return {
            "schedule": settings.MODEL_RETRAIN_SCHEDULE_CRON,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "next_run": self.next_run_at().isoformat(),
            "last_results": [r.to_dict() for r in self.last_reports],
        }


#: process-wide scheduler
retraining_scheduler = RetrainingScheduler()


def register_schedule(app=None) -> Iterable:
    """
    Attach the weekly retrain entry to a Celery app's beat schedule.

    The task itself lives in ``src.tasks.forecast_tasks`` so that it is only
    registered once, in the worker's import graph.
    """
    app = app or celery_app
    schedule = dict(getattr(app.conf, "beat_schedule", {}) or {})
    schedule["weekly-model-retrain"] = {
        "task": "src.tasks.forecast_tasks.retrain_all_models",
        "schedule": settings.MODEL_RETRAIN_SCHEDULE_CRON,
    }
    app.conf.beat_schedule = schedule
    logger.info("retraining_schedule_registered", schedule=settings.MODEL_RETRAIN_SCHEDULE_CRON)
    return schedule
