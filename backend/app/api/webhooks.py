from __future__ import annotations

import base64
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/gmail")
async def gmail_webhook(
    request: Request,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    """Receive Gmail push notifications from Google Pub/Sub."""
    if token != settings.PUBSUB_VERIFICATION_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid verification token")

    try:
        body = await request.json()
    except Exception:
        # Return 200 so Pub/Sub doesn't retry malformed messages
        logger.warning("gmail_webhook: failed to parse JSON body")
        return {"status": "ok"}

    try:
        message = body.get("message", {})
        data_b64 = message.get("data", "")
        # Standard base64 with padding
        data_bytes = base64.b64decode(data_b64 + "==")
        data = json.loads(data_bytes.decode("utf-8"))
    except Exception as exc:
        logger.warning("gmail_webhook: failed to decode message data: %s", exc)
        return {"status": "ok"}

    email_address = data.get("emailAddress")
    history_id = data.get("historyId")

    if not email_address or not history_id:
        logger.warning("gmail_webhook: missing emailAddress or historyId in payload")
        return {"status": "ok"}

    user = db.query(User).filter(User.email == email_address).first()
    if user is None:
        logger.warning("gmail_webhook: no user found for email %s", email_address)
        return {"status": "ok"}

    from app.tasks.gmail_tasks import process_gmail_notification
    process_gmail_notification.delay(user.id, str(history_id))
    logger.info("gmail_webhook: enqueued notification for user %s, historyId=%s", user.id, history_id)

    # Always return 200; non-2xx causes Pub/Sub to retry infinitely
    return {"status": "ok"}
