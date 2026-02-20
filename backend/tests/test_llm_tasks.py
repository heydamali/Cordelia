"""Tests for app.tasks.llm_tasks.process_conversation_with_llm."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import anthropic
import pytest

from app.services.llm_processor import LLMTask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conversation(id="conv-1"):
    conv = MagicMock()
    conv.id = id
    return conv


def _make_message():
    msg = MagicMock()
    msg.sent_at = MagicMock()
    return msg


def _make_task_obj(task_key="reply-john"):
    t = MagicMock()
    t.task_key = task_key
    return t


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestProcessConversationWithLLM:
    """Unit tests for the process_conversation_with_llm Celery task.

    Calling the task object directly (e.g. ``task("arg1", "arg2")``) invokes
    Celery's ``Task.__call__``, which executes ``run(*args)`` synchronously in
    the current process — no broker required.  This mirrors the pattern used
    in test_gmail_tasks.py.
    """

    @patch("app.tasks.llm_tasks.task_engine")
    @patch("app.tasks.llm_tasks.llm_processor")
    @patch("app.tasks.llm_tasks.SessionLocal")
    def test_happy_path(self, mock_session_local, mock_llm_processor, mock_task_engine):
        """Happy path: conversation + messages found → process + upsert called."""
        db = MagicMock()
        mock_session_local.return_value = db

        conversation = _make_conversation()
        message = _make_message()
        existing_task = _make_task_obj("existing-key")

        db.query.return_value.filter.return_value.first.return_value = conversation
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [message]
        db.query.return_value.filter.return_value.all.return_value = [existing_task]

        llm_task = LLMTask(
            task_key="reply-john",
            title="Reply to John",
            category="reply",
            priority="high",
        )
        mock_llm_processor.process_conversation.return_value = (
            [llm_task],
            '{"tasks": [...]}',
            {"input_tokens": 10, "output_tokens": 20},
        )
        mock_llm_processor._MODEL = "claude-haiku-4-5-20251001"
        mock_task_engine.upsert_tasks.return_value = [MagicMock()]

        from app.tasks.llm_tasks import process_conversation_with_llm
        process_conversation_with_llm("conv-1", "user-1")

        mock_llm_processor.process_conversation.assert_called_once_with(
            conversation, [message], ["existing-key"]
        )
        mock_task_engine.upsert_tasks.assert_called_once()
        db.close.assert_called_once()

    @patch("app.tasks.llm_tasks.task_engine")
    @patch("app.tasks.llm_tasks.llm_processor")
    @patch("app.tasks.llm_tasks.SessionLocal")
    def test_db_session_always_closed_on_error(
        self, mock_session_local, mock_llm_processor, mock_task_engine
    ):
        """DB session is closed even when an unexpected exception occurs."""
        db = MagicMock()
        mock_session_local.return_value = db
        db.query.side_effect = RuntimeError("unexpected DB error")

        from app.tasks.llm_tasks import process_conversation_with_llm
        with pytest.raises(RuntimeError):
            process_conversation_with_llm("conv-1", "user-1")

        db.close.assert_called_once()

    @patch("app.tasks.llm_tasks.task_engine")
    @patch("app.tasks.llm_tasks.llm_processor")
    @patch("app.tasks.llm_tasks.SessionLocal")
    def test_conversation_not_found_returns_early(
        self, mock_session_local, mock_llm_processor, mock_task_engine
    ):
        """When conversation is not found, return early without calling LLM."""
        db = MagicMock()
        mock_session_local.return_value = db
        db.query.return_value.filter.return_value.first.return_value = None

        from app.tasks.llm_tasks import process_conversation_with_llm
        process_conversation_with_llm("conv-missing", "user-1")

        mock_llm_processor.process_conversation.assert_not_called()
        mock_task_engine.upsert_tasks.assert_not_called()
        db.close.assert_called_once()

    @patch("app.tasks.llm_tasks.task_engine")
    @patch("app.tasks.llm_tasks.llm_processor")
    @patch("app.tasks.llm_tasks.SessionLocal")
    def test_no_messages_skips_llm(
        self, mock_session_local, mock_llm_processor, mock_task_engine
    ):
        """When there are no messages for the conversation, skip LLM call."""
        db = MagicMock()
        mock_session_local.return_value = db

        conversation = _make_conversation()
        db.query.return_value.filter.return_value.first.return_value = conversation
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        from app.tasks.llm_tasks import process_conversation_with_llm
        process_conversation_with_llm("conv-1", "user-1")

        mock_llm_processor.process_conversation.assert_not_called()
        mock_task_engine.upsert_tasks.assert_not_called()
        db.close.assert_called_once()

    @patch("app.tasks.llm_tasks.task_engine")
    @patch("app.tasks.llm_tasks.llm_processor")
    @patch("app.tasks.llm_tasks.SessionLocal")
    def test_anthropic_api_error_triggers_retry(
        self, mock_session_local, mock_llm_processor, mock_task_engine
    ):
        """anthropic.APIError causes the task to call self.retry()."""
        db = MagicMock()
        mock_session_local.return_value = db

        conversation = _make_conversation()
        db.query.return_value.filter.return_value.first.return_value = conversation
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            _make_message()
        ]
        db.query.return_value.filter.return_value.all.return_value = []

        api_error = anthropic.APIConnectionError(request=MagicMock())
        mock_llm_processor.process_conversation.side_effect = api_error

        from app.tasks.llm_tasks import process_conversation_with_llm
        with patch.object(
            process_conversation_with_llm, "retry", side_effect=Exception("retry triggered")
        ):
            with pytest.raises(Exception, match="retry triggered"):
                process_conversation_with_llm("conv-1", "user-1")

        mock_task_engine.upsert_tasks.assert_not_called()
        db.close.assert_called_once()

    @patch("app.tasks.llm_tasks.task_engine")
    @patch("app.tasks.llm_tasks.llm_processor")
    @patch("app.tasks.llm_tasks.SessionLocal")
    def test_value_error_drops_no_retry(
        self, mock_session_local, mock_llm_processor, mock_task_engine
    ):
        """ValueError (parse failure) is logged and dropped — no retry, task completes."""
        db = MagicMock()
        mock_session_local.return_value = db

        conversation = _make_conversation()
        db.query.return_value.filter.return_value.first.return_value = conversation
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            _make_message()
        ]
        db.query.return_value.filter.return_value.all.return_value = []
        mock_llm_processor.process_conversation.side_effect = ValueError("bad JSON")

        from app.tasks.llm_tasks import process_conversation_with_llm
        # Should complete without raising
        process_conversation_with_llm("conv-1", "user-1")

        mock_task_engine.upsert_tasks.assert_not_called()
        db.close.assert_called_once()

    @patch("app.tasks.llm_tasks.task_engine")
    @patch("app.tasks.llm_tasks.llm_processor")
    @patch("app.tasks.llm_tasks.SessionLocal")
    def test_existing_task_keys_passed_to_process_conversation(
        self, mock_session_local, mock_llm_processor, mock_task_engine
    ):
        """Existing task_keys from DB are forwarded to process_conversation for deduplication."""
        db = MagicMock()
        mock_session_local.return_value = db

        conversation = _make_conversation()
        db.query.return_value.filter.return_value.first.return_value = conversation
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
            _make_message()
        ]
        task_a = _make_task_obj("reply-alice")
        task_b = _make_task_obj("schedule-meeting")
        db.query.return_value.filter.return_value.all.return_value = [task_a, task_b]

        mock_llm_processor.process_conversation.return_value = ([], "", {})
        mock_llm_processor._MODEL = "claude-haiku-4-5-20251001"
        mock_task_engine.upsert_tasks.return_value = []

        from app.tasks.llm_tasks import process_conversation_with_llm
        process_conversation_with_llm("conv-1", "user-1")

        call_args = mock_llm_processor.process_conversation.call_args[0]
        existing_keys = call_args[2]
        assert set(existing_keys) == {"reply-alice", "schedule-meeting"}
        db.close.assert_called_once()
