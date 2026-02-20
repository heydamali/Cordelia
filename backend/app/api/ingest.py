from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.message import Message
from app.schemas.ingest import IngestRequestSchema
from app.services.ingest_service import ingest
from app.tasks.llm_tasks import process_conversation_with_llm

router = APIRouter(tags=["ingest"])


def _verify_ingest_key(x_ingest_key: str | None = Header(default=None)) -> None:
    if x_ingest_key != settings.INGEST_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Ingest-Key header",
        )


@router.post("/ingest", dependencies=[Depends(_verify_ingest_key)])
def ingest_endpoint(
    payload: IngestRequestSchema,
    db: Session = Depends(get_db),
) -> dict:
    try:
        conversation = ingest(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    process_conversation_with_llm.delay(conversation.id, payload.user_id)

    messages_stored = (
        db.query(Message).filter(Message.conversation_id == conversation.id).count()
    )

    return {
        "conversation_id": conversation.id,
        "messages_stored": messages_stored,
    }
