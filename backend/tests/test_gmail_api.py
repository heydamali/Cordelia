"""Integration tests for the Gmail FastAPI endpoints.

Uses the shared client/db_session fixtures from conftest.py.
Mocks at the GmailConnector level to avoid any Google API calls.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.models.user import User
from app.services.gmail_connector import (
    EmailAddress,
    GmailAPIError,
    GmailAuthError,
    ParsedMessage,
    ThreadDetail,
    ThreadListResult,
    ThreadSummary,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_user(db_session, email: str = "test@example.com") -> User:
    user = User(email=email, google_id="g_test")
    user.set_refresh_token("fake-refresh-token")
    db_session.add(user)
    db_session.commit()
    return user


def _make_parsed_message(
    msg_id: str = "msg_1",
    thread_id: str = "thread_1",
    subject: str = "Hello",
    body_plain: str = "Body text",
) -> ParsedMessage:
    return ParsedMessage(
        message_id=msg_id,
        thread_id=thread_id,
        subject=subject,
        sender=EmailAddress(name="Alice", email="alice@example.com"),
        to=[EmailAddress(name="Bob", email="bob@example.com")],
        cc=[],
        date=datetime(2023, 11, 14, 12, 0, 0, tzinfo=timezone.utc),
        body_plain=body_plain,
        body_html="<p>Body text</p>",
        labels=["INBOX"],
        snippet=body_plain[:50],
    )


def _make_thread_list_result(count: int = 2) -> ThreadListResult:
    return ThreadListResult(
        threads=[
            ThreadSummary(thread_id=f"t{i}", snippet=f"Snip {i}", history_id=f"h{i}")
            for i in range(count)
        ],
        next_page_token=None,
        result_size_estimate=count,
    )


# ── List threads endpoint ─────────────────────────────────────────────────────


def test_list_threads_ok(client, db_session):
    user = _make_user(db_session)
    mock_result = _make_thread_list_result(count=2)

    with patch("app.api.gmail.GmailConnector") as MockConnector:
        MockConnector.return_value.list_threads.return_value = mock_result
        resp = client.get("/gmail/threads", params={"user_id": user.id})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["threads"]) == 2
    assert data["threads"][0]["thread_id"] == "t0"
    assert data["next_page_token"] is None
    assert data["result_size_estimate"] == 2


def test_list_threads_with_pagination(client, db_session):
    user = _make_user(db_session, email="page@example.com")
    mock_result = ThreadListResult(
        threads=[ThreadSummary(thread_id="t0", snippet="S", history_id="h")],
        next_page_token="next_tok",
        result_size_estimate=50,
    )

    with patch("app.api.gmail.GmailConnector") as MockConnector:
        MockConnector.return_value.list_threads.return_value = mock_result
        resp = client.get(
            "/gmail/threads",
            params={"user_id": user.id, "max_results": 1, "page_token": "prev_tok"},
        )

    assert resp.status_code == 200
    assert resp.json()["next_page_token"] == "next_tok"
    MockConnector.return_value.list_threads.assert_called_once_with(
        max_results=1, page_token="prev_tok", query=None, label_ids=["INBOX"]
    )


def test_list_threads_user_not_found(client):
    resp = client.get("/gmail/threads", params={"user_id": "nonexistent"})
    assert resp.status_code == 404


def test_list_threads_no_refresh_token(client, db_session):
    user = User(email="notoken@example.com", google_id="g_notoken")
    # deliberately do NOT call set_refresh_token
    db_session.add(user)
    db_session.commit()

    resp = client.get("/gmail/threads", params={"user_id": user.id})
    assert resp.status_code == 400


def test_list_threads_auth_error(client, db_session):
    user = _make_user(db_session, email="authfail@example.com")

    with patch("app.api.gmail.GmailConnector") as MockConnector:
        MockConnector.return_value.list_threads.side_effect = GmailAuthError("token revoked")
        resp = client.get("/gmail/threads", params={"user_id": user.id})

    assert resp.status_code == 401
    assert "revoked" in resp.json()["detail"]


def test_list_threads_api_error(client, db_session):
    user = _make_user(db_session, email="apierr@example.com")

    with patch("app.api.gmail.GmailConnector") as MockConnector:
        MockConnector.return_value.list_threads.side_effect = GmailAPIError(429, "rate limited")
        resp = client.get("/gmail/threads", params={"user_id": user.id})

    assert resp.status_code == 429


# ── Get thread endpoint ───────────────────────────────────────────────────────


def test_get_thread_ok(client, db_session):
    user = _make_user(db_session, email="getthread@example.com")
    msg = _make_parsed_message()
    mock_detail = ThreadDetail(
        thread_id="thread_1", messages=[msg], history_id="h1"
    )

    with patch("app.api.gmail.GmailConnector") as MockConnector:
        MockConnector.return_value.get_thread.return_value = mock_detail
        resp = client.get("/gmail/threads/thread_1", params={"user_id": user.id})

    assert resp.status_code == 200
    data = resp.json()
    assert data["thread_id"] == "thread_1"
    assert len(data["messages"]) == 1
    m = data["messages"][0]
    assert m["subject"] == "Hello"
    assert m["sender"]["email"] == "alice@example.com"
    assert m["body_plain"] == "Body text"
    assert m["labels"] == ["INBOX"]


def test_get_thread_user_not_found(client):
    resp = client.get("/gmail/threads/thread_1", params={"user_id": "nonexistent"})
    assert resp.status_code == 404


def test_get_thread_auth_error(client, db_session):
    user = _make_user(db_session, email="threadauth@example.com")

    with patch("app.api.gmail.GmailConnector") as MockConnector:
        MockConnector.return_value.get_thread.side_effect = GmailAuthError("expired")
        resp = client.get("/gmail/threads/t1", params={"user_id": user.id})

    assert resp.status_code == 401


def test_get_thread_not_found(client, db_session):
    user = _make_user(db_session, email="thread404@example.com")

    with patch("app.api.gmail.GmailConnector") as MockConnector:
        MockConnector.return_value.get_thread.side_effect = GmailAPIError(404, "Thread not found")
        resp = client.get("/gmail/threads/bad_id", params={"user_id": user.id})

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()
