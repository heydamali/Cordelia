from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import redis as redis_module
from celery import shared_task
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal
from app.models.user import User
from app.models.user_source_setting import UserSourceSetting
from app.schemas.ingest import IngestMessageSchema, IngestRequestSchema
from app.services.gmail_connector import (
    GmailConnector,
    GmailAuthError,
    GmailAPIError,
)
from app.services.ingest_service import ingest
from app.tasks.llm_tasks import process_conversation_with_llm

logger = logging.getLogger(__name__)


def _get_gmail_setting(db, user_id: str) -> UserSourceSetting | None:
    """Return the gmail UserSourceSetting row, or None."""
    return (
        db.query(UserSourceSetting)
        .filter(UserSourceSetting.user_id == user_id, UserSourceSetting.source == "gmail")
        .first()
    )


def _get_history_id(setting: UserSourceSetting | None, user: User) -> str | None:
    """Read history_id from UserSourceSetting.sync_cursor, fallback to User.gmail_history_id."""
    if setting and setting.sync_cursor:
        try:
            return json.loads(setting.sync_cursor).get("history_id")
        except (json.JSONDecodeError, TypeError):
            pass
    return user.gmail_history_id


def _set_history_id(setting: UserSourceSetting | None, user: User, history_id: str) -> None:
    """Write history_id to UserSourceSetting.sync_cursor and legacy User field."""
    if setting:
        setting.sync_cursor = json.dumps({"history_id": history_id})
    user.gmail_history_id = history_id


def _build_ingest_payload(thread, user_id: str, user_email: str) -> IngestRequestSchema:
    """Build an IngestRequestSchema from a ThreadDetail."""

    def _recipient_role(msg, user_email: str) -> str:
        """Return 'to', 'cc', or 'other' based on where the user appears."""
        email_lower = user_email.lower()
        if any(addr.email.lower() == email_lower for addr in msg.to):
            return "to"
        if any(addr.email.lower() == email_lower for addr in msg.cc):
            return "cc"
        return "other"

    return IngestRequestSchema(
        source="gmail",
        user_id=user_id,
        conversation_source_id=thread.thread_id,
        subject=thread.messages[0].subject if thread.messages else None,
        messages=[
            IngestMessageSchema(
                source_id=msg.message_id,
                sender_name=msg.sender.name,
                sender_handle=msg.sender.email,
                body_text=msg.body_plain,
                body_html=msg.body_html,
                sent_at=msg.date,
                is_from_user=msg.sender.email.lower() == user_email.lower(),
                raw_metadata={
                    "labels": msg.labels,
                    "recipient_role": _recipient_role(msg, user_email),
                },
            )
            for msg in thread.messages
        ],
    )


@celery_app.task(name="app.tasks.gmail_tasks.initial_gmail_sync")
def initial_gmail_sync(user_id: str) -> None:
    """Fetch the last 1 day of Gmail threads for a brand-new user.

    A single 24h window keeps onboarding fast — calendar covers upcoming
    obligations, and the live webhook flow handles everything going forward.
    """
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            logger.warning("initial_gmail_sync: user %s not found", user_id)
            return

        try:
            connector = GmailConnector(user=user)
        except ValueError as exc:
            logger.warning("initial_gmail_sync: cannot build connector for user %s: %s", user_id, exc)
            return

        seen_thread_ids: set[str] = set()
        try:
            _ingest_window(db, connector, user_id, user.email, "newer_than:1d", seen_thread_ids)
        except GmailAuthError as exc:
            logger.error("initial_gmail_sync: auth error for user %s: %s", user_id, exc)
            return

        logger.info(
            "initial_gmail_sync: complete – processed %d threads for new user %s",
            len(seen_thread_ids), user_id,
        )
    finally:
        db.close()


def _ingest_window(
    db: Session,
    connector: GmailConnector,
    user_id: str,
    user_email: str,
    query: str,
    seen_thread_ids: set[str],
) -> None:
    """Fetch and ingest all threads for *query* that are not already in *seen_thread_ids*.

    Mutates *seen_thread_ids* in-place with every thread encountered (even if its
    individual fetch later fails) so that wider windows never re-process the same thread.

    Raises GmailAuthError so the caller can abort all remaining windows.
    Swallows GmailAPIError (transient / quota) and stops pagination for this window.
    """
    page_token: str | None = None
    while True:
        try:
            result = connector.list_threads(query=query, max_results=50, page_token=page_token)
        except GmailAuthError:
            raise  # propagate — caller must abort
        except GmailAPIError as exc:
            logger.error(
                "_ingest_window: list_threads failed query=%s user=%s: %s",
                query, user_id, exc,
            )
            break

        for summary in result.threads:
            if summary.thread_id in seen_thread_ids:
                continue  # already processed in a narrower window
            seen_thread_ids.add(summary.thread_id)
            try:
                thread = connector.get_thread(summary.thread_id)
                payload = _build_ingest_payload(thread, user_id, user_email)
                conversation = ingest(db, payload)
                process_conversation_with_llm.delay(conversation.id, user_id)
            except (GmailAuthError, GmailAPIError) as exc:
                logger.warning(
                    "_ingest_window: failed to fetch thread %s for user %s: %s",
                    summary.thread_id, user_id, exc,
                )

        if result.next_page_token is None:
            break
        page_token = result.next_page_token


