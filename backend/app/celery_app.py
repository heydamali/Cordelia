from datetime import timedelta

from celery import Celery

from app.config import settings

celery_app = Celery(
    "cordelia",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.tasks.gmail_tasks", "app.tasks.llm_tasks"],
)

celery_app.conf.beat_schedule = {
    "renew-gmail-watches": {
        "task": "app.tasks.gmail_tasks.renew_all_watches",
        "schedule": timedelta(days=6),   # Gmail watches expire at 7 days
    },
}
