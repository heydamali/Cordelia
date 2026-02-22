from __future__ import annotations

import logging

import anthropic
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.task import Task
from app.services import llm_processor, task_engine

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    name="app.tasks.llm_tasks.process_conversation_with_llm",
)
def process_conversation_with_llm(self, conversation_id: str, user_id: str) -> None:
    """Load a conversation from DB, run LLM extraction, and upsert tasks."""
    db: Session = SessionLocal()
    try:
        conversation = (
            db.query(Conversation).filter(Conversation.id == conversation_id).first()
        )
        if conversation is None:
            logger.warning(
                "process_conversation_with_llm: conversation %s not found", conversation_id
            )
            return

        messages = (
            db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.sent_at.asc())
            .all()
        )
        if not messages:
            logger.warning(
                "process_conversation_with_llm: no messages for conversation %s",
                conversation_id,
            )
            return

        existing_task_keys = [
            t.task_key
            for t in db.query(Task).filter(Task.conversation_id == conversation_id).all()
        ]

        try:
            llm_tasks, raw_text, usage = llm_processor.process_conversation(
                conversation, messages, existing_task_keys
            )
        except ValueError as exc:
            logger.error(
                "process_conversation_with_llm: parse failure for conversation %s: %s",
                conversation_id,
                exc,
            )
            return  # Parse failures won't self-heal; no retry
        except anthropic.APIError as exc:
            logger.warning(
                "process_conversation_with_llm: API error for conversation %s: %s",
                conversation_id,
                exc,
            )
            raise self.retry(exc=exc)

        raw_llm_output = {"text": raw_text, "usage": usage}
        upserted = task_engine.upsert_tasks(
            db=db,
            conversation_id=conversation_id,
            user_id=user_id,
            llm_tasks=llm_tasks,
            raw_llm_output=raw_llm_output,
            llm_model=llm_processor._MODEL,
        )

        logger.info(
            "process_conversation_with_llm: conversation=%s tasks_processed=%d",
            conversation_id,
            len(upserted),
        )

        # Prune conversations that yielded no actionable tasks (spam / promotions).
        # Deleting the Conversation cascades to its Messages via "all, delete-orphan".
        remaining = db.query(Task).filter(Task.conversation_id == conversation_id).count()
        if remaining == 0:
            conversation_obj = (
                db.query(Conversation).filter(Conversation.id == conversation_id).first()
            )
            if conversation_obj:
                db.delete(conversation_obj)
                db.commit()
                logger.info(
                    "process_conversation_with_llm: pruned spam conversation=%s",
                    conversation_id,
                )
    finally:
        db.close()
