from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.schemas.gmail import ThreadListResponseSchema, ThreadDetailResponseSchema
from app.services.gmail_connector import (
    GmailConnector,
    GmailAuthError,
    GmailAPIError,
)

router = APIRouter(prefix="/gmail", tags=["gmail"])


def _get_user(user_id: str, db: Session) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _get_connector(user: User) -> GmailConnector:
    try:
        return GmailConnector(user=user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/threads", response_model=ThreadListResponseSchema)
def list_threads(
    user_id: str = Query(..., description="The authenticated user's ID"),
    max_results: int = Query(20, ge=1, le=100),
    page_token: str | None = Query(None),
    q: str | None = Query(None, description="Gmail search query, e.g. 'is:unread'"),
    label_ids: list[str] = Query(default=["INBOX"], description="Labels to filter by"),
    db: Session = Depends(get_db),
):
    """List threads from the user's inbox (paginated)."""
    user = _get_user(user_id, db)
    connector = _get_connector(user)
    try:
        result = connector.list_threads(
            max_results=max_results,
            page_token=page_token,
            query=q,
            label_ids=label_ids,
        )
    except GmailAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except GmailAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    return ThreadListResponseSchema(
        threads=[
            {"thread_id": t.thread_id, "snippet": t.snippet, "history_id": t.history_id}
            for t in result.threads
        ],
        next_page_token=result.next_page_token,
        result_size_estimate=result.result_size_estimate,
    )


@router.get("/threads/{thread_id}", response_model=ThreadDetailResponseSchema)
def get_thread(
    thread_id: str,
    user_id: str = Query(..., description="The authenticated user's ID"),
    db: Session = Depends(get_db),
):
    """Fetch a full thread by ID with all messages parsed."""
    user = _get_user(user_id, db)
    connector = _get_connector(user)
    try:
        detail = connector.get_thread(thread_id)
    except GmailAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except GmailAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    return ThreadDetailResponseSchema(
        thread_id=detail.thread_id,
        history_id=detail.history_id,
        messages=[
            {
                "message_id": m.message_id,
                "thread_id": m.thread_id,
                "subject": m.subject,
                "sender": {"name": m.sender.name, "email": m.sender.email},
                "to": [{"name": a.name, "email": a.email} for a in m.to],
                "cc": [{"name": a.name, "email": a.email} for a in m.cc],
                "date": m.date,
                "body_plain": m.body_plain,
                "body_html": m.body_html,
                "labels": m.labels,
                "snippet": m.snippet,
            }
            for m in detail.messages
        ],
    )
