"""Tests for POST /webhooks/gmail."""

import base64
import json
from unittest.mock import MagicMock, patch

from app.models.user import User


# ── Helpers ───────────────────────────────────────────────────────────────────


def _encode_data(payload: dict) -> str:
    """Encode a dict as base64 the way Pub/Sub does (no padding)."""
    return base64.b64encode(json.dumps(payload).encode()).decode()


def _pubsub_body(email: str = "user@example.com", history_id: str = "12345") -> dict:
    return {
        "message": {
            "data": _encode_data({"emailAddress": email, "historyId": history_id}),
            "messageId": "pub-msg-1",
        },
        "subscription": "projects/test/subscriptions/gmail-push-sub",
    }


VALID_TOKEN = "test-verification-token"  # matches conftest default


def _make_user(db_session, email: str = "user@example.com") -> User:
    user = User(email=email, google_id="g_webhook")
    user.set_refresh_token("fake-token")
    user.gmail_history_id = "11111"
    db_session.add(user)
    db_session.commit()
    return user


# ── Token verification ────────────────────────────────────────────────────────


def test_missing_token_returns_403(client, db_session):
    _make_user(db_session)
    resp = client.post("/webhooks/gmail", json=_pubsub_body())
    assert resp.status_code == 422  # token query param required


def test_wrong_token_returns_403(client, db_session):
    _make_user(db_session)
    resp = client.post("/webhooks/gmail", params={"token": "wrong-token"}, json=_pubsub_body())
    assert resp.status_code == 403


# ── Happy path ────────────────────────────────────────────────────────────────


def test_valid_notification_enqueues_task(client, db_session):
    user = _make_user(db_session, email="notify@example.com")

    with patch("app.tasks.gmail_tasks.process_gmail_notification") as mock_task:
        resp = client.post(
            "/webhooks/gmail",
            params={"token": VALID_TOKEN},
            json=_pubsub_body(email="notify@example.com", history_id="99999"),
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    mock_task.delay.assert_called_once_with(user.id, "99999")


# ── Graceful degradation (always 200) ────────────────────────────────────────


def test_unknown_email_returns_200(client, db_session):
    resp = client.post(
        "/webhooks/gmail",
        params={"token": VALID_TOKEN},
        json=_pubsub_body(email="nobody@example.com"),
    )
    assert resp.status_code == 200


def test_malformed_json_body_returns_200(client):
    resp = client.post(
        "/webhooks/gmail",
        params={"token": VALID_TOKEN},
        content=b"not-json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200


def test_missing_data_field_returns_200(client, db_session):
    _make_user(db_session)
    resp = client.post(
        "/webhooks/gmail",
        params={"token": VALID_TOKEN},
        json={"message": {}, "subscription": "projects/test/subscriptions/s"},
    )
    assert resp.status_code == 200


def test_missing_email_in_payload_returns_200(client, db_session):
    _make_user(db_session)
    body = {
        "message": {
            "data": _encode_data({"historyId": "12345"}),  # no emailAddress
        },
    }
    resp = client.post("/webhooks/gmail", params={"token": VALID_TOKEN}, json=body)
    assert resp.status_code == 200
