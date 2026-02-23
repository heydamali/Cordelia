from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import redis as redis_module
from celery import shared_task
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal
from app.models.user import User
from app.models.user_source_setting import UserSourceSetting
from app.schemas.ingest import IngestMessageSchema, IngestRequestSchema
from app.services.calendar_connector import (
    CalendarConnector,
    CalendarAuthError,
    CalendarAPIError,
    CalendarEvent,
)
from app.services.ingest_service import ingest
from app.tasks.llm_tasks import process_conversation_with_llm

logger = logging.getLogger(__name__)

_CALENDAR_WEBHOOK_PATH = "/webhooks/calendar"


def _get_calendar_setting(db: Session, user_id: str) -> UserSourceSetting | None:
    return (
        db.query(UserSourceSetting)
        .filter(UserSourceSetting.user_id == user_id, UserSourceSetting.source == "google_calendar")
        .first()
    )


def _get_sync_token(setting: UserSourceSetting | None) -> str | None:
    if setting and setting.sync_cursor:
        try:
            return json.loads(setting.sync_cursor).get("sync_token")
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _set_sync_cursor(setting: UserSourceSetting, sync_token: str | None, channel_id: str | None = None) -> None:
    cursor: dict = {}
    if setting.sync_cursor:
        try:
            cursor = json.loads(setting.sync_cursor)
        except (json.JSONDecodeError, TypeError):
            pass
    if sync_token:
        cursor["sync_token"] = sync_token
    if channel_id:
        cursor["channel_id"] = channel_id
    setting.sync_cursor = json.dumps(cursor)


def _build_calendar_ingest_payload(
    event: CalendarEvent,
    user_id: str,
    user_email: str,
) -> IngestRequestSchema:
    """Map a CalendarEvent to an IngestRequestSchema for the ingest pipeline."""
    # Build a human-readable summary for the LLM
    lines: list[str] = []
    lines.append(f"Event: {event.summary}")
    if event.start:
        lines.append(f"Start: {event.start.isoformat()}")
    if event.end:
        lines.append(f"End: {event.end.isoformat()}")
    if event.start_date and not event.start:
        lines.append(f"Date: {event.start_date} (all day)")
    if event.location:
        lines.append(f"Location: {event.location}")
    if event.organizer_name or event.organizer_email:
        org = event.organizer_name or event.organizer_email
        lines.append(f"Organizer: {org}")
    if event.description:
        desc = event.description[:500]
        lines.append(f"Description: {desc}")
    if event.attendees:
        names = [a.display_name or a.email for a in event.attendees[:10]]
        lines.append(f"Attendees: {', '.join(names)}")

    # Find user's RSVP status
    user_rsvp = "unknown"
    for a in event.attendees:
        if a.self_ or a.email.lower() == user_email.lower():
            user_rsvp = a.response_status
            break

    body_text = "\n".join(lines)

    return IngestRequestSchema(
        source="google_calendar",
        user_id=user_id,
        conversation_source_id=event.event_id,
        subject=event.summary,
        messages=[
            IngestMessageSchema(
                source_id=f"{event.event_id}:{event.updated.isoformat()}",
                sender_name=event.organizer_name or None,
                sender_handle=event.organizer_email or None,
                body_text=body_text,
                sent_at=event.updated,
                is_from_user=(event.organizer_email.lower() == user_email.lower()),
                raw_metadata={
                    "event_id": event.event_id,
                    "start": event.start.isoformat() if event.start else event.start_date,
                    "end": event.end.isoformat() if event.end else event.end_date,
                    "location": event.location,
                    "attendees": [
                        {"email": a.email, "name": a.display_name, "status": a.response_status}
                        for a in event.attendees
                    ],
                    "user_rsvp": user_rsvp,
                    "event_status": event.status,
                    "recurring": bool(event.recurring_event_id),
                },
            ),
        ],
    )


