"""Integration tests for app.services.task_engine.upsert_tasks."""
from __future__ import annotations

import uuid

import pytest

from app.models.conversation import Conversation
from app.models.task import Task
from app.models.user import User
from app.services.llm_processor import LLMTask
from app.services.task_engine import upsert_tasks

_MODEL = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_user(db_session) -> User:
    user = User(email=f"{uuid.uuid4()}@test.com", google_id=str(uuid.uuid4()))
    db_session.add(user)
    db_session.flush()
    return user


def _make_conversation(db_session, user_id: str) -> Conversation:
    conv = Conversation(
        user_id=user_id,
        source="gmail",
        source_id=str(uuid.uuid4()),
    )
    db_session.add(conv)
    db_session.flush()
    return conv


def _llm_task(key: str, category: str = "reply", priority: str = "high") -> LLMTask:
    return LLMTask(task_key=key, title=f"Task {key}", category=category, priority=priority)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUpsertTasks:

    def test_actionable_task_inserted_as_pending(self, db_session):
        """A new actionable task is stored with status=pending."""
        user = _make_user(db_session)
        conv = _make_conversation(db_session, user.id)

        results = upsert_tasks(
            db=db_session,
            conversation_id=conv.id,
            user_id=user.id,
            llm_tasks=[_llm_task("reply-alice", category="reply")],
            raw_llm_output={},
            llm_model=_MODEL,
        )

        assert len(results) == 1
        assert results[0].status == "pending"
        assert results[0].category == "reply"
        assert results[0].task_key == "reply-alice"

        stored = db_session.query(Task).filter(Task.conversation_id == conv.id).all()
        assert len(stored) == 1
        assert stored[0].status == "pending"

    def test_ignored_category_not_inserted(self, db_session):
        """An LLM task with category=ignored is never stored in the DB."""
        user = _make_user(db_session)
        conv = _make_conversation(db_session, user.id)

        results = upsert_tasks(
            db=db_session,
            conversation_id=conv.id,
            user_id=user.id,
            llm_tasks=[_llm_task("promo-thread", category="ignored")],
            raw_llm_output={},
            llm_model=_MODEL,
        )

        assert results == []
        count = db_session.query(Task).filter(Task.conversation_id == conv.id).count()
        assert count == 0

    def test_multiple_tasks_only_actionable_stored(self, db_session):
        """Mix of ignored and actionable — only actionable tasks are stored."""
        user = _make_user(db_session)
        conv = _make_conversation(db_session, user.id)

        llm_tasks = [
            _llm_task("promo-1", category="ignored"),
            _llm_task("reply-bob", category="reply"),
            _llm_task("promo-2", category="ignored"),
        ]

        results = upsert_tasks(
            db=db_session,
            conversation_id=conv.id,
            user_id=user.id,
            llm_tasks=llm_tasks,
            raw_llm_output={},
            llm_model=_MODEL,
        )

        assert len(results) == 1
        assert results[0].task_key == "reply-bob"

        stored = db_session.query(Task).filter(Task.conversation_id == conv.id).all()
        assert len(stored) == 1

    def test_legacy_ignored_row_deleted(self, db_session):
        """An existing task with status=ignored is deleted (legacy cleanup)."""
        user = _make_user(db_session)
        conv = _make_conversation(db_session, user.id)

        # Insert a legacy ignored task directly
        legacy = Task(
            user_id=user.id,
            conversation_id=conv.id,
            task_key="old-ignored",
            title="Old Ignored",
            category="ignored",
            priority="low",
            status="ignored",
            llm_model=_MODEL,
            raw_llm_output={},
            notify_at=[],
            notifications_sent=[],
        )
        db_session.add(legacy)
        db_session.flush()

        # Run upsert with the same task key but now classified as ignored again
        results = upsert_tasks(
            db=db_session,
            conversation_id=conv.id,
            user_id=user.id,
            llm_tasks=[_llm_task("old-ignored", category="ignored")],
            raw_llm_output={},
            llm_model=_MODEL,
        )

        assert results == []
        count = db_session.query(Task).filter(Task.conversation_id == conv.id).count()
        assert count == 0

    def test_pending_task_updated_not_duplicated(self, db_session):
        """Re-running upsert on an existing pending task updates it (no duplicate row)."""
        user = _make_user(db_session)
        conv = _make_conversation(db_session, user.id)

        # First run
        upsert_tasks(
            db=db_session,
            conversation_id=conv.id,
            user_id=user.id,
            llm_tasks=[_llm_task("reply-carol", category="reply", priority="low")],
            raw_llm_output={},
            llm_model=_MODEL,
        )

        # Second run with updated priority
        results = upsert_tasks(
            db=db_session,
            conversation_id=conv.id,
            user_id=user.id,
            llm_tasks=[_llm_task("reply-carol", category="reply", priority="high")],
            raw_llm_output={},
            llm_model=_MODEL,
        )

        assert len(results) == 1
        assert results[0].priority == "high"  # bumped up

        stored = db_session.query(Task).filter(Task.conversation_id == conv.id).all()
        assert len(stored) == 1

    def test_priority_does_not_downgrade(self, db_session):
        """Priority is never lowered on re-run — only bumped up."""
        user = _make_user(db_session)
        conv = _make_conversation(db_session, user.id)

        upsert_tasks(
            db=db_session,
            conversation_id=conv.id,
            user_id=user.id,
            llm_tasks=[_llm_task("task-x", priority="high")],
            raw_llm_output={},
            llm_model=_MODEL,
        )

        results = upsert_tasks(
            db=db_session,
            conversation_id=conv.id,
            user_id=user.id,
            llm_tasks=[_llm_task("task-x", priority="low")],
            raw_llm_output={},
            llm_model=_MODEL,
        )

        assert results[0].priority == "high"  # unchanged
