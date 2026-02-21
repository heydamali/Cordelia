"""Tests for GET /tasks and PATCH /tasks/{task_id} endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

import pytest

from app.models.conversation import Conversation
from app.models.task import Task
from app.models.user import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(db_session) -> User:
    user = User(
        id=str(uuid.uuid4()),
        email=f"tasks-test-{uuid.uuid4().hex[:8]}@example.com",
        name="Tasks Test User",
    )
    db_session.add(user)
    db_session.commit()
    return user


def _make_conversation(db_session, user: User) -> Conversation:
    conv = Conversation(
        id=str(uuid.uuid4()),
        user_id=user.id,
        source="gmail",
        source_id=f"thread-{uuid.uuid4().hex[:8]}",
    )
    db_session.add(conv)
    db_session.commit()
    return conv


def _make_task(
    db_session,
    user: User,
    conv: Conversation,
    *,
    priority: str = "medium",
    status: str = "pending",
    category: str = "reply",
    due_at: datetime | None = None,
    title: str = "Test task",
    notify_at: list | None = None,
    notifications_sent: list | None = None,
    snoozed_until: datetime | None = None,
) -> Task:
    task = Task(
        id=str(uuid.uuid4()),
        user_id=user.id,
        conversation_id=conv.id,
        task_key=f"key-{uuid.uuid4().hex[:8]}",
        title=title,
        category=category,
        priority=priority,
        status=status,
        due_at=due_at,
        llm_model="claude-test",
        notify_at=notify_at if notify_at is not None else [],
        notifications_sent=notifications_sent if notifications_sent is not None else [],
        snoozed_until=snoozed_until,
    )
    db_session.add(task)
    db_session.commit()
    return task


# ---------------------------------------------------------------------------
# GET /tasks — default (pending)
# ---------------------------------------------------------------------------


def test_get_tasks_returns_pending_by_default(client, db_session):
    user = _make_user(db_session)
    conv = _make_conversation(db_session, user)
    task = _make_task(db_session, user, conv, status="pending")
    _make_task(db_session, user, conv, status="done")

    resp = client.get(f"/tasks?user_id={user.id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["tasks"][0]["id"] == task.id
    # Verify required fields present
    t = data["tasks"][0]
    assert "id" in t
    assert "conversation_id" in t
    assert "task_key" in t
    assert "title" in t
    assert "category" in t
    assert "priority" in t
    assert "status" in t
    assert "created_at" in t
    assert "updated_at" in t
    assert "snoozed_until" in t
    assert "notify_at" in t
    assert "notifications_sent" in t
    # Internal audit fields should NOT be present
    assert "raw_llm_output" not in t
    assert "llm_model" not in t


# ---------------------------------------------------------------------------
# GET /tasks — sorting
# ---------------------------------------------------------------------------


def test_get_tasks_sorted_by_priority(client, db_session):
    user = _make_user(db_session)
    conv = _make_conversation(db_session, user)
    low = _make_task(db_session, user, conv, priority="low", title="Low priority")
    med = _make_task(db_session, user, conv, priority="medium", title="Medium priority")
    high = _make_task(db_session, user, conv, priority="high", title="High priority")

    resp = client.get(f"/tasks?user_id={user.id}")

    assert resp.status_code == 200
    titles = [t["title"] for t in resp.json()["tasks"]]
    assert titles.index("High priority") < titles.index("Medium priority")
    assert titles.index("Medium priority") < titles.index("Low priority")


# ---------------------------------------------------------------------------
# GET /tasks — status filter
# ---------------------------------------------------------------------------


def test_get_tasks_status_done(client, db_session):
    user = _make_user(db_session)
    conv = _make_conversation(db_session, user)
    done_task = _make_task(db_session, user, conv, status="done")
    _make_task(db_session, user, conv, status="pending")

    resp = client.get(f"/tasks?user_id={user.id}&status=done")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["tasks"][0]["id"] == done_task.id


def test_get_tasks_status_all(client, db_session):
    user = _make_user(db_session)
    conv = _make_conversation(db_session, user)
    _make_task(db_session, user, conv, status="pending")
    _make_task(db_session, user, conv, status="done")
    _make_task(db_session, user, conv, status="snoozed")

    resp = client.get(f"/tasks?user_id={user.id}&status=all")

    assert resp.status_code == 200
    assert resp.json()["total"] == 3


def test_get_tasks_status_expired_filter(client, db_session):
    """status=expired returns only expired tasks."""
    user = _make_user(db_session)
    conv = _make_conversation(db_session, user)
    expired_task = _make_task(db_session, user, conv, status="expired")
    _make_task(db_session, user, conv, status="pending")

    resp = client.get(f"/tasks?user_id={user.id}&status=expired")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["tasks"][0]["id"] == expired_task.id


def test_get_tasks_status_all_includes_expired(client, db_session):
    """status=all includes expired tasks."""
    user = _make_user(db_session)
    conv = _make_conversation(db_session, user)
    _make_task(db_session, user, conv, status="pending")
    _make_task(db_session, user, conv, status="expired")

    resp = client.get(f"/tasks?user_id={user.id}&status=all")

    assert resp.status_code == 200
    assert resp.json()["total"] == 2


# ---------------------------------------------------------------------------
# GET /tasks — category filter
# ---------------------------------------------------------------------------


def test_get_tasks_category_filter(client, db_session):
    user = _make_user(db_session)
    conv = _make_conversation(db_session, user)
    reply_task = _make_task(db_session, user, conv, category="reply")
    _make_task(db_session, user, conv, category="appointment")

    resp = client.get(f"/tasks?user_id={user.id}&category=reply")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["tasks"][0]["id"] == reply_task.id


# ---------------------------------------------------------------------------
# GET /tasks — error cases
# ---------------------------------------------------------------------------


def test_get_tasks_unknown_user_returns_404(client):
    resp = client.get(f"/tasks?user_id={uuid.uuid4()}")
    assert resp.status_code == 404


def test_get_tasks_invalid_status_returns_400(client, db_session):
    user = _make_user(db_session)
    resp = client.get(f"/tasks?user_id={user.id}&status=invalid_status")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /tasks — isolation between users
# ---------------------------------------------------------------------------


def test_get_tasks_excludes_other_users_tasks(client, db_session):
    user_a = _make_user(db_session)
    user_b = _make_user(db_session)
    conv_a = _make_conversation(db_session, user_a)
    conv_b = _make_conversation(db_session, user_b)

    task_a = _make_task(db_session, user_a, conv_a)
    _make_task(db_session, user_b, conv_b)

    resp = client.get(f"/tasks?user_id={user_a.id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["tasks"][0]["id"] == task_a.id


# ---------------------------------------------------------------------------
# PATCH /tasks/{task_id}
# ---------------------------------------------------------------------------


def test_patch_task_updates_status_to_done(client, db_session):
    user = _make_user(db_session)
    conv = _make_conversation(db_session, user)
    task = _make_task(db_session, user, conv, status="pending")
    original_updated_at = task.updated_at

    resp = client.patch(
        f"/tasks/{task.id}?user_id={user.id}",
        json={"status": "done"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == task.id
    assert data["status"] == "done"
    # updated_at should have changed
    assert data["updated_at"] != original_updated_at.isoformat().replace("+00:00", "Z")


def test_patch_task_wrong_user_returns_404(client, db_session):
    user_a = _make_user(db_session)
    user_b = _make_user(db_session)
    conv = _make_conversation(db_session, user_a)
    task = _make_task(db_session, user_a, conv)

    resp = client.patch(
        f"/tasks/{task.id}?user_id={user_b.id}",
        json={"status": "done"},
    )

    assert resp.status_code == 404


def test_patch_task_unknown_task_returns_404(client, db_session):
    user = _make_user(db_session)

    resp = client.patch(
        f"/tasks/{uuid.uuid4()}?user_id={user.id}",
        json={"status": "done"},
    )

    assert resp.status_code == 404


def test_patch_task_invalid_status_returns_422(client, db_session):
    user = _make_user(db_session)
    conv = _make_conversation(db_session, user)
    task = _make_task(db_session, user, conv)

    resp = client.patch(
        f"/tasks/{task.id}?user_id={user.id}",
        json={"status": "not_a_real_status"},
    )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /tasks/{task_id} — snoozed_until
# ---------------------------------------------------------------------------


def test_patch_snoozed_stores_snoozed_until(client, db_session):
    """PATCH with status=snoozed and snoozed_until stores the datetime."""
    user = _make_user(db_session)
    conv = _make_conversation(db_session, user)
    task = _make_task(db_session, user, conv, status="pending")

    snooze_until = "2026-03-01T09:00:00Z"

    resp = client.patch(
        f"/tasks/{task.id}?user_id={user.id}",
        json={"status": "snoozed", "snoozed_until": snooze_until},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "snoozed"
    assert data["snoozed_until"] is not None


def test_patch_snoozed_without_snoozed_until_is_indefinite(client, db_session):
    """PATCH with status=snoozed and no snoozed_until leaves existing value unchanged."""
    user = _make_user(db_session)
    conv = _make_conversation(db_session, user)
    existing_snooze = datetime(2026, 3, 15, 9, 0, 0, tzinfo=timezone.utc)
    task = _make_task(
        db_session, user, conv, status="snoozed", snoozed_until=existing_snooze
    )

    resp = client.patch(
        f"/tasks/{task.id}?user_id={user.id}",
        json={"status": "snoozed"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "snoozed"
    # snoozed_until should still be set (not cleared)
    assert data["snoozed_until"] is not None


def test_patch_done_clears_snoozed_until(client, db_session):
    """PATCH with status=done clears snoozed_until."""
    user = _make_user(db_session)
    conv = _make_conversation(db_session, user)
    existing_snooze = datetime(2026, 3, 15, 9, 0, 0, tzinfo=timezone.utc)
    task = _make_task(
        db_session, user, conv, status="snoozed", snoozed_until=existing_snooze
    )

    resp = client.patch(
        f"/tasks/{task.id}?user_id={user.id}",
        json={"status": "done"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "done"
    assert data["snoozed_until"] is None
