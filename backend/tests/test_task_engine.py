"""Integration tests for app.services.task_engine."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.conversation import Conversation
from app.models.task import Task
from app.models.user import User
from app.services.llm_processor import LLMTask
from app.services.task_engine import (
    _dates_close,
    _jaccard,
    _merge_summaries,
    _tokenize,
    merge_duplicate_tasks,
    upsert_tasks,
)

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

        upserted, auto_completed = upsert_tasks(
            db=db_session,
            conversation_id=conv.id,
            user_id=user.id,
            llm_tasks=[_llm_task("reply-alice", category="reply")],
            raw_llm_output={},
            llm_model=_MODEL,
        )

        assert len(upserted) == 1
        assert upserted[0].status == "pending"
        assert upserted[0].category == "reply"
        assert upserted[0].task_key == "reply-alice"

        stored = db_session.query(Task).filter(Task.conversation_id == conv.id).all()
        assert len(stored) == 1
        assert stored[0].status == "pending"

    def test_ignored_category_not_inserted(self, db_session):
        """An LLM task with category=ignored is never stored in the DB."""
        user = _make_user(db_session)
        conv = _make_conversation(db_session, user.id)

        upserted, auto_completed = upsert_tasks(
            db=db_session,
            conversation_id=conv.id,
            user_id=user.id,
            llm_tasks=[_llm_task("promo-thread", category="ignored")],
            raw_llm_output={},
            llm_model=_MODEL,
        )

        assert upserted == []
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

        upserted, auto_completed = upsert_tasks(
            db=db_session,
            conversation_id=conv.id,
            user_id=user.id,
            llm_tasks=llm_tasks,
            raw_llm_output={},
            llm_model=_MODEL,
        )

        assert len(upserted) == 1
        assert upserted[0].task_key == "reply-bob"

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
        upserted, auto_completed = upsert_tasks(
            db=db_session,
            conversation_id=conv.id,
            user_id=user.id,
            llm_tasks=[_llm_task("old-ignored", category="ignored")],
            raw_llm_output={},
            llm_model=_MODEL,
        )

        assert upserted == []
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
        upserted, auto_completed = upsert_tasks(
            db=db_session,
            conversation_id=conv.id,
            user_id=user.id,
            llm_tasks=[_llm_task("reply-carol", category="reply", priority="high")],
            raw_llm_output={},
            llm_model=_MODEL,
        )

        assert len(upserted) == 1
        assert upserted[0].priority == "high"  # bumped up

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

        upserted, auto_completed = upsert_tasks(
            db=db_session,
            conversation_id=conv.id,
            user_id=user.id,
            llm_tasks=[_llm_task("task-x", priority="low")],
            raw_llm_output={},
            llm_model=_MODEL,
        )

        assert upserted[0].priority == "high"  # unchanged

    def test_auto_complete_on_resolved(self, db_session):
        """A pending task is marked done when LLM says resolved=true."""
        user = _make_user(db_session)
        conv = _make_conversation(db_session, user.id)

        upsert_tasks(
            db=db_session,
            conversation_id=conv.id,
            user_id=user.id,
            llm_tasks=[_llm_task("reply-dan", category="reply")],
            raw_llm_output={},
            llm_model=_MODEL,
        )

        resolved_task = LLMTask(
            task_key="reply-dan", title="Reply to Dan",
            category="reply", priority="high", resolved=True,
        )
        upserted, auto_completed = upsert_tasks(
            db=db_session,
            conversation_id=conv.id,
            user_id=user.id,
            llm_tasks=[resolved_task],
            raw_llm_output={},
            llm_model=_MODEL,
        )

        assert len(auto_completed) == 1
        assert auto_completed[0].status == "done"


# ---------------------------------------------------------------------------
# Unit tests for fuzzy dedup helpers
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_basic(self):
        tokens = _tokenize("Attend Cisco Culture Chat with Ryan")
        assert "cisco" in tokens
        assert "culture" in tokens
        assert "chat" in tokens
        assert "ryan" in tokens
        # stop words excluded
        assert "attend" not in tokens
        assert "with" not in tokens

    def test_single_char_excluded(self):
        tokens = _tokenize("A quick test")
        assert "a" not in tokens
        assert "quick" in tokens
        assert "test" in tokens


class TestJaccard:
    def test_identical(self):
        s = {"cisco", "culture", "chat"}
        assert _jaccard(s, s) == 1.0

    def test_disjoint(self):
        assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_partial_overlap(self):
        a = {"cisco", "culture", "chat", "ryan"}
        b = {"cisco", "culture", "chat"}
        # intersection=3, union=4
        assert abs(_jaccard(a, b) - 0.75) < 0.01

    def test_empty(self):
        assert _jaccard(set(), {"a"}) == 0.0


class TestDatesClose:
    def test_both_none(self):
        assert _dates_close(None, None) is True

    def test_one_none(self):
        assert _dates_close(datetime.now(timezone.utc), None) is False

    def test_close_dates(self):
        d1 = datetime(2026, 3, 12, 10, 0, tzinfo=timezone.utc)
        d2 = datetime(2026, 3, 12, 14, 0, tzinfo=timezone.utc)
        assert _dates_close(d1, d2) is True

    def test_far_dates(self):
        d1 = datetime(2026, 3, 12, 10, 0, tzinfo=timezone.utc)
        d2 = datetime(2026, 3, 20, 10, 0, tzinfo=timezone.utc)
        assert _dates_close(d1, d2) is False


# ---------------------------------------------------------------------------
# Fuzzy dedup integration tests
# ---------------------------------------------------------------------------


class TestFuzzyDedup:

    def test_fuzzy_match_prevents_duplicate(self, db_session):
        """A new task with similar title/date merges into existing — summaries combined, sources set."""
        user = _make_user(db_session)
        conv1 = _make_conversation(db_session, user.id)

        # Use a far-future date to avoid reject_past filtering
        future = datetime.now(timezone.utc) + timedelta(days=30)

        # First: create a calendar-sourced task
        cal_task = Task(
            user_id=user.id,
            conversation_id=conv1.id,
            task_key="attend-cisco-culture-chat",
            source="google_calendar",
            title="Attend Cisco Culture Chat with Ryan",
            category="appointment",
            priority="medium",
            status="pending",
            due_at=future,
            summary="Team culture sync",
            llm_model=_MODEL,
            raw_llm_output={},
            notify_at=[],
            notifications_sent=[],
        )
        db_session.add(cal_task)
        db_session.flush()

        # Second: email about the same event, different conv, different task_key
        conv2 = _make_conversation(db_session, user.id)
        future_str = (future + timedelta(hours=1)).isoformat()
        email_task = LLMTask(
            task_key="appointment-cisco-culture-chat",
            title="Appointment Cisco Culture Chat",
            category="appointment",
            priority="medium",
            due_at=future_str,
            summary="Bring quarterly metrics",
        )

        upserted, _ = upsert_tasks(
            db=db_session,
            conversation_id=conv2.id,
            user_id=user.id,
            llm_tasks=[email_task],
            raw_llm_output={},
            llm_model=_MODEL,
        )

        # Should have merged — only 1 task in DB for this user
        all_tasks = db_session.query(Task).filter(Task.user_id == user.id).all()
        assert len(all_tasks) == 1
        task = all_tasks[0]
        assert task.task_key == "attend-cisco-culture-chat"
        # Summary should contain content from both sources
        assert "culture sync" in task.summary
        assert "quarterly metrics" in task.summary
        # Sources should list both
        assert set(task.sources) == {"google_calendar", "gmail"}

    def test_no_fuzzy_match_for_unrelated_tasks(self, db_session):
        """Tasks with different titles create separate rows."""
        user = _make_user(db_session)
        conv1 = _make_conversation(db_session, user.id)

        task1 = Task(
            user_id=user.id,
            conversation_id=conv1.id,
            task_key="attend-cisco-culture-chat",
            source="google_calendar",
            title="Attend Cisco Culture Chat",
            category="appointment",
            priority="medium",
            status="pending",
            due_at=datetime(2026, 3, 12, 14, 0, tzinfo=timezone.utc),
            llm_model=_MODEL,
            raw_llm_output={},
            notify_at=[],
            notifications_sent=[],
        )
        db_session.add(task1)
        db_session.flush()

        conv2 = _make_conversation(db_session, user.id)
        different_task = LLMTask(
            task_key="reply-john-budget",
            title="Reply to John about budget report",
            category="reply",
            priority="high",
        )

        upserted, _ = upsert_tasks(
            db=db_session,
            conversation_id=conv2.id,
            user_id=user.id,
            llm_tasks=[different_task],
            raw_llm_output={},
            llm_model=_MODEL,
        )

        all_tasks = db_session.query(Task).filter(Task.user_id == user.id).all()
        assert len(all_tasks) == 2

    def test_missed_task_updated_on_reprocess(self, db_session):
        """A task with status=missed is updated when reprocessed."""
        user = _make_user(db_session)
        conv = _make_conversation(db_session, user.id)

        missed = Task(
            user_id=user.id,
            conversation_id=conv.id,
            task_key="attend-meeting",
            source="gmail",
            title="Attend meeting",
            category="appointment",
            priority="medium",
            status="missed",
            due_at=datetime(2026, 3, 10, 14, 0, tzinfo=timezone.utc),
            llm_model=_MODEL,
            raw_llm_output={},
            notify_at=[],
            notifications_sent=[],
        )
        db_session.add(missed)
        db_session.flush()

        updated_task = LLMTask(
            task_key="attend-meeting",
            title="Attend meeting (updated)",
            category="appointment",
            priority="high",
            due_at="2026-03-15T14:00:00Z",
        )

        upserted, _ = upsert_tasks(
            db=db_session,
            conversation_id=conv.id,
            user_id=user.id,
            llm_tasks=[updated_task],
            raw_llm_output={},
            llm_model=_MODEL,
        )

        assert len(upserted) == 1
        assert upserted[0].title == "Attend meeting (updated)"
        assert upserted[0].priority == "high"


# ---------------------------------------------------------------------------
# merge_duplicate_tasks
# ---------------------------------------------------------------------------


class TestMergeDuplicates:

    def test_merges_duplicates(self, db_session):
        """Duplicate tasks are merged — calendar source wins, summaries combined, sources set."""
        user = _make_user(db_session)
        conv1 = _make_conversation(db_session, user.id)
        conv2 = _make_conversation(db_session, user.id)
        due = datetime(2026, 3, 12, 14, 0, tzinfo=timezone.utc)

        cal_task = Task(
            user_id=user.id, conversation_id=conv1.id,
            task_key="attend-cisco-chat", source="google_calendar",
            title="Attend Cisco Culture Chat", category="appointment",
            priority="medium", status="pending", due_at=due,
            summary="Quarterly review with leadership",
            llm_model=_MODEL, raw_llm_output={}, notify_at=[], notifications_sent=[],
        )
        email_task = Task(
            user_id=user.id, conversation_id=conv2.id,
            task_key="appointment-cisco-chat", source="gmail",
            title="Appointment: Cisco Culture Chat", category="appointment",
            priority="medium", status="pending", due_at=due,
            summary="Prepare status slides\nBring budget report",
            llm_model=_MODEL, raw_llm_output={}, notify_at=[], notifications_sent=[],
        )
        db_session.add_all([cal_task, email_task])
        db_session.flush()

        merged = merge_duplicate_tasks(db_session, user.id)
        assert merged == 1

        open_tasks = db_session.query(Task).filter(
            Task.user_id == user.id, Task.status == "pending",
        ).all()
        assert len(open_tasks) == 1
        survivor = open_tasks[0]
        # Calendar task should be the survivor
        assert survivor.source == "google_calendar"
        # Summary should contain content from both tasks
        assert "Quarterly review" in survivor.summary
        assert "status slides" in survivor.summary
        assert "budget report" in survivor.summary
        # Sources should list both
        assert set(survivor.sources) == {"google_calendar", "gmail"}

    def test_no_merge_for_unrelated(self, db_session):
        """Unrelated tasks are not merged."""
        user = _make_user(db_session)
        conv = _make_conversation(db_session, user.id)

        t1 = Task(
            user_id=user.id, conversation_id=conv.id,
            task_key="reply-alice", source="gmail",
            title="Reply to Alice about budget", category="reply",
            priority="high", status="pending",
            llm_model=_MODEL, raw_llm_output={}, notify_at=[], notifications_sent=[],
        )
        t2 = Task(
            user_id=user.id, conversation_id=conv.id,
            task_key="attend-meeting", source="google_calendar",
            title="Attend quarterly review meeting", category="appointment",
            priority="medium", status="pending",
            llm_model=_MODEL, raw_llm_output={}, notify_at=[], notifications_sent=[],
        )
        db_session.add_all([t1, t2])
        db_session.flush()

        merged = merge_duplicate_tasks(db_session, user.id)
        assert merged == 0


# ---------------------------------------------------------------------------
# _merge_summaries unit tests
# ---------------------------------------------------------------------------


class TestMergeSummaries:
    def test_both_none(self):
        assert _merge_summaries(None, None) is None

    def test_existing_none(self):
        assert _merge_summaries(None, "New info") == "New info"

    def test_incoming_none(self):
        assert _merge_summaries("Existing info", None) == "Existing info"

    def test_combines_different_lines(self):
        result = _merge_summaries("Quarterly review", "Prepare slides")
        assert "Quarterly review" in result
        assert "Prepare slides" in result

    def test_deduplicates_identical_lines(self):
        result = _merge_summaries("Quarterly review", "Quarterly review")
        assert result.count("Quarterly review") == 1

    def test_bullet_formatting(self):
        result = _merge_summaries("Line one", "Line two")
        assert result.startswith("•")
