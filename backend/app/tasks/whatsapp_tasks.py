from __future__ import annotations

import logging

from app.celery_app import celery_app
from app.database import SessionLocal
from app.schemas.ingest import IngestRequestSchema
from app.services.ingest_service import ingest
from app.tasks.llm_tasks import process_conversation_with_llm

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.whatsapp_tasks.ingest_whatsapp_messages")
def ingest_whatsapp_messages(payload_dict: dict) -> None:
    """Ingest WhatsApp messages from the Node.js service and run LLM processing."""
    db = SessionLocal()
    try:
        payload = IngestRequestSchema(**payload_dict)
        conversation = ingest(db, payload)
        process_conversation_with_llm.delay(str(conversation.id), payload.user_id)
        logger.info(
            "ingest_whatsapp_messages: conversation=%s user=%s messages=%d",
            conversation.id,
            payload.user_id,
            len(payload.messages),
        )
    except Exception:
        logger.exception("ingest_whatsapp_messages failed")
        raise
    finally:
        db.close()
