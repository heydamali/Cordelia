"""Tests for app.services.llm_processor."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.services.llm_processor import LLMTask, build_prompt, parse_llm_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conversation(subject: str = "Test Subject") -> MagicMock:
    conv = MagicMock()
    conv.subject = subject
    conv.source = "gmail"
    return conv


def _make_message(is_from_user: bool = False, body: str = "Hello") -> MagicMock:
    msg = MagicMock()
    msg.is_from_user = is_from_user
    msg.sender_handle = "sender@example.com"
    msg.sender_name = "Sender"
    msg.body_text = body
    msg.sent_at = datetime(2026, 2, 20, tzinfo=timezone.utc)
    return msg


# ---------------------------------------------------------------------------
# build_prompt — TODAY injection
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_today_is_first_line(self):
        """TODAY appears as the very first line of the prompt."""
        conv = _make_conversation()
        prompt = build_prompt(conv, [], [])
        first_line = prompt.split("\n")[0]
        assert first_line.startswith("TODAY: ")

    def test_today_is_valid_iso_date(self):
        """The date in TODAY is a valid ISO-format date string."""
        conv = _make_conversation()
        prompt = build_prompt(conv, [], [])
        first_line = prompt.split("\n")[0]
        date_str = first_line.replace("TODAY: ", "")
        parsed = datetime.fromisoformat(date_str)
        assert isinstance(parsed.year, int)

    def test_today_appears_before_subject(self):
        """TODAY line appears before SUBJECT line in the prompt."""
        conv = _make_conversation(subject="My Email Subject")
        prompt = build_prompt(conv, [], [])
        lines = prompt.split("\n")
        today_idx = next(i for i, line in enumerate(lines) if line.startswith("TODAY:"))
        subject_idx = next(i for i, line in enumerate(lines) if line.startswith("SUBJECT:"))
        assert today_idx < subject_idx

    def test_subject_included(self):
        """SUBJECT line includes the conversation subject."""
        conv = _make_conversation(subject="Meeting Tomorrow")
        prompt = build_prompt(conv, [], [])
        assert "SUBJECT: Meeting Tomorrow" in prompt

    def test_existing_task_keys_included(self):
        """EXISTING_TASK_KEYS are listed in the prompt."""
        conv = _make_conversation()
        prompt = build_prompt(conv, [], ["reply-alice", "schedule-meeting"])
        assert "reply-alice" in prompt
        assert "schedule-meeting" in prompt

    def test_messages_appended(self):
        """Message body text appears in the prompt."""
        conv = _make_conversation()
        msg = _make_message(body="Please reply to this email.")
        prompt = build_prompt(conv, [msg], [])
        assert "Please reply to this email." in prompt


# ---------------------------------------------------------------------------
# parse_llm_response — notify_at
# ---------------------------------------------------------------------------


class TestParseLLMResponse:
    def test_notify_at_parsed_from_response(self):
        """notify_at is correctly parsed from LLM JSON response."""
        raw = (
            '{"tasks": [{"task_key": "reply-john", "title": "Reply to John", '
            '"category": "reply", "priority": "high", '
            '"notify_at": ["2026-02-25T08:00:00Z"]}]}'
        )
        resp = parse_llm_response(raw)
        assert len(resp.tasks) == 1
        assert resp.tasks[0].notify_at == ["2026-02-25T08:00:00Z"]

    def test_notify_at_defaults_to_empty_list(self):
        """When notify_at is absent from LLM output, it defaults to []."""
        raw = (
            '{"tasks": [{"task_key": "reply-john", "title": "Reply to John", '
            '"category": "reply", "priority": "high"}]}'
        )
        resp = parse_llm_response(raw)
        assert resp.tasks[0].notify_at == []

    def test_multiple_notify_at(self):
        """Multiple notify_at datetimes are all parsed."""
        raw = (
            '{"tasks": [{"task_key": "task-1", "title": "Task", '
            '"category": "action", "priority": "medium", '
            '"notify_at": ["2026-02-25T08:00:00Z", "2026-02-26T09:00:00Z"]}]}'
        )
        resp = parse_llm_response(raw)
        assert len(resp.tasks[0].notify_at) == 2

    def test_empty_notify_at_for_ignored(self):
        """Ignored tasks with empty notify_at list parse correctly."""
        raw = (
            '{"tasks": [{"task_key": "ignore-newsletter", "title": "Newsletter", '
            '"category": "ignored", "priority": "low", '
            '"ignore_reason": "Promotional", "notify_at": []}]}'
        )
        resp = parse_llm_response(raw)
        assert resp.tasks[0].notify_at == []


# ---------------------------------------------------------------------------
# LLMTask — notify_at field
# ---------------------------------------------------------------------------


class TestLLMTask:
    def test_constructs_without_notify_at_defaults_to_empty_list(self):
        """LLMTask constructed without notify_at defaults to []."""
        task = LLMTask(
            task_key="reply-john",
            title="Reply to John",
            category="reply",
            priority="high",
        )
        assert task.notify_at == []

    def test_constructs_with_notify_at(self):
        """LLMTask accepts notify_at list."""
        task = LLMTask(
            task_key="reply-john",
            title="Reply to John",
            category="reply",
            priority="high",
            notify_at=["2026-02-25T08:00:00Z"],
        )
        assert task.notify_at == ["2026-02-25T08:00:00Z"]

    def test_constructs_with_all_optional_fields_absent(self):
        """LLMTask works with only required fields."""
        task = LLMTask(
            task_key="action-thing",
            title="Do a thing",
            category="action",
            priority="low",
        )
        assert task.summary is None
        assert task.due_at is None
        assert task.ignore_reason is None
        assert task.notify_at == []
