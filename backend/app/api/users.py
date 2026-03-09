import json
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.models.user_source_setting import UserSourceSetting

router = APIRouter(prefix="/users", tags=["users"])


class PushTokenUpdateSchema(BaseModel):
    user_id: str
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


@router.post("/push-token", status_code=200)
def register_push_token(body: PushTokenUpdateSchema, db: Session = Depends(get_db)):
    """Register or update a device push token for APNs notifications."""
    user = db.query(User).filter(User.id == body.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.push_token = body.push_token
    db.commit()
    return {"status": "ok", "user_id": user.id}
