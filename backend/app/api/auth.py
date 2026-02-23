import base64
import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.user import User
from app.models.user_source_setting import UserSourceSetting
from app.services.gmail_connector import GmailConnector, GmailAuthError, GmailAPIError
from app.services.calendar_connector import CalendarConnector, CalendarAuthError, CalendarAPIError
from app.tasks.gmail_tasks import initial_gmail_sync
from app.tasks.calendar_tasks import initial_calendar_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]


def _create_flow() -> Flow:
    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    flow = Flow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = settings.GOOGLE_REDIRECT_URI
    return flow


@router.get("/google")
def auth_google(app_redirect: str | None = Query(None)):
    flow = _create_flow()

    auth_kwargs: dict = dict(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    # If called from the mobile app, encode the mobile redirect URI in OAuth state
    # so the callback can redirect back to the app instead of returning JSON.
    if app_redirect:
        state = base64.urlsafe_b64encode(app_redirect.encode()).decode().rstrip("=")
        auth_kwargs["state"] = state

    authorization_url, _ = flow.authorization_url(**auth_kwargs)
    return RedirectResponse(url=authorization_url)


@router.get("/google/callback")
def auth_google_callback(code: str, state: str | None = None, db: Session = Depends(get_db)):
    flow = _create_flow()
    flow.fetch_token(code=code)
    credentials = flow.credentials

    # Fetch user profile from Google
    oauth2_service = build("oauth2", "v2", credentials=credentials)
    user_info = oauth2_service.userinfo().get().execute()

    email = user_info["email"]
    google_id = user_info["id"]
    name = user_info.get("name")

    # Upsert user
    existing = db.query(User).filter(User.email == email).first()
    was_new_user = existing is None
    if was_new_user:
        user = User(email=email, google_id=google_id, name=name)
        db.add(user)
    else:
        user = existing
        user.google_id = google_id
        if name:
            user.name = name

    if credentials.refresh_token:
        user.set_refresh_token(credentials.refresh_token)
    elif was_new_user:
        raise HTTPException(
            status_code=400,
            detail="No refresh token received. Revoke access at https://myaccount.google.com/permissions and try again.",
        )

    db.commit()
    db.refresh(user)

    # Ensure UserSourceSetting rows exist for both sources
    for source_key in ("gmail", "google_calendar"):
        existing_setting = (
            db.query(UserSourceSetting)
            .filter(UserSourceSetting.user_id == user.id, UserSourceSetting.source == source_key)
            .first()
        )
        if not existing_setting:
            db.add(UserSourceSetting(user_id=user.id, source=source_key, enabled=True))
    db.commit()

    # Register Gmail watch
    gmail_setting = (
        db.query(UserSourceSetting)
        .filter(UserSourceSetting.user_id == user.id, UserSourceSetting.source == "gmail")
        .first()
    )
    try:
        connector = GmailConnector(user=user)
        reg = connector.register_watch(topic_name=settings.PUBSUB_TOPIC)
        user.gmail_history_id = reg.history_id
        watch_expiry = datetime.fromtimestamp(reg.expiration_ms / 1000, tz=timezone.utc)
        user.gmail_watch_expiry = watch_expiry
        if gmail_setting:
            gmail_setting.sync_cursor = json.dumps({"history_id": reg.history_id})
            gmail_setting.watch_expiry = watch_expiry
        db.commit()
    except (GmailAuthError, GmailAPIError) as exc:
        logger.warning("failed to register Gmail watch for user %s: %s", user.id, exc)

    # Register Calendar watch
    cal_setting = (
        db.query(UserSourceSetting)
        .filter(UserSourceSetting.user_id == user.id, UserSourceSetting.source == "google_calendar")
        .first()
    )
    try:
        cal_connector = CalendarConnector(user=user)
        channel_id = CalendarConnector.generate_channel_id()
        webhook_base = settings.GOOGLE_REDIRECT_URI.rsplit("/", 2)[0]
        cal_reg = cal_connector.register_watch(
            channel_id=channel_id,
            webhook_url=f"{webhook_base}/webhooks/calendar",
        )
        if cal_setting:
            cal_setting.sync_cursor = json.dumps({"channel_id": channel_id})
            cal_setting.watch_resource_id = cal_reg.resource_id
            cal_setting.watch_expiry = datetime.fromtimestamp(cal_reg.expiration_ms / 1000, tz=timezone.utc)
        db.commit()
    except (CalendarAuthError, CalendarAPIError, ValueError) as exc:
        logger.warning("failed to register Calendar watch for user %s: %s", user.id, exc)

    if was_new_user:
        initial_gmail_sync.delay(user.id)
        initial_calendar_sync.delay(user.id)
        logger.info("enqueued initial syncs for new user %s", user.id)

    # Decode mobile redirect URI from state and redirect back to the app
    if state:
        try:
            padding = "=" * ((4 - len(state) % 4) % 4)
            app_redirect = base64.urlsafe_b64decode(state + padding).decode()
            return RedirectResponse(url=f"{app_redirect}?{urlencode({'user_id': user.id, 'email': user.email})}")
        except Exception:
            logger.warning("auth_google_callback: failed to decode state for redirect")

    return {
        "message": "Google OAuth successful",
        "user_id": user.id,
        "email": user.email,
    }
