from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class TaskSchema(BaseModel):
    id: str
    conversation_id: str
    task_key: str
    title: str
    category: str
    priority: str
    summary: str | None
    due_at: datetime | None
    status: str
    ignore_reason: str | None
    source: str
    sources: list[str] | None = None
    created_at: datetime
    updated_at: datetime
    snoozed_until: datetime | None
    notify_at: list
    notifications_sent: list

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def _derive_sources(self):
        if not self.sources:
            self.sources = [self.source]
        return self


class TaskListResponseSchema(BaseModel):
    tasks: list[TaskSchema]
    total: int
    has_more: bool
    offset: int


class TaskStatusUpdateSchema(BaseModel):
    status: Literal["pending", "done", "snoozed", "ignored"]
    snoozed_until: datetime | None = None
