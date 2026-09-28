"""
Celery configuration for async task processing
"""
from celery import Celery
from src.core.config import settings

celery_app = Celery(
    "boq_price_forecast",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "src.tasks.etl_tasks",
        "src.tasks.forecast_tasks",
        "src.tasks.calc_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone=settings.ETL_TIMEZONE,
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour
    task_soft_time_limit=3000,  # 50 minutes
    worker_prefetch_multiplier=4,
    worker_max_tasks_per_child=100,
    beat_schedule={},
)


# Celery Beat Schedule
celery_app.conf.beat_schedule = {
    "nightly-etl": {
        "task": "src.tasks.etl_tasks.run_nightly_etl",
        "schedule": settings.ETL_SCHEDULE_CRON,
    },
    "weekly-model-retrain": {
        "task": "src.tasks.forecast_tasks.retrain_all_models",
        "schedule": settings.MODEL_RETRAIN_SCHEDULE_CRON,
    },
}


@celery_app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")