@celery_app.task(bind=True, max_retries=3, name="app.tasks.gmail_tasks.process_gmail_notification")
def process_gmail_notification(self, user_id: str, notification_history_id: str) -> None:
    """Fetch new threads for a user after receiving a Gmail push notification."""
    _redis = redis_module.from_url(settings.REDIS_URL)
    lock = _redis.lock(f"cordelia:gmail_lock:{user_id}", timeout=300)  # 5-min TTL

    if not lock.acquire(blocking=False):
        logger.info("lock held for user %s, skipping", user_id)
        return

    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            logger.warning("process_gmail_notification: user %s not found", user_id)
            return

        gmail_setting = _get_gmail_setting(db, user_id)
        if gmail_setting and not gmail_setting.enabled:
            logger.info("process_gmail_notification: gmail disabled for user %s", user_id)
            return

        history_id = _get_history_id(gmail_setting, user)
        if not history_id:
            logger.warning("process_gmail_notification: user %s has no gmail_history_id", user_id)
            return

        try:
            connector = GmailConnector(user=user)
        except ValueError as exc:
            logger.warning("process_gmail_notification: cannot build connector for user %s: %s", user_id, exc)
            return

        try:
            result = connector.list_history(start_history_id=history_id)
        except GmailAPIError as exc:
            if exc.status_code == 404:
                logger.warning(
                    "process_gmail_notification: historyId too old for user %s, re-registering watch",
                    user_id,
                )
                _re_register_watch(user, db, connector)
                return
            logger.error("process_gmail_notification: GmailAPIError for user %s: %s", user_id, exc)
            raise self.retry(exc=exc)
        except GmailAuthError as exc:
            logger.error("process_gmail_notification: GmailAuthError for user %s: %s", user_id, exc)
            return

        seen_thread_ids: set[str] = set()
        for record in result.records:
            for thread_id in record.thread_ids_added:
                if thread_id in seen_thread_ids:
                    continue
                seen_thread_ids.add(thread_id)
                try:
                    thread = connector.get_thread(thread_id)
                    payload = _build_ingest_payload(thread, user_id, user.email)
                    conversation = ingest(db, payload)
                    logger.info(
                        "stored thread %s for user %s (%d messages)",
                        thread.thread_id,
                        user_id,
                        len(thread.messages),
                    )
                    process_conversation_with_llm.delay(conversation.id, user_id)
                except (GmailAuthError, GmailAPIError) as exc:
                    logger.warning(
                        "process_gmail_notification: failed to fetch thread %s for user %s: %s",
                        thread_id,
                        user_id,
                        exc,
                    )

        _set_history_id(gmail_setting, user, result.history_id)
        db.commit()
    finally:
        db.close()
        try:
            lock.release()
        except Exception as exc:
            logger.debug("could not release lock for user %s: %s", user_id, exc)


@celery_app.task(name="app.tasks.gmail_tasks.renew_all_watches")
def renew_all_watches() -> None:
    """Renew Gmail push watches for all users with Gmail enabled."""
    db: Session = SessionLocal()
    try:
        # Find users with Gmail enabled (or no setting row yet — legacy users)
        enabled_settings = (
            db.query(UserSourceSetting)
            .filter(UserSourceSetting.source == "gmail", UserSourceSetting.enabled.is_(True))
            .all()
        )
        enabled_user_ids = {s.user_id for s in enabled_settings}
        settings_by_user = {s.user_id: s for s in enabled_settings}

        users = (
            db.query(User)
            .filter(
                User.encrypted_refresh_token.isnot(None),
                User.id.in_(enabled_user_ids),
            )
            .all()
        )
        for user in users:
            try:
                connector = GmailConnector(user=user)
                reg = connector.register_watch(topic_name=settings.PUBSUB_TOPIC)
                gmail_setting = settings_by_user.get(user.id)
                _set_history_id(gmail_setting, user, reg.history_id)
                watch_expiry = datetime.fromtimestamp(reg.expiration_ms / 1000, tz=timezone.utc)
                user.gmail_watch_expiry = watch_expiry
                if gmail_setting:
                    gmail_setting.watch_expiry = watch_expiry
                db.commit()
                logger.info("renewed Gmail watch for user %s", user.id)
            except (GmailAuthError, GmailAPIError, ValueError) as exc:
                logger.warning("renew_all_watches: failed for user %s: %s", user.id, exc)
                db.rollback()
    finally:
        db.close()


def _re_register_watch(user: User, db: Session, connector: GmailConnector) -> None:
    """Re-register a Gmail watch after historyId expiry."""
    try:
        reg = connector.register_watch(topic_name=settings.PUBSUB_TOPIC)
        gmail_setting = _get_gmail_setting(db, user.id)
        _set_history_id(gmail_setting, user, reg.history_id)
        watch_expiry = datetime.fromtimestamp(reg.expiration_ms / 1000, tz=timezone.utc)
        user.gmail_watch_expiry = watch_expiry
        if gmail_setting:
            gmail_setting.watch_expiry = watch_expiry
        db.commit()
        logger.info("re-registered Gmail watch for user %s, new historyId=%s", user.id, reg.history_id)
    except (GmailAuthError, GmailAPIError) as exc:
        logger.error("_re_register_watch: failed for user %s: %s", user.id, exc)
        db.rollback()
