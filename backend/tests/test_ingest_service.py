"""Tests for ingest_service.ingest()."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.ingest import IngestMessageSchema, IngestRequestSchema
from app.services.ingest_service import ingest


def _make_user(db_session) -> User:
    user = User(
        id=str(uuid.uuid4()),
        email=f"svc-test-{uuid.uuid4().hex[:8]}@example.com",
        name="Service Test User",
    )
    db_session.add(user)
    db_session.commit()
    return user


def _make_payload(user_id: str, **overrides) -> IngestRequestSchema:
    defaults = dict(
        source="gmail",
        user_id=user_id,
        conversation_source_id="thread-svc-1",
        subject="Service Test",
        messages=[
            IngestMessageSchema(
                source_id="msg-svc-1",
                sender_name="Bob",
                sender_handle="bob@example.com",
                body_text="Hello from service test",
                sent_at=datetime(2026, 2, 19, 10, 0, 0, tzinfo=timezone.utc),
                is_from_user=False,
                raw_metadata={"labels": ["INBOX"]},
            )
        ],
    )
    defaults.update(overrides)
    return IngestRequestSchema(**defaults)


# ---------------------------------------------------------------------------


def test_ingest_creates_conversation(db_session):
    user = _make_user(db_session)
    payload = _make_payload(user.id)

    conv = ingest(db_session, payload)

    assert isinstance(conv, Conversation)
    assert conv.user_id == user.id
    assert conv.source == "gmail"
    assert conv.source_id == "thread-svc-1"
    assert conv.subject == "Service Test"
    assert conv.snippet == "Hello from service test"


def test_ingest_creates_message(db_session):
    user = _make_user(db_session)
    payload = _make_payload(user.id)

    conv = ingest(db_session, payload)

    msgs = db_session.query(Message).filter(Message.conversation_id == conv.id).all()
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.source_id == "msg-svc-1"
    assert msg.sender_handle == "bob@example.com"
    assert msg.body_text == "Hello from service test"
    assert msg.is_from_user is False
    assert msg.raw_metadata == {"labels": ["INBOX"]}


def test_ingest_upserts_conversation_on_second_call(db_session):
    user = _make_user(db_session)

    # First call
    conv1 = ingest(db_session, _make_payload(user.id))

    # Second call with different message
    payload2 = _make_payload(
        user.id,
        messages=[
            IngestMessageSchema(
                source_id="msg-svc-2",
                body_text="Second message",
                sent_at=datetime(2026, 2, 19, 11, 0, 0, tzinfo=timezone.utc),
            )
        ],
    )
    conv2 = ingest(db_session, payload2)

    assert conv1.id == conv2.id
    # Snippet should reflect the new latest message
    assert conv2.snippet == "Second message"

    # Both messages should exist
    count = db_session.query(Message).filter(Message.conversation_id == conv1.id).count()
    assert count == 2


def test_ingest_skips_duplicate_messages(db_session):
    user = _make_user(db_session)
    payload = _make_payload(user.id)

    ingest(db_session, payload)
    ingest(db_session, payload)  # second call — same message source_id

    count = db_session.query(Message).filter(
        Message.source == "gmail", Message.source_id == "msg-svc-1"
    ).count()
    assert count == 1


def test_ingest_raises_for_unknown_user(db_session):
    payload = _make_payload(str(uuid.uuid4()))

    with pytest.raises(ValueError, match="not found"):
        ingest(db_session, payload)


def test_ingest_sets_last_message_at(db_session):
    user = _make_user(db_session)
    sent = datetime(2026, 2, 19, 10, 0, 0, tzinfo=timezone.utc)
    payload = _make_payload(
        user.id,
        messages=[
            IngestMessageSchema(source_id="msg-ts-1", sent_at=sent)
        ],
    )

    conv = ingest(db_session, payload)

    assert conv.last_message_at is not None
