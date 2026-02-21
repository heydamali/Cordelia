from __future__ import annotations

import logging
from datetime import datetime, timezone

import redis as redis_module
from celery import shared_task
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal
from app.models.user import User
from app.schemas.ingest import IngestMessageSchema, IngestRequestSchema
from app.services.gmail_connector import (
    GmailConnector,
    GmailAuthError,
    GmailAPIError,
)
from app.services.ingest_service import ingest
from app.tasks.llm_tasks import process_conversation_with_llm

logger = logging.getLogger(__name__)


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
        if not user.gmail_history_id:
            logger.warning("process_gmail_notification: user %s has no gmail_history_id", user_id)
            return

        try:
            connector = GmailConnector(user=user)
        except ValueError as exc:
            logger.warning("process_gmail_notification: cannot build connector for user %s: %s", user_id, exc)
            return

        try:
            result = connector.list_history(start_history_id=user.gmail_history_id)
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
                    payload = IngestRequestSchema(
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
                                is_from_user=False,
                                raw_metadata={"labels": msg.labels},
                            )
                            for msg in thread.messages
                        ],
                    )
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

        user.gmail_history_id = result.history_id
        db.commit()
    finally:
        db.close()
        try:
            lock.release()
        except Exception as exc:
            logger.debug("could not release lock for user %s: %s", user_id, exc)


@celery_app.task(name="app.tasks.gmail_tasks.renew_all_watches")
def renew_all_watches() -> None:
    """Renew Gmail push watches for all users with a stored refresh token."""
    db: Session = SessionLocal()
    try:
        users = (
            db.query(User)
            .filter(User.encrypted_refresh_token.isnot(None))
            .all()
        )
        for user in users:
            try:
                connector = GmailConnector(user=user)
                reg = connector.register_watch(topic_name=settings.PUBSUB_TOPIC)
                user.gmail_history_id = reg.history_id
                user.gmail_watch_expiry = datetime.fromtimestamp(
                    reg.expiration_ms / 1000, tz=timezone.utc
                )
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
        user.gmail_history_id = reg.history_id
        user.gmail_watch_expiry = datetime.fromtimestamp(
            reg.expiration_ms / 1000, tz=timezone.utc
        )
        db.commit()
        logger.info("re-registered Gmail watch for user %s, new historyId=%s", user.id, reg.history_id)
    except (GmailAuthError, GmailAPIError) as exc:
        logger.error("_re_register_watch: failed for user %s: %s", user.id, exc)
        db.rollback()