@celery_app.task(name="app.tasks.calendar_tasks.initial_calendar_sync")
def initial_calendar_sync(user_id: str) -> None:
    """Fetch events from -1d to +7d for a new user and ingest them."""
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            logger.warning("initial_calendar_sync: user %s not found", user_id)
            return

        cal_setting = _get_calendar_setting(db, user_id)
        if cal_setting and not cal_setting.enabled:
            logger.info("initial_calendar_sync: calendar disabled for user %s", user_id)
            return

        try:
            connector = CalendarConnector(user=user)
        except ValueError as exc:
            logger.warning("initial_calendar_sync: cannot build connector for user %s: %s", user_id, exc)
            return

        now = datetime.now(timezone.utc)
        time_min = now - timedelta(days=1)
        time_max = now + timedelta(days=7)

        page_token: str | None = None
        event_count = 0
        final_sync_token: str | None = None

        while True:
            try:
                result = connector.list_events(
                    time_min=time_min,
                    time_max=time_max,
                    page_token=page_token,
                )
            except CalendarAuthError as exc:
                logger.error("initial_calendar_sync: auth error for user %s: %s", user_id, exc)
                return
            except CalendarAPIError as exc:
                logger.error("initial_calendar_sync: API error for user %s: %s", user_id, exc)
                return

            for event in result.events:
                if event.status == "cancelled":
                    continue
                try:
                    payload = _build_calendar_ingest_payload(event, user_id, user.email)
                    conversation = ingest(db, payload)
                    process_conversation_with_llm.delay(conversation.id, user_id)
                    event_count += 1
                except Exception as exc:
                    logger.warning(
                        "initial_calendar_sync: failed to ingest event %s for user %s: %s",
                        event.event_id, user_id, exc,
                    )

            if result.next_sync_token:
                final_sync_token = result.next_sync_token

            if result.next_page_token is None:
                break
            page_token = result.next_page_token

        # Store sync token for incremental sync
        if cal_setting and final_sync_token:
            _set_sync_cursor(cal_setting, final_sync_token)
            db.commit()

        logger.info(
            "initial_calendar_sync: complete – processed %d events for user %s",
            event_count, user_id,
        )
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=3, name="app.tasks.calendar_tasks.process_calendar_notification")
def process_calendar_notification(self, user_id: str) -> None:
    """Incremental sync after a calendar push notification."""
    _redis = redis_module.from_url(settings.REDIS_URL)
    lock = _redis.lock(f"cordelia:calendar_lock:{user_id}", timeout=300)

    if not lock.acquire(blocking=False):
        logger.info("calendar lock held for user %s, skipping", user_id)
        return

    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            logger.warning("process_calendar_notification: user %s not found", user_id)
            return

        cal_setting = _get_calendar_setting(db, user_id)
        if cal_setting and not cal_setting.enabled:
            logger.info("process_calendar_notification: calendar disabled for user %s", user_id)
            return

        sync_token = _get_sync_token(cal_setting)
        if not sync_token:
            logger.warning("process_calendar_notification: no sync_token for user %s", user_id)
            return

        try:
            connector = CalendarConnector(user=user)
        except ValueError as exc:
            logger.warning("process_calendar_notification: cannot build connector for user %s: %s", user_id, exc)
            return

        page_token: str | None = None
        event_count = 0
        new_sync_token: str | None = None

        while True:
            try:
                result = connector.list_events(sync_token=sync_token, page_token=page_token)
            except CalendarAPIError as exc:
                if exc.status_code == 410:
                    # Sync token expired — do a full resync
                    logger.warning(
                        "process_calendar_notification: sync token expired for user %s, re-syncing",
                        user_id,
                    )
                    initial_calendar_sync.delay(user_id)
                    return
                logger.error("process_calendar_notification: API error for user %s: %s", user_id, exc)
                raise self.retry(exc=exc)
            except CalendarAuthError as exc:
                logger.error("process_calendar_notification: auth error for user %s: %s", user_id, exc)
                return

            for event in result.events:
                try:
                    payload = _build_calendar_ingest_payload(event, user_id, user.email)
                    conversation = ingest(db, payload)
                    process_conversation_with_llm.delay(conversation.id, user_id)
                    event_count += 1
                except Exception as exc:
                    logger.warning(
                        "process_calendar_notification: failed to ingest event %s for user %s: %s",
                        event.event_id, user_id, exc,
                    )

            if result.next_sync_token:
                new_sync_token = result.next_sync_token

            if result.next_page_token is None:
                break
            page_token = result.next_page_token

        if cal_setting and new_sync_token:
            _set_sync_cursor(cal_setting, new_sync_token)
            db.commit()

        logger.info(
            "process_calendar_notification: processed %d events for user %s",
            event_count, user_id,
        )
    finally:
        db.close()
        try:
            lock.release()
        except Exception as exc:
            logger.debug("could not release calendar lock for user %s: %s", user_id, exc)


@celery_app.task(name="app.tasks.calendar_tasks.renew_all_calendar_watches")
def renew_all_calendar_watches() -> None:
    """Renew Calendar push watches for all users with calendar enabled."""
    db: Session = SessionLocal()
    try:
        enabled_settings = (
            db.query(UserSourceSetting)
            .filter(
                UserSourceSetting.source == "google_calendar",
                UserSourceSetting.enabled.is_(True),
            )
            .all()
        )

        for cal_setting in enabled_settings:
            user = db.query(User).filter(User.id == cal_setting.user_id).first()
            if user is None or not user.encrypted_refresh_token:
                continue

            try:
                connector = CalendarConnector(user=user)

                # Stop old watch if we have a resource_id
                if cal_setting.watch_resource_id:
                    try:
                        old_cursor = json.loads(cal_setting.sync_cursor or "{}")
                        old_channel_id = old_cursor.get("channel_id", "")
                        if old_channel_id:
                            connector.stop_watch(old_channel_id, cal_setting.watch_resource_id)
                    except (CalendarAuthError, CalendarAPIError):
                        pass  # old watch may already be expired

                webhook_url = f"{settings.GOOGLE_REDIRECT_URI.rsplit('/', 2)[0]}{_CALENDAR_WEBHOOK_PATH}"
                channel_id = CalendarConnector.generate_channel_id()
                reg = connector.register_watch(channel_id=channel_id, webhook_url=webhook_url)

                cal_setting.watch_resource_id = reg.resource_id
                cal_setting.watch_expiry = datetime.fromtimestamp(reg.expiration_ms / 1000, tz=timezone.utc)
                _set_sync_cursor(cal_setting, None, channel_id=channel_id)
                db.commit()
                logger.info("renewed Calendar watch for user %s", cal_setting.user_id)
            except (CalendarAuthError, CalendarAPIError, ValueError) as exc:
                logger.warning("renew_all_calendar_watches: failed for user %s: %s", cal_setting.user_id, exc)
                db.rollback()
    finally:
        db.close()
