import json
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.jwt import get_current_user
from app.config import settings
from app.database import get_db
from app.models.user import User
from app.models.user_source_setting import UserSourceSetting

router = APIRouter(prefix="/users", tags=["users"])


class PushTokenUpdateSchema(BaseModel):
    push_token: str


@router.post("/resync-calendar", status_code=200)
def resync_calendar(
    user_id: str,
    x_api_key: str = Header(alias="X-API-Key"),
    db: Session = Depends(get_db),
):
    """Re-register the calendar watch and kick off a fresh calendar sync for a user.

    Protected by INGEST_API_KEY. Use this to recover from a failed initial sync
    (e.g. the Google Calendar API was disabled at the time of first login).
    """
    if x_api_key != settings.INGEST_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Re-register the calendar watch
    from app.services.calendar_connector import CalendarConnector, CalendarAuthError, CalendarAPIError

    watch_status = "skipped"
    try:
        connector = CalendarConnector(user=user)
        channel_id = CalendarConnector.generate_channel_id()
        parsed = urlparse(settings.GOOGLE_REDIRECT_URI)
        webhook_url = f"{parsed.scheme}://{parsed.netloc}/webhooks/calendar"
        reg = connector.register_watch(channel_id=channel_id, webhook_url=webhook_url)

        cal_setting = (
            db.query(UserSourceSetting)
            .filter(UserSourceSetting.user_id == user.id, UserSourceSetting.source == "google_calendar")
            .first()
        )
        if cal_setting:
            cal_setting.sync_cursor = json.dumps({"channel_id": channel_id})
            cal_setting.watch_resource_id = reg.resource_id
            from datetime import datetime, timezone
            cal_setting.watch_expiry = datetime.fromtimestamp(reg.expiration_ms / 1000, tz=timezone.utc)
        db.commit()
        watch_status = "registered"
    except (CalendarAuthError, CalendarAPIError, ValueError) as exc:
        watch_status = f"failed: {exc}"

    # Enqueue a fresh calendar sync
    from app.tasks.calendar_tasks import initial_calendar_sync
    initial_calendar_sync.delay(user_id)

    return {"status": "ok", "watch": watch_status, "sync": "enqueued"}


@router.post("/backfill-reprocess", status_code=200)
def backfill_reprocess(
    x_api_key: str = Header(alias="X-API-Key"),
    db: Session = Depends(get_db),
):
    """Reprocess all open conversations with the latest LLM logic and renew Gmail watches.

    One-time admin endpoint. Protected by INGEST_API_KEY.
    """
    if x_api_key != settings.INGEST_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")

    from app.models.conversation import Conversation
    from app.models.task import Task
    from app.tasks.llm_tasks import process_conversation_with_llm
    from app.tasks.gmail_tasks import renew_all_watches

    open_convs = (
        db.query(Conversation.id, Conversation.user_id)
        .join(Task, Task.conversation_id == Conversation.id)
        .filter(Task.status.in_(["pending", "snoozed", "missed", "expired"]))
        .distinct()
        .all()
    )

    for conv_id, user_id in open_convs:
        process_conversation_with_llm.delay(conv_id, user_id)

    renew_all_watches.delay()

    return {
        "status": "ok",
        "conversations_enqueued": len(open_convs),
        "watch_renewal": "enqueued",
    }


@router.post("/merge-duplicates", status_code=200)
def merge_duplicates(
    x_api_key: str = Header(alias="X-API-Key"),
    db: Session = Depends(get_db),
):
    """Find and merge duplicate tasks across all users.

    One-time admin endpoint. Protected by INGEST_API_KEY.
    """
    if x_api_key != settings.INGEST_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")

    from app.models.user import User
    from app.services.task_engine import merge_duplicate_tasks

    users = db.query(User).all()
    total = sum(merge_duplicate_tasks(db, u.id) for u in users)
    return {"status": "ok", "merged": total}


@router.post("/push-token", status_code=200)
def register_push_token(
    body: PushTokenUpdateSchema,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Register or update a device push token for APNs notifications."""
    user.push_token = body.push_token
    db.commit()
    return {"status": "ok", "user_id": user.id}
