from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.task import Task
from app.models.user import User
from app.schemas.tasks import TaskListResponseSchema, TaskSchema, TaskStatusUpdateSchema

router = APIRouter(prefix="/tasks", tags=["tasks"])

_VALID_STATUSES = {"pending", "done", "snoozed", "ignored", "all", "expired"}


def _get_user(user_id: str, db: Session) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("", response_model=TaskListResponseSchema)
def list_tasks(
    user_id: str = Query(..., description="The authenticated user's ID"),
    status: str = Query("pending", description="Filter by status: pending/done/snoozed/ignored/expired/all"),
    category: str | None = Query(None, description="Filter by category, e.g. reply, appointment"),
    priority: str | None = Query(None, description="Filter by priority: high/medium/low"),
    db: Session = Depends(get_db),
):
    """List tasks for a user, sorted by priority then due date."""
    if status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status {status!r}. Must be one of: {', '.join(sorted(_VALID_STATUSES))}",
        )

    _get_user(user_id, db)

    priority_rank = case({"high": 1, "medium": 2, "low": 3}, value=Task.priority)

    query = db.query(Task).filter(Task.user_id == user_id)

    if status != "all":
        query = query.filter(Task.status == status)

    if category is not None:
        query = query.filter(Task.category == category)

    if priority is not None:
        query = query.filter(Task.priority == priority)

    tasks = (
        query.order_by(
            priority_rank,
            Task.due_at.asc().nullslast(),
            Task.created_at.asc(),
        )
        .all()
    )

    return TaskListResponseSchema(tasks=tasks, total=len(tasks))


@router.patch("/{task_id}", response_model=TaskSchema)
def update_task_status(
    task_id: str,
    body: TaskStatusUpdateSchema,
    user_id: str = Query(..., description="The authenticated user's ID"),
    db: Session = Depends(get_db),
):
    """Update the status of a task (ownership-checked)."""
    task = db.query(Task).filter(Task.id == task_id).first()

    if task is None or task.user_id != user_id:
        raise HTTPException(status_code=404, detail="Task not found")

    task.status = body.status
    task.updated_at = datetime.now(timezone.utc)

    if body.status == "snoozed" and body.snoozed_until is not None:
        task.snoozed_until = body.snoozed_until
    elif body.status != "snoozed":
        task.snoozed_until = None   # clear when transitioning away from snoozed

    db.commit()
    db.refresh(task)

    return task
