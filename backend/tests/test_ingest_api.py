"""Tests for POST /ingest endpoint."""

from __future__ import annotations

import uuid

import pytest

from app.models.user import User

INGEST_KEY = "test-ingest-api-key"


def _make_user(db_session) -> User:
    user = User(
        id=str(uuid.uuid4()),
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        name="Test User",
    )
    db_session.add(user)
    db_session.commit()
    return user


# ---------------------------------------------------------------------------
# Auth guard tests
# ---------------------------------------------------------------------------


def test_ingest_missing_key_returns_401(client):
    resp = client.post("/ingest", json={})
    assert resp.status_code == 401


def test_ingest_wrong_key_returns_401(client):
    resp = client.post(
        "/ingest",
        json={},
        headers={"X-Ingest-Key": "wrong-key"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_ingest_creates_conversation_and_messages(client, db_session):
    user = _make_user(db_session)

    payload = {
        "source": "gmail",
        "user_id": user.id,
        "conversation_source_id": "thread-abc",
        "subject": "Hello World",
        "messages": [
            {
                "source_id": "msg-1",
                "sender_name": "Alice",
                "sender_handle": "alice@example.com",
                "body_text": "Hi there!",
                "sent_at": "2026-02-19T10:00:00Z",
                "is_from_user": False,
            }
        ],
    }

    resp = client.post(
        "/ingest",
        json=payload,
        headers={"X-Ingest-Key": INGEST_KEY},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert "conversation_id" in data
    assert data["messages_stored"] == 1


def test_ingest_idempotent_on_duplicate_messages(client, db_session):
    user = _make_user(db_session)

    payload = {
        "source": "gmail",
        "user_id": user.id,
        "conversation_source_id": "thread-dup",
        "messages": [
            {
                "source_id": "msg-dup-1",
                "sent_at": "2026-02-19T10:00:00Z",
            }
        ],
    }

    # First ingest
    r1 = client.post("/ingest", json=payload, headers={"X-Ingest-Key": INGEST_KEY})
    assert r1.status_code == 200
    conv_id = r1.json()["conversation_id"]

    # Second ingest — same messages, same conversation
    r2 = client.post("/ingest", json=payload, headers={"X-Ingest-Key": INGEST_KEY})
    assert r2.status_code == 200
    assert r2.json()["conversation_id"] == conv_id
    # Total stored is still 1 (duplicate skipped)
    assert r2.json()["messages_stored"] == 1


def test_ingest_unknown_user_returns_404(client):
    payload = {
        "source": "gmail",
        "user_id": str(uuid.uuid4()),
        "conversation_source_id": "thread-xyz",
        "messages": [],
    }

    resp = client.post(
        "/ingest",
        json=payload,
        headers={"X-Ingest-Key": INGEST_KEY},
    )

    assert resp.status_code == 404
