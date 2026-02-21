from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import anthropic
from sqlalchemy.orm import Session

from app.config import settings
from app.models.conversation import Conversation
from app.models.message import Message
from app.schemas.ingest import IngestMessageSchema, IngestRequestSchema
from app.services.gmail_connector import GmailAPIError, GmailAuthError, GmailConnector
from app.services.ingest_service import ingest

if TYPE_CHECKING:
    from app.models.task import Task
    from app.models.user import User

logger = logging.getLogger(__name__)

_BODY_TRUNCATE = 2000

_COMPLETION_CHECK_SYSTEM = """You are a task completion checker.
Given a task description and an email conversation, determine whether the task has been completed.

A task is complete if the user has taken the specific action required — sent the reply, confirmed the appointment, completed the action.
A task is NOT complete if the user sent a clarifying question, asked for more time, or the exchange is still ongoing.

Respond with raw JSON only:
{"resolved": true, "reason": "User sent reply confirming attendance on Thursday"}
or
{"resolved": false, "reason": "User asked a clarifying question, task still open"}"""


def _refresh_from_source(conversation: Conversation, user: "User", db: Session) -> None:
    """Re-fetch the conversation from its source and ingest any new messages."""
    if conversation.source != "gmail":
        return
    try:
        connector = GmailConnector(user=user)
        thread = connector.get_thread(conversation.source_id)
        payload = IngestRequestSchema(
            source="gmail",
            user_id=user.id,
            conversation_source_id=thread.thread_id,
            subject=thread.messages[0].subject if thread.messages else None,
            messages=[
                IngestMessageSchema(
                    source_id=msg.message_id,
                    sender_name=msg.sender.name,
                    sender_handle=msg.sender.email,
                    body_text=msg.body_plain,
                    body_html=msg.body_html,
                    sent_at=msg.date,
                    is_from_user=False,
                    raw_metadata={"labels": msg.labels},
                )
                for msg in thread.messages
            ],
        )
        ingest(db, payload)
    except (GmailAuthError, GmailAPIError, ValueError) as exc:
        logger.warning(
            "completion_check: source refresh failed for conv %s: %s",
            conversation.id,
            exc,
        )
        # Fall through — check DB-only state


def check_task_resolved(task: "Task", conversation: Conversation, messages: list) -> bool:
    """Call LLM completion judge. Returns True if resolved, False on any error."""
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    lines: list[str] = []
    lines.append(f"TASK: {task.title}")
    lines.append(f"CATEGORY: {task.category}")
    if task.summary:
        lines.append(f"SUMMARY: {task.summary}")
    if task.due_at:
        lines.append(f"DUE_AT: {task.due_at.isoformat()}")
    lines.append("")
    lines.append("CONVERSATION:")

    for msg in messages:
        direction = "USER" if msg.is_from_user else "SENDER"
        sender = msg.sender_handle or msg.sender_name or "unknown"
        sent_at = msg.sent_at.isoformat() if msg.sent_at else "unknown"
        body = (msg.body_text or "").strip()
        if len(body) > _BODY_TRUNCATE:
            body = body[:_BODY_TRUNCATE] + "...[truncated]"
        lines.append(f"[{direction}] From: {sender} | Sent: {sent_at}")
        lines.append(body)
        lines.append("")

    prompt = "\n".join(lines)

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=_COMPLETION_CHECK_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.content[0].text
        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw_text).strip()
        data = json.loads(cleaned)
        return bool(data.get("resolved", False))
    except Exception as exc:
        logger.warning("completion_check: LLM judge failed for task %s: %s", task.id, exc)
        return False  # conservative fallback — never auto-close on uncertainty


def check_and_sync_completion(task: "Task", user: "User", db: Session) -> bool:
    """
    Refresh source + LLM-judge whether task is already complete.
    If resolved: marks task.status = "done", commits, returns True.
    If not resolved or any error: returns False (caller proceeds with notification).
    """
    conversation = db.query(Conversation).filter(Conversation.id == task.conversation_id).first()
    if conversation is None:
        return False

    _refresh_from_source(conversation, user, db)

    # Pre-filter: if no user messages since task creation, skip LLM call
    user_messages_after = db.query(Message).filter(
        Message.conversation_id == conversation.id,
        Message.is_from_user == True,
        Message.sent_at > task.created_at,
    ).all()
    if not user_messages_after:
        return False

    all_messages = db.query(Message).filter(
        Message.conversation_id == conversation.id
    ).order_by(Message.sent_at.asc()).all()

    resolved = check_task_resolved(task, conversation, all_messages)
    if resolved:
        task.status = "done"
        task.updated_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(
            "completion_check: auto-closed task %s (resolved via source check)", task.id
        )
    return resolved
