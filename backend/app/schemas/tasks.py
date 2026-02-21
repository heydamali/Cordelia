from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


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
    created_at: datetime
    updated_at: datetime
    snoozed_until: datetime | None
    notify_at: list
    notifications_sent: list

    model_config = ConfigDict(from_attributes=True)


class TaskListResponseSchema(BaseModel):
    tasks: list[TaskSchema]
    total: int


class TaskStatusUpdateSchema(BaseModel):
    status: Literal["pending", "done", "snoozed", "ignored"]
    snoozed_until: datetime | None = None
