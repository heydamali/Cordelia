from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class EmailAddressSchema(BaseModel):
    name: str
    email: str


class ParsedMessageSchema(BaseModel):
    message_id: str
    thread_id: str
    subject: str
    sender: EmailAddressSchema
    to: list[EmailAddressSchema]
    cc: list[EmailAddressSchema]
    date: datetime
    body_plain: str
    body_html: str
    labels: list[str]
    snippet: str


class ThreadSummarySchema(BaseModel):
    thread_id: str
    snippet: str
    history_id: str


class ThreadListResponseSchema(BaseModel):
    threads: list[ThreadSummarySchema]
    next_page_token: str | None
    result_size_estimate: int


class ThreadDetailResponseSchema(BaseModel):
    thread_id: str
    messages: list[ParsedMessageSchema]
    history_id: str
