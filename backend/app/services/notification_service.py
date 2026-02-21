from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.task import Task
    from app.models.user import User

logger = logging.getLogger(__name__)


def send_push_notification(push_token: str, title: str, body: str, data: dict) -> None:
    """APNs stub — logs only. Wire to APNs when credentials available."""
    logger.info("PUSH [%s]: %s — %s | data=%s", push_token, title, body, data)


def notify_task_reminder(user: "User", task: "Task", minutes_until_due: int | None) -> None:
    """Compose and dispatch a task reminder. No-op if user has no push_token."""
    if not user.push_token:
        return
    title = "Urgent: Task reminder" if task.priority == "high" else "Task reminder"
    if minutes_until_due is not None:
        if minutes_until_due <= 60:
            due_str = f"Due in {minutes_until_due} min"
        elif minutes_until_due <= 1440:
            due_str = f"Due in {minutes_until_due // 60}h"
        else:
            due_str = f"Due in {minutes_until_due // 1440}d"
        body = f"{task.title} — {due_str}"
    else:
        body = task.title
    send_push_notification(
        push_token=user.push_token,
        title=title,
        body=body,
        data={
            "task_id": task.id,
            "task_key": task.task_key,
            "category": task.category,
            "priority": task.priority,
        },
    )
