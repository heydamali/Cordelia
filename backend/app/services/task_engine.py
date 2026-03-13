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

# ── Fuzzy dedup helpers ──────────────────────────────────────────────────

_STOP_WORDS = frozenset({
    "attend", "rsvp", "for", "the", "a", "an", "to", "with", "about",
    "at", "on", "in", "of", "and", "prepare", "appointment", "complete",
    "join", "go", "session", "meeting", "event", "call",
})


def _tokenize(title: str) -> set[str]:
    """Lowercase word tokens, excluding stop words and single chars."""
    return {w for w in title.lower().split() if w not in _STOP_WORDS and len(w) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _dates_close(d1: datetime | None, d2: datetime | None, hours: int = 48) -> bool:
    if d1 is None and d2 is None:
        return True
    if d1 is None or d2 is None:
        return False
    return abs((d1 - d2).total_seconds()) < hours * 3600


def _merge_summaries(existing: str | None, incoming: str | None) -> str | None:
    """Combine two summaries, deduplicating identical lines."""
    if not incoming:
        return existing
    if not existing:
        return incoming
    existing_lines = [l.strip() for l in existing.splitlines() if l.strip()]
    incoming_lines = [l.strip() for l in incoming.splitlines() if l.strip()]
    seen = set(existing_lines)
    merged = list(existing_lines)
    for line in incoming_lines:
        # Strip leading bullet for comparison
        clean = line.lstrip("•-– ").strip()
        if clean and clean not in {l.lstrip("•-– ").strip() for l in seen}:
            merged.append(line)
            seen.add(line)
    if len(merged) > 1:
        # Format as bullet list if multiple lines
        merged = [f"• {l.lstrip('•-– ').strip()}" for l in merged]
    return "\n".join(merged)


def _combine_sources(task_a_source: str, task_b_source: str,
                     task_a_sources: list | None = None,
                     task_b_sources: list | None = None) -> list[str]:
    """Build a deduplicated sources list from two tasks."""
    all_sources: list[str] = []
    for s in (task_a_sources or [task_a_source]) + (task_b_sources or [task_b_source]):
        if s not in all_sources:
            all_sources.append(s)
    return all_sources


def _find_fuzzy_match(
    llm_task: LLMTask,
    parsed_due: datetime | None,
    existing_tasks: dict[str, Task],
) -> str | None:
    """Return the task_key of a fuzzy-matched existing task, or None."""
    new_tokens = _tokenize(llm_task.title)
    action_cats = {"appointment", "action"}
    for key, task in existing_tasks.items():
        if key == llm_task.task_key:
            continue  # exact match handled by normal path
        if _jaccard(new_tokens, _tokenize(task.title)) < 0.4:
            continue
        if not _dates_close(parsed_due, task.due_at):
            continue
        cat_match = (
            task.category == llm_task.category
            or (task.category in action_cats and llm_task.category in action_cats)
        )
        if not cat_match:
            continue
        logger.info(
            "task_engine: fuzzy dedup matched %r → existing %r (%s)",
            llm_task.task_key, key, task.title,
        )
        return key
    return None


# ── Parsing ──────────────────────────────────────────────────────────────


def _parse_due_at(due_at_str: str | None, *, reject_past: bool = False) -> datetime | None:
    if not due_at_str:
        return None
    try:
        dt = dateutil_parser.parse(due_at_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if reject_past and dt < datetime.now(timezone.utc):
            logger.info("task_engine: discarding past due_at=%s", due_at_str)
            return None
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
    source: str = "gmail",
) -> tuple[list[Task], list[Task]]:
    """Idempotently upsert LLM tasks into the DB, applying priority/status rules.

    Returns (upserted, auto_completed) where auto_completed are tasks the LLM
    determined are now resolved.

    Upsert rules:
    - No existing row + ignored category → skip; caller prunes conversation if no tasks remain
    - No existing row + actionable       → INSERT with status=pending
    - pending + resolved=true            → mark status=done (auto-complete)
    - pending          → UPDATE title/summary/due_at/notify_at; priority only bumps UP
    - done / snoozed   → UPDATE llm_model + raw_llm_output only
    - ignored (legacy) → DELETE the row; caller prunes conversation if no tasks remain

    Cross-source deduplication: existing tasks are looked up by (user_id, task_key) so
    that a calendar event about the same appointment as an email thread reuses the same
    task rather than creating a duplicate.
    """
    now = datetime.now(timezone.utc)

    # Load tasks for this conversation first (highest priority for key matching)
    conv_tasks: dict[str, Task] = {
        t.task_key: t
        for t in db.query(Task).filter(Task.conversation_id == conversation_id).all()
    }
    # Also load open tasks for this user from OTHER conversations (cross-source dedup)
    user_tasks: dict[str, Task] = {
        t.task_key: t
        for t in db.query(Task).filter(
            Task.user_id == user_id,
            Task.conversation_id != conversation_id,
            Task.status.in_(["pending", "snoozed", "missed", "expired"]),
        ).all()
    }
    # Conversation-specific tasks take precedence over cross-source matches
    existing_rows: dict[str, Task] = {**user_tasks, **conv_tasks}

    results: list[Task] = []
    auto_completed: list[Task] = []

    for llm_task in llm_tasks:
        existing = existing_rows.get(llm_task.task_key)

        # For email sources, reject due_at values that are already in the past
        # (the LLM likely misresolved a relative date like "this Thursday")
        reject_past = source == "gmail"
        parsed_due = _parse_due_at(llm_task.due_at, reject_past=reject_past)

        # Fuzzy dedup: if no exact key match, try title/date similarity
        if existing is None and llm_task.category != "ignored":
            fuzzy_key = _find_fuzzy_match(llm_task, parsed_due, existing_rows)
            if fuzzy_key:
                existing = existing_rows[fuzzy_key]
                llm_task.task_key = fuzzy_key

        if existing is None:
            if llm_task.category == "ignored":
                continue  # never persist ignored tasks
            task = Task(
                user_id=user_id,
                conversation_id=conversation_id,
                task_key=llm_task.task_key,
                source=source,
                title=llm_task.title,
                category=llm_task.category,
                priority=llm_task.priority,
                summary=llm_task.summary,
                due_at=parsed_due,
                status="pending",
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
            db.delete(existing)  # remove legacy ignored row
            continue

        elif existing.status in ("done", "snoozed"):
            # UPDATE llm_model + raw_llm_output only; leave priority/title untouched
            existing.llm_model = llm_model
            existing.raw_llm_output = raw_llm_output
            existing.updated_at = now
            results.append(existing)

        else:
            # pending / missed / expired — check for LLM-detected resolution first
            if llm_task.resolved:
                existing.status = "done"
                existing.llm_model = llm_model
                existing.raw_llm_output = raw_llm_output
                existing.updated_at = now
                results.append(existing)
                auto_completed.append(existing)
                continue

            # UPDATE title, summary, due_at, notify_at; priority only bumps UP
            existing.title = llm_task.title
            # Preserve calendar-sourced due_at when email re-processes the same task
            is_cross_source = existing.source != source
            if is_cross_source:
                existing.summary = _merge_summaries(existing.summary, llm_task.summary)
                existing.sources = _combine_sources(
                    existing.source, source, existing.sources,
                )
            else:
                existing.summary = llm_task.summary
            if is_cross_source and existing.source == "google_calendar" and existing.due_at:
                pass  # calendar due_at is authoritative (based on event start time)
            else:
                existing.due_at = _parse_due_at(llm_task.due_at, reject_past=reject_past)
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
    return results, auto_completed


def merge_duplicate_tasks(db: Session, user_id: str) -> int:
    """Find and merge duplicate tasks for a user. Returns count of merged tasks."""
    open_tasks = db.query(Task).filter(
        Task.user_id == user_id,
        Task.status.in_(["pending", "snoozed", "missed", "expired"]),
    ).all()

    merged = 0
    seen: dict[str, Task] = {}

    # Sort: calendar tasks first (authoritative source), then by created_at
    for task in sorted(open_tasks, key=lambda t: (t.source != "google_calendar", t.created_at)):
        tokens = _tokenize(task.title)
        matched_key = None
        for key, existing in seen.items():
            if _jaccard(tokens, _tokenize(existing.title)) >= 0.4 and _dates_close(task.due_at, existing.due_at):
                matched_key = key
                break
        if matched_key:
            survivor = seen[matched_key]
            survivor.summary = _merge_summaries(survivor.summary, task.summary)
            survivor.sources = _combine_sources(
                survivor.source, task.source, survivor.sources, task.sources,
            )
            task.status = "done"
            task.updated_at = datetime.now(timezone.utc)
            merged += 1
            logger.info(
                "merge_duplicate_tasks: merged %r into %r for user %s",
                task.task_key, matched_key, user_id,
            )
        else:
            seen[task.task_key] = task

    db.commit()
    return merged
