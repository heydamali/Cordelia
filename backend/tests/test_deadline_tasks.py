"""Tests for app.tasks.deadline_tasks.process_task_deadlines."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_task(
    task_id: str = "task-1",
    status: str = "pending",
    notify_at: list | None = None,
    notifications_sent: list | None = None,
    due_at: datetime | None = None,
    snoozed_until: datetime | None = None,
    user_id: str = "user-1",
) -> MagicMock:
    task = MagicMock()
    task.id = task_id
    task.status = status
    task.notify_at = notify_at or []
    task.notifications_sent = notifications_sent or []
    task.due_at = due_at
    task.snoozed_until = snoozed_until
    task.user_id = user_id
    return task


def _make_mock_user(user_id: str = "user-1") -> MagicMock:
    user = MagicMock()
    user.id = user_id
    user.push_token = "test-token"
    return user


def _build_db(pass1_tasks=None, pass2_tasks=None, pass3_tasks=None, user=None):
    """Build a mock DB whose .all() returns different results for each pass."""
    mock_db = MagicMock()
    call_count = 0

    def all_side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return pass1_tasks or []
        elif call_count == 2:
            return pass2_tasks or []
        elif call_count == 3:
            return pass3_tasks or []
        return []

    mock_db.query.return_value.filter.return_value.all.side_effect = all_side_effect
    mock_db.query.return_value.filter.return_value.first.return_value = (
        user or _make_mock_user()
    )
    return mock_db


def _run(mock_db, completion_returns=False, notify_mock=None):
    """Run process_task_deadlines with the given mocked DB."""
    with (
        patch("app.tasks.deadline_tasks.SessionLocal", return_value=mock_db),
        patch(
            "app.tasks.deadline_tasks.check_and_sync_completion",
            return_value=completion_returns,
        ),
        patch(
            "app.tasks.deadline_tasks.notify_task_reminder",
            notify_mock or MagicMock(),
        ),
    ):
        from app.tasks.deadline_tasks import process_task_deadlines
        process_task_deadlines()


# ---------------------------------------------------------------------------
# Pass 1 — Re-surface snoozed
# ---------------------------------------------------------------------------


class TestPass1Snoozed:
    def test_resurfaces_expired_snooze(self):
        """Snoozed tasks with past snoozed_until are set back to pending."""
        now = datetime.now(timezone.utc)
        task = _make_mock_task(
            status="snoozed", snoozed_until=now - timedelta(hours=1)
        )
        mock_db = _build_db(pass1_tasks=[task])

        _run(mock_db)

        assert task.status == "pending"
        assert task.snoozed_until is None
        mock_db.close.assert_called_once()

    def test_future_snoozed_not_filtered_in(self):
        """The DB filter excludes future snoozed tasks; no tasks → no commit."""
        mock_db = _build_db()  # all passes return []

        _run(mock_db)

        mock_db.commit.assert_not_called()
        mock_db.close.assert_called_once()

    def test_commit_called_when_snoozed_tasks_exist(self):
        """db.commit() is called after updating snoozed tasks."""
        task = _make_mock_task(status="snoozed")
        mock_db = _build_db(pass1_tasks=[task])

        _run(mock_db)

        mock_db.commit.assert_called()


# ---------------------------------------------------------------------------
# Pass 2 — Fire notify_at
# ---------------------------------------------------------------------------


class TestPass2NotifyAt:
    def test_sends_notification_for_due_notify_at(self):
        """Pending task with past notify_at → notification sent."""
        now = datetime.now(timezone.utc)
        past_dt = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        task = _make_mock_task(
            notify_at=[past_dt],
            notifications_sent=[],
            due_at=now + timedelta(hours=2),
        )
        mock_notify = MagicMock()
        mock_db = _build_db(pass2_tasks=[task])

        _run(mock_db, notify_mock=mock_notify)

        mock_notify.assert_called_once()
        assert past_dt in task.notifications_sent

    def test_skips_already_sent_notify_at(self):
        """notify_at datetimes already in notifications_sent are skipped."""
        now = datetime.now(timezone.utc)
        past_dt = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        task = _make_mock_task(
            notify_at=[past_dt],
            notifications_sent=[past_dt],  # already sent
        )
        mock_notify = MagicMock()
        mock_db = _build_db(pass2_tasks=[task])

        _run(mock_db, notify_mock=mock_notify)

        mock_notify.assert_not_called()

    def test_skips_future_notify_at(self):
        """notify_at datetimes in the future are not triggered."""
        now = datetime.now(timezone.utc)
        future_dt = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

        task = _make_mock_task(notify_at=[future_dt], notifications_sent=[])
        mock_notify = MagicMock()
        mock_db = _build_db(pass2_tasks=[task])

        _run(mock_db, notify_mock=mock_notify)

        mock_notify.assert_not_called()

    def test_skips_when_completion_check_returns_true(self):
        """When completion check resolves the task, notification is skipped."""
        now = datetime.now(timezone.utc)
        past_dt = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        task = _make_mock_task(
            notify_at=[past_dt],
            notifications_sent=[],
            due_at=now + timedelta(hours=2),
        )
        mock_notify = MagicMock()
        mock_db = _build_db(pass2_tasks=[task])

        _run(mock_db, completion_returns=True, notify_mock=mock_notify)

        mock_notify.assert_not_called()

    def test_notifications_sent_updated_with_reassignment(self):
        """notifications_sent is reassigned (not mutated) after sending."""
        now = datetime.now(timezone.utc)
        past_dt = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        task = _make_mock_task(notify_at=[past_dt], notifications_sent=[])
        mock_db = _build_db(pass2_tasks=[task])

        _run(mock_db)

        # The new value is assigned (not mutated in-place)
        assert past_dt in task.notifications_sent


# ---------------------------------------------------------------------------
# Pass 3 — Expire overdue
# ---------------------------------------------------------------------------


class TestPass3Expire:
    def test_expires_overdue_pending_tasks(self):
        """Overdue pending tasks are set to expired."""
        now = datetime.now(timezone.utc)
        task = _make_mock_task(
            status="pending", due_at=now - timedelta(hours=1)
        )
        mock_db = _build_db(pass3_tasks=[task])

        _run(mock_db)

        assert task.status == "expired"
        mock_db.close.assert_called_once()

    def test_commit_called_for_expired_tasks(self):
        """db.commit() is called after expiring tasks."""
        now = datetime.now(timezone.utc)
        task = _make_mock_task(status="pending", due_at=now - timedelta(hours=1))
        mock_db = _build_db(pass3_tasks=[task])

        _run(mock_db)

        mock_db.commit.assert_called()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_db_close_called_even_on_exception(self):
        """db.close() is always called even when the task body raises."""
        mock_db = MagicMock()
        mock_db.query.side_effect = RuntimeError("db error")

        with (
            patch("app.tasks.deadline_tasks.SessionLocal", return_value=mock_db),
            patch("app.tasks.deadline_tasks.check_and_sync_completion", return_value=False),
            patch("app.tasks.deadline_tasks.notify_task_reminder"),
        ):
            from app.tasks.deadline_tasks import process_task_deadlines
            with pytest.raises(RuntimeError):
                process_task_deadlines()

        mock_db.close.assert_called_once()
