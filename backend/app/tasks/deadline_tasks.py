from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models.task import Task
from app.models.user import User
from app.services.completion_check import check_and_sync_completion
from app.services.notification_service import notify_task_reminder

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.deadline_tasks.process_task_deadlines")
def process_task_deadlines() -> None:
    """
    Runs every 30 minutes via Celery Beat. Three ordered passes:

    Pass 1 — Re-surface snoozed tasks whose snooze has expired.
    Pass 2 — Fire notify_at datetimes that have passed but not been sent.
    Pass 3 — Expire overdue pending tasks.

    Pass order is critical: Pass 1 before Pass 3 ensures a just-resurfaced-but-overdue
    task gets expired in the same run.
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        # Pass 1 — Re-surface snoozed tasks whose snooze has expired
        snoozed = db.query(Task).filter(
            Task.status == "snoozed",
            Task.snoozed_until.isnot(None),
            Task.snoozed_until <= now,
        ).all()
        for t in snoozed:
            t.status = "pending"
            t.snoozed_until = None
            t.updated_at = now
        if snoozed:
            db.commit()

        # Pass 2 — Fire notify_at datetimes that have passed but not been sent
        pending = db.query(Task).filter(Task.status == "pending").all()
        fired = False
        for t in pending:
            if not t.notify_at:
                continue
            sent = set(t.notifications_sent or [])
            newly: list[str] = []
            for dt_str in t.notify_at:
                if dt_str in sent:
                    continue
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                if dt > now:
                    continue
                # Before notifying, refresh source and check if already completed
                user = db.query(User).filter(User.id == t.user_id).first()
                if not user:
                    continue
                if check_and_sync_completion(t, user, db):
                    # Task was auto-completed — skip all remaining notify_at for this task
                    logger.info(
                        "task %s auto-completed via source check, skipping notification", t.id
                    )
                    break
                mins = (
                    max(0, int((t.due_at - now).total_seconds() // 60))
                    if t.due_at
                    else None
                )
                try:
                    notify_task_reminder(user, t, mins)
                    newly.append(dt_str)
                except Exception as exc:
                    logger.error("failed to notify task %s: %s", t.id, exc)
            if newly:
                # Reassign — never mutate JSON in-place (SQLAlchemy won't detect the change)
                t.notifications_sent = [*sent, *newly]
                t.updated_at = now
                fired = True
        if fired:
            db.commit()

        # Pass 3 — Expire overdue pending tasks
        overdue = db.query(Task).filter(
            Task.status == "pending",
            Task.due_at.isnot(None),
            Task.due_at < now,
        ).all()
        for t in overdue:
            t.status = "expired"
            t.updated_at = now
        if overdue:
            db.commit()

    finally:
        db.close()
