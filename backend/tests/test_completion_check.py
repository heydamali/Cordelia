"""Tests for app.services.completion_check."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.services.gmail_connector import GmailAuthError, GmailAPIError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    task_id: str = "task-1",
    conversation_id: str = "conv-1",
    user_id: str = "user-1",
    category: str = "reply",
    created_at: datetime | None = None,
) -> MagicMock:
    task = MagicMock()
    task.id = task_id
    task.conversation_id = conversation_id
    task.user_id = user_id
    task.category = category
    task.title = "Reply to John"
    task.summary = "John needs a response."
    task.due_at = None
    task.status = "pending"
    task.created_at = created_at or datetime(2026, 2, 20, tzinfo=timezone.utc)
    return task


def _make_conversation(
    conv_id: str = "conv-1",
    source: str = "gmail",
    source_id: str = "thread-1",
) -> MagicMock:
    conv = MagicMock()
    conv.id = conv_id
    conv.source = source
    conv.source_id = source_id
    return conv


def _make_user(user_id: str = "user-1") -> MagicMock:
    user = MagicMock()
    user.id = user_id
    return user


def _make_message(
    is_from_user: bool = True,
    sent_at: datetime | None = None,
    body: str = "I'll be there Thursday.",
) -> MagicMock:
    msg = MagicMock()
    msg.is_from_user = is_from_user
    msg.sent_at = sent_at or datetime(2026, 2, 21, tzinfo=timezone.utc)
    msg.sender_handle = "user@example.com"
    msg.sender_name = "User"
    msg.body_text = body
    return msg


def _make_mock_db(
    conversation: MagicMock | None = None,
    user_messages: list | None = None,
    all_messages: list | None = None,
) -> MagicMock:
    """Build a mock DB with configurable query results for each chain variant."""
    mock_db = MagicMock()
    # db.query(...).filter(...).first() → conversation
    mock_db.query.return_value.filter.return_value.first.return_value = conversation
    # db.query(...).filter(...).all() → user_messages_after
    mock_db.query.return_value.filter.return_value.all.return_value = user_messages or []
    # db.query(...).filter(...).order_by(...).all() → all_messages
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = (
        all_messages or []
    )
    return mock_db


def _make_llm_response(resolved: bool, reason: str = "") -> MagicMock:
    content = MagicMock()
    content.text = json.dumps({"resolved": resolved, "reason": reason})
    response = MagicMock()
    response.content = [content]
    return response


# ---------------------------------------------------------------------------
# _refresh_from_source
# ---------------------------------------------------------------------------


class TestRefreshFromSource:
    def test_gmail_source_calls_connector_and_ingest(self):
        """Gmail source triggers GmailConnector.get_thread and ingest."""
        conv = _make_conversation(source="gmail", source_id="thread-1")
        user = _make_user()
        mock_db = MagicMock()

        mock_thread = MagicMock()
        mock_thread.thread_id = "thread-1"
        mock_thread.messages = []

        mock_connector = MagicMock()
        mock_connector.get_thread.return_value = mock_thread

        with (
            patch(
                "app.services.completion_check.GmailConnector",
                return_value=mock_connector,
            ),
            patch("app.services.completion_check.ingest") as mock_ingest,
        ):
            from app.services.completion_check import _refresh_from_source
            _refresh_from_source(conv, user, mock_db)

        mock_connector.get_thread.assert_called_once_with("thread-1")
        mock_ingest.assert_called_once()

    def test_non_gmail_source_skips_connector(self):
        """Non-Gmail source skips GmailConnector entirely."""
        conv = _make_conversation(source="other", source_id="id-1")
        user = _make_user()
        mock_db = MagicMock()

        with (
            patch(
                "app.services.completion_check.GmailConnector"
            ) as mock_connector_cls,
            patch("app.services.completion_check.ingest") as mock_ingest,
        ):
            from app.services.completion_check import _refresh_from_source
            _refresh_from_source(conv, user, mock_db)

        mock_connector_cls.assert_not_called()
        mock_ingest.assert_not_called()

    def test_gmail_auth_error_falls_through_silently(self):
        """GmailAuthError during refresh is caught; function does not raise."""
        conv = _make_conversation(source="gmail")
        user = _make_user()
        mock_db = MagicMock()

        with (
            patch(
                "app.services.completion_check.GmailConnector",
                side_effect=GmailAuthError("revoked"),
            ),
            patch("app.services.completion_check.ingest") as mock_ingest,
        ):
            from app.services.completion_check import _refresh_from_source
            _refresh_from_source(conv, user, mock_db)  # must not raise

        mock_ingest.assert_not_called()

    def test_gmail_api_error_falls_through_silently(self):
        """GmailAPIError during refresh is caught; function does not raise."""
        conv = _make_conversation(source="gmail")
        user = _make_user()
        mock_db = MagicMock()

        with (
            patch(
                "app.services.completion_check.GmailConnector",
                side_effect=GmailAPIError(500, "server error"),
            ),
            patch("app.services.completion_check.ingest") as mock_ingest,
        ):
            from app.services.completion_check import _refresh_from_source
            _refresh_from_source(conv, user, mock_db)  # must not raise

        mock_ingest.assert_not_called()


# ---------------------------------------------------------------------------
# check_and_sync_completion
# ---------------------------------------------------------------------------


class TestCheckAndSyncCompletion:
    def test_no_user_messages_returns_false_no_llm(self):
        """No user messages after task creation → returns False without calling LLM."""
        task = _make_task()
        user = _make_user()
        conv = _make_conversation()
        mock_db = _make_mock_db(conversation=conv, user_messages=[])

        with (
            patch("app.services.completion_check.GmailConnector"),
            patch("app.services.completion_check.ingest"),
            patch("app.services.completion_check.anthropic") as mock_anthropic,
        ):
            from app.services.completion_check import check_and_sync_completion
            result = check_and_sync_completion(task, user, mock_db)

        assert result is False
        mock_anthropic.Anthropic.assert_not_called()

    def test_user_reply_llm_resolved_true_returns_true_and_closes_task(self):
        """User reply + LLM resolved=true → task.status=done, returns True."""
        task = _make_task()
        user = _make_user()
        conv = _make_conversation()
        user_msg = _make_message(is_from_user=True, body="I'll be there Thursday.")
        all_msgs = [_make_message(is_from_user=False, body="Are you coming?"), user_msg]
        mock_db = _make_mock_db(
            conversation=conv, user_messages=[user_msg], all_messages=all_msgs
        )

        llm_resp = _make_llm_response(resolved=True, reason="User confirmed attendance")

        with (
            patch("app.services.completion_check.GmailConnector"),
            patch("app.services.completion_check.ingest"),
            patch("app.services.completion_check.anthropic") as mock_anthropic,
        ):
            mock_anthropic.Anthropic.return_value.messages.create.return_value = llm_resp
            from app.services.completion_check import check_and_sync_completion
            result = check_and_sync_completion(task, user, mock_db)

        assert result is True
        assert task.status == "done"
        mock_db.commit.assert_called()

    def test_clarifying_question_llm_resolved_false_returns_false(self):
        """User clarifying question + LLM resolved=false → returns False, task unchanged."""
        task = _make_task()
        user = _make_user()
        conv = _make_conversation()
        user_msg = _make_message(is_from_user=True, body="Which Thursday did you mean?")
        mock_db = _make_mock_db(
            conversation=conv, user_messages=[user_msg], all_messages=[user_msg]
        )

        llm_resp = _make_llm_response(
            resolved=False, reason="User asked a clarifying question"
        )

        with (
            patch("app.services.completion_check.GmailConnector"),
            patch("app.services.completion_check.ingest"),
            patch("app.services.completion_check.anthropic") as mock_anthropic,
        ):
            mock_anthropic.Anthropic.return_value.messages.create.return_value = llm_resp
            from app.services.completion_check import check_and_sync_completion
            result = check_and_sync_completion(task, user, mock_db)

        assert result is False
        assert task.status == "pending"

    def test_gmail_auth_error_on_refresh_falls_through_returns_false(self):
        """GmailAuthError on source refresh → falls through; no user messages → False."""
        task = _make_task()
        user = _make_user()
        conv = _make_conversation()
        mock_db = _make_mock_db(conversation=conv, user_messages=[])

        with (
            patch(
                "app.services.completion_check.GmailConnector",
                side_effect=GmailAuthError("revoked"),
            ),
            patch("app.services.completion_check.ingest"),
            patch("app.services.completion_check.anthropic") as mock_anthropic,
        ):
            from app.services.completion_check import check_and_sync_completion
            result = check_and_sync_completion(task, user, mock_db)  # must not raise

        assert result is False
        mock_anthropic.Anthropic.assert_not_called()

    def test_llm_api_error_returns_false(self):
        """LLM API error → conservative fallback → returns False."""
        task = _make_task()
        user = _make_user()
        conv = _make_conversation()
        user_msg = _make_message(is_from_user=True)
        mock_db = _make_mock_db(
            conversation=conv, user_messages=[user_msg], all_messages=[user_msg]
        )

        with (
            patch("app.services.completion_check.GmailConnector"),
            patch("app.services.completion_check.ingest"),
            patch("app.services.completion_check.anthropic") as mock_anthropic,
        ):
            mock_anthropic.Anthropic.return_value.messages.create.side_effect = Exception(
                "API Error"
            )
            from app.services.completion_check import check_and_sync_completion
            result = check_and_sync_completion(task, user, mock_db)

        assert result is False
        assert task.status == "pending"

    def test_llm_unparseable_json_returns_false(self):
        """LLM returns unparseable JSON → conservative fallback → returns False."""
        task = _make_task()
        user = _make_user()
        conv = _make_conversation()
        user_msg = _make_message(is_from_user=True)
        mock_db = _make_mock_db(
            conversation=conv, user_messages=[user_msg], all_messages=[user_msg]
        )

        bad_content = MagicMock()
        bad_content.text = "not valid json {{{"
        bad_response = MagicMock()
        bad_response.content = [bad_content]

        with (
            patch("app.services.completion_check.GmailConnector"),
            patch("app.services.completion_check.ingest"),
            patch("app.services.completion_check.anthropic") as mock_anthropic,
        ):
            mock_anthropic.Anthropic.return_value.messages.create.return_value = bad_response
            from app.services.completion_check import check_and_sync_completion
            result = check_and_sync_completion(task, user, mock_db)

        assert result is False

    def test_conversation_not_found_returns_false(self):
        """If conversation is not in DB, returns False immediately."""
        task = _make_task()
        user = _make_user()
        mock_db = _make_mock_db(conversation=None)

        with (
            patch("app.services.completion_check.GmailConnector"),
            patch("app.services.completion_check.ingest"),
            patch("app.services.completion_check.anthropic") as mock_anthropic,
        ):
            from app.services.completion_check import check_and_sync_completion
            result = check_and_sync_completion(task, user, mock_db)

        assert result is False
        mock_anthropic.Anthropic.assert_not_called()
