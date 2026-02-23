from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.task import Task
from app.models.user import User
from app.models.user_source_setting import UserSourceSetting
from app.schemas.tasks import TaskListResponseSchema, TaskSchema, TaskStatusUpdateSchema

router = APIRouter(prefix="/tasks", tags=["tasks"])

_VALID_STATUSES = {"pending", "done", "snoozed", "ignored", "all", "expired", "missed"}


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
    source: str | None = Query(None, description="Filter by source: gmail, google_calendar"),
    limit: int = Query(20, ge=1, le=100, description="Maximum number of tasks to return"),
    offset: int = Query(0, ge=0, description="Number of tasks to skip"),
    db: Session = Depends(get_db),
):
    """List tasks for a user, sorted by priority then due date."""
    if status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status {status!r}. Must be one of: {', '.join(sorted(_VALID_STATUSES))}",
        )

    _get_user(user_id, db)

    # Auto-transition past-due appointment tasks to missed
    now = datetime.now(timezone.utc)
    overdue_appts = (
        db.query(Task)
        .filter(
            Task.user_id == user_id,
            Task.status == "pending",
            Task.category == "appointment",
            Task.due_at.isnot(None),
            Task.due_at < now,
        )
        .all()
    )
    if overdue_appts:
        for t in overdue_appts:
            t.status = "missed"
            t.updated_at = now
        db.commit()

    priority_rank = case({"high": 1, "medium": 2, "low": 3}, value=Task.priority)

    query = db.query(Task).filter(Task.user_id == user_id)

    if status != "all":
        query = query.filter(Task.status == status)

    if category is not None:
        query = query.filter(Task.category == category)

    if priority is not None:
        query = query.filter(Task.priority == priority)

    if source is not None:
        query = query.filter(Task.source == source)
    else:
        # Auto-filter to tasks from enabled sources only
        enabled_sources = (
            db.query(UserSourceSetting.source)
            .filter(UserSourceSetting.user_id == user_id, UserSourceSetting.enabled.is_(True))
            .all()
        )
        enabled = [row[0] for row in enabled_sources]
        if enabled:
            query = query.filter(Task.source.in_(enabled))

    base_query = query.order_by(
        priority_rank,
        Task.due_at.asc().nullslast(),
        Task.created_at.asc(),
    )

    total = base_query.count()
    tasks_plus_one = base_query.offset(offset).limit(limit + 1).all()
    has_more = len(tasks_plus_one) > limit
    tasks = tasks_plus_one[:limit]

    return TaskListResponseSchema(tasks=tasks, total=total, has_more=has_more, offset=offset)


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
