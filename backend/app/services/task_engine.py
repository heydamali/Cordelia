from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from dateutil import parser as dateutil_parser
from sqlalchemy.orm import Session

from app.models.task import Task
from app.services.llm_processor import LLMTask

logger = logging.getLogger(__name__)

_PRIORITY_RANK: dict[str, int] = {"high": 3, "medium": 2, "low": 1}


def _parse_due_at(due_at_str: str | None) -> datetime | None:
    if not due_at_str:
        return None
    try:
        dt = dateutil_parser.parse(due_at_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, OverflowError):
        logger.warning("task_engine: could not parse due_at=%r", due_at_str)
        return None


def upsert_tasks(
    db: Session,
    conversation_id: str,
    user_id: str,
    llm_tasks: list[LLMTask],
    raw_llm_output: Any,
    llm_model: str,
) -> list[Task]:
    """Idempotently upsert LLM tasks into the DB, applying priority/status rules.

    Upsert rules:
    - No existing row  → INSERT; status=ignored if category=ignored, else pending
    - pending          → UPDATE title/summary/due_at/notify_at; priority only bumps UP
    - done / snoozed   → UPDATE llm_model + raw_llm_output only
    - ignored          → no-op, skip entirely
    """
    now = datetime.now(timezone.utc)

    # Load all existing tasks for this conversation in one query
    existing_rows: dict[str, Task] = {
        t.task_key: t
        for t in db.query(Task).filter(Task.conversation_id == conversation_id).all()
    }

    results: list[Task] = []

    for llm_task in llm_tasks:
        existing = existing_rows.get(llm_task.task_key)

        if existing is None:
            status = "ignored" if llm_task.category == "ignored" else "pending"
            task = Task(
                user_id=user_id,
                conversation_id=conversation_id,
                task_key=llm_task.task_key,
                title=llm_task.title,
                category=llm_task.category,
                priority=llm_task.priority,
                summary=llm_task.summary,
                due_at=_parse_due_at(llm_task.due_at),
                status=status,
                ignore_reason=llm_task.ignore_reason,
                llm_model=llm_model,
                raw_llm_output=raw_llm_output,
                notify_at=llm_task.notify_at,
                notifications_sent=[],
                created_at=now,
                updated_at=now,
            )
            db.add(task)
            results.append(task)

        elif existing.status == "ignored":
            # No-op — skip entirely
            continue

        elif existing.status in ("done", "snoozed"):
            # UPDATE llm_model + raw_llm_output only; leave priority/title untouched
            existing.llm_model = llm_model
            existing.raw_llm_output = raw_llm_output
            existing.updated_at = now
            results.append(existing)

        else:
            # pending — UPDATE title, summary, due_at, notify_at; priority only bumps UP
            existing.title = llm_task.title
            existing.summary = llm_task.summary
            existing.due_at = _parse_due_at(llm_task.due_at)
            existing.notify_at = llm_task.notify_at   # LLM may have better date info on re-run
            existing.llm_model = llm_model
            existing.raw_llm_output = raw_llm_output
            existing.updated_at = now
            old_rank = _PRIORITY_RANK.get(existing.priority, 0)
            new_rank = _PRIORITY_RANK.get(llm_task.priority, 0)
            if new_rank > old_rank:
                existing.priority = llm_task.priority
            results.append(existing)

    db.commit()
    return results
