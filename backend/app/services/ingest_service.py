from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.ingest import IngestRequestSchema

logger = logging.getLogger(__name__)


def ingest(db: Session, payload: IngestRequestSchema) -> Conversation:
    """Upsert a conversation and its messages from an ingest payload.

    Returns the (possibly newly created) Conversation row.
    Raises ValueError if the referenced user does not exist.
    """
    user = db.query(User).filter(User.id == payload.user_id).first()
    if user is None:
        raise ValueError(f"User {payload.user_id!r} not found")

    # --- upsert conversation ---
    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.user_id == payload.user_id,
            Conversation.source == payload.source,
            Conversation.source_id == payload.conversation_source_id,
        )
        .first()
    )

    now = datetime.now(timezone.utc)

    if conversation is None:
        conversation = Conversation(
            user_id=payload.user_id,
            source=payload.source,
            source_id=payload.conversation_source_id,
            subject=payload.subject,
            created_at=now,
            updated_at=now,
        )
        db.add(conversation)
        db.flush()  # get conversation.id before inserting messages

    # Update snippet and last_message_at from the most recent message
    if payload.messages:
        latest = max(payload.messages, key=lambda m: m.sent_at)
        conversation.snippet = (latest.body_text or "")[:200] or None
        conversation.last_message_at = latest.sent_at
    conversation.updated_at = now

    # --- upsert messages ---
    stored = 0
    for msg_schema in payload.messages:
        existing = (
            db.query(Message)
            .filter(
                Message.source == payload.source,
                Message.source_id == msg_schema.source_id,
            )
            .first()
        )
        if existing is not None:
            continue  # idempotent — skip duplicates

        message = Message(
            conversation_id=conversation.id,
            user_id=payload.user_id,
            source=payload.source,
            source_id=msg_schema.source_id,
            sender_name=msg_schema.sender_name,
            sender_handle=msg_schema.sender_handle,
            body_text=msg_schema.body_text,
            body_html=msg_schema.body_html,
            sent_at=msg_schema.sent_at,
            is_from_user=msg_schema.is_from_user,
            raw_metadata=msg_schema.raw_metadata,
            created_at=now,
        )
        db.add(message)
        stored += 1

    db.commit()
    db.refresh(conversation)
    logger.debug(
        "ingest: conversation=%s source=%s stored %d new messages",
        conversation.id,
        payload.source,
        stored,
    )
    return conversation
