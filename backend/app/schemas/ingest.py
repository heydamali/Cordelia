from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class IngestMessageSchema(BaseModel):
    source_id: str
    sender_name: str | None = None
    sender_handle: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    sent_at: datetime
    is_from_user: bool = False
    raw_metadata: dict | None = None


class IngestRequestSchema(BaseModel):
    source: str
    user_id: str
    conversation_source_id: str
    subject: str | None = None
    messages: list[IngestMessageSchema]
