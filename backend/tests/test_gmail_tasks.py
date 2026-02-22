"""Tests for Celery tasks in app.tasks.gmail_tasks.

Task functions create and close their own DB sessions, so we mock
the session entirely rather than passing the test db_session.
"""

import contextlib
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.gmail_connector import (
    GmailAPIError,
    GmailAuthError,
    HistoryListResult,
    HistoryRecord,
    ThreadDetail,
    ThreadListResult,
    ThreadSummary,
    WatchRegistration,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_mock_user(
    user_id: str = "user-1",
    history_id: str | None = "11111",
    has_token: bool = True,
) -> MagicMock:
    user = MagicMock()
    user.id = user_id
    user.gmail_history_id = history_id
    user.gmail_watch_expiry = None
    user.encrypted_refresh_token = "encrypted-token" if has_token else None
    user.get_refresh_token.return_value = "fake-token" if has_token else None
    return user


def _make_mock_db(user=None) -> MagicMock:
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = user
    return mock_db


def _make_mock_redis(lock_acquired: bool = True) -> tuple[MagicMock, MagicMock]:
    """Return (mock_redis_module, mock_lock) with configurable acquire result."""
    mock_lock = MagicMock()
    mock_lock.acquire.return_value = lock_acquired
    mock_redis_module = MagicMock()
    mock_redis_module.from_url.return_value.lock.return_value = mock_lock
    return mock_redis_module, mock_lock


def _make_watch_reg(history_id: str = "99999") -> WatchRegistration:
    return WatchRegistration(history_id=history_id, expiration_ms=9999999999000)


def _make_history_result(thread_ids: list[str], new_cursor: str = "22222") -> HistoryListResult:
    records = (
        [HistoryRecord(history_id="h1", thread_ids_added=thread_ids)]
        if thread_ids
        else []
    )
    return HistoryListResult(records=records, history_id=new_cursor)


def _make_thread_detail(thread_id: str = "t1") -> ThreadDetail:
    return ThreadDetail(thread_id=thread_id, messages=[], history_id="h_detail")


# ── process_gmail_notification ────────────────────────────────────────────────


class TestProcessGmailNotification:
    def _run(self, user, connector=None, history_id="99999", lock_acquired=True):
        mock_db = _make_mock_db(user)
        mock_redis_module, mock_lock = _make_mock_redis(lock_acquired=lock_acquired)

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("app.tasks.gmail_tasks.SessionLocal", return_value=mock_db)
            )
            stack.enter_context(
                patch("app.tasks.gmail_tasks.process_conversation_with_llm")
            )
            stack.enter_context(
                patch("app.tasks.gmail_tasks.redis_module", mock_redis_module)
            )
            if connector is not None:
                stack.enter_context(
                    patch("app.tasks.gmail_tasks.GmailConnector", return_value=connector)
                )
            from app.tasks.gmail_tasks import process_gmail_notification
            process_gmail_notification("user-1", history_id)

        return mock_db

    def test_user_not_found_returns_early(self):
        mock_db = self._run(user=None)
        mock_db.commit.assert_not_called()

    def test_user_with_no_history_id_returns_early(self):
        user = _make_mock_user(history_id=None)
        mock_db = self._run(user=user)
        mock_db.commit.assert_not_called()

    def test_success_fetches_threads_and_updates_cursor(self):
        user = _make_mock_user(history_id="11111")
        history_result = _make_history_result(["thread_a", "thread_b"], new_cursor="22222")

        connector = MagicMock()
        connector.list_history.return_value = history_result
        connector.get_thread.return_value = _make_thread_detail()

        mock_db = self._run(user=user, connector=connector)

        connector.list_history.assert_called_once_with(start_history_id="11111")
        assert connector.get_thread.call_count == 2
        assert user.gmail_history_id == "22222"
        # ingest() commits once per thread (2) + cursor update commit (1) = 3 total
        assert mock_db.commit.call_count == 3

    def test_404_triggers_re_registration(self):
        user = _make_mock_user(history_id="11111")

        connector = MagicMock()
        connector.list_history.side_effect = GmailAPIError(404, "historyId too old")
        connector.register_watch.return_value = _make_watch_reg("77777")

        self._run(user=user, connector=connector)

        connector.register_watch.assert_called_once()
        assert user.gmail_history_id == "77777"

    def test_auth_error_returns_early(self):
        user = _make_mock_user(history_id="11111")

        connector = MagicMock()
        connector.list_history.side_effect = GmailAuthError("revoked")

        mock_db = self._run(user=user, connector=connector)

        mock_db.commit.assert_not_called()

    def test_get_thread_error_is_swallowed(self):
        user = _make_mock_user(history_id="11111")
        history_result = _make_history_result(["thread_a"], new_cursor="22222")

        connector = MagicMock()
        connector.list_history.return_value = history_result
        connector.get_thread.side_effect = GmailAPIError(500, "server error")

        mock_db = self._run(user=user, connector=connector)

        # Cursor still updated even if individual thread fetch fails
        assert user.gmail_history_id == "22222"
        mock_db.commit.assert_called_once()


# ── renew_all_watches ─────────────────────────────────────────────────────────


class TestRenewAllWatches:
    def _make_db_with_users(self, users: list) -> MagicMock:
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = users
        return mock_db

    def test_renews_for_all_users_with_token(self):
        u1 = _make_mock_user("u1", history_id="aaa")
        u2 = _make_mock_user("u2", history_id="bbb")
        mock_db = self._make_db_with_users([u1, u2])

        connector = MagicMock()
        connector.register_watch.return_value = _make_watch_reg("new_cursor")

        with (
            patch("app.tasks.gmail_tasks.SessionLocal", return_value=mock_db),
            patch("app.tasks.gmail_tasks.GmailConnector", return_value=connector),
        ):
            from app.tasks.gmail_tasks import renew_all_watches
            renew_all_watches()

        assert connector.register_watch.call_count == 2
        assert u1.gmail_history_id == "new_cursor"
        assert u2.gmail_history_id == "new_cursor"
        assert u1.gmail_watch_expiry is not None
        assert mock_db.commit.call_count == 2

    def test_error_for_one_user_does_not_stop_others(self):
        u1 = _make_mock_user("u1", history_id="aaa")
        u2 = _make_mock_user("u2", history_id="bbb")
        mock_db = self._make_db_with_users([u1, u2])

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise GmailAPIError(403, "forbidden")
            return _make_watch_reg("renewed_cursor")

        connector = MagicMock()
        connector.register_watch.side_effect = side_effect

        with (
            patch("app.tasks.gmail_tasks.SessionLocal", return_value=mock_db),
            patch("app.tasks.gmail_tasks.GmailConnector", return_value=connector),
        ):
            from app.tasks.gmail_tasks import renew_all_watches
            renew_all_watches()  # must not raise

        # Both users were attempted
        assert connector.register_watch.call_count == 2
        # Second user was successfully updated
        assert u2.gmail_history_id == "renewed_cursor"


# ── _re_register_watch ────────────────────────────────────────────────────────


class TestReRegisterWatch:
    def test_success_updates_columns(self):
        user = _make_mock_user(history_id="old")
        mock_db = MagicMock()
        connector = MagicMock()
        connector.register_watch.return_value = _make_watch_reg("new_id")

        from app.tasks.gmail_tasks import _re_register_watch
        _re_register_watch(user, mock_db, connector)

        assert user.gmail_history_id == "new_id"
        assert user.gmail_watch_expiry is not None
        mock_db.commit.assert_called_once()

    def test_error_rolls_back(self):
        user = _make_mock_user(history_id="old")
        mock_db = MagicMock()
        connector = MagicMock()
        connector.register_watch.side_effect = GmailAPIError(403, "forbidden")

        from app.tasks.gmail_tasks import _re_register_watch
        _re_register_watch(user, mock_db, connector)  # must not raise

        mock_db.rollback.assert_called_once()
        assert user.gmail_history_id == "old"  # not modified before exception


# ── TestGmailLock ─────────────────────────────────────────────────────────────


class TestGmailLock:
    def test_lock_acquired_processing_continues(self):
        """When lock is acquired, processing proceeds normally."""
        user = _make_mock_user(history_id="11111")
        history_result = _make_history_result([], new_cursor="22222")

        connector = MagicMock()
        connector.list_history.return_value = history_result

        mock_redis_module, mock_lock = _make_mock_redis(lock_acquired=True)
        mock_db = _make_mock_db(user)

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("app.tasks.gmail_tasks.SessionLocal", return_value=mock_db)
            )
            stack.enter_context(
                patch("app.tasks.gmail_tasks.process_conversation_with_llm")
            )
            stack.enter_context(
                patch("app.tasks.gmail_tasks.redis_module", mock_redis_module)
            )
            stack.enter_context(
                patch("app.tasks.gmail_tasks.GmailConnector", return_value=connector)
            )
            from app.tasks.gmail_tasks import process_gmail_notification
            process_gmail_notification("user-1", "11111")

        connector.list_history.assert_called_once()
        mock_lock.release.assert_called_once()

    def test_lock_not_acquired_returns_early_no_db_commit(self):
        """When lock is not acquired, task returns early without DB commit."""
        user = _make_mock_user(history_id="11111")
        mock_redis_module, mock_lock = _make_mock_redis(lock_acquired=False)
        mock_db = _make_mock_db(user)

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("app.tasks.gmail_tasks.SessionLocal", return_value=mock_db)
            )
            stack.enter_context(
                patch("app.tasks.gmail_tasks.process_conversation_with_llm")
            )
            stack.enter_context(
                patch("app.tasks.gmail_tasks.redis_module", mock_redis_module)
            )
            from app.tasks.gmail_tasks import process_gmail_notification
            process_gmail_notification("user-1", "11111")

        mock_db.commit.assert_not_called()

    def test_lock_key_uses_correct_format(self):
        """Lock key format is cordelia:gmail_lock:{user_id}."""
        mock_redis = MagicMock()
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = False  # return early, keeps test simple
        mock_redis.lock.return_value = mock_lock

        mock_redis_module = MagicMock()
        mock_redis_module.from_url.return_value = mock_redis

        mock_db = _make_mock_db(_make_mock_user(user_id="my-user-123"))

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("app.tasks.gmail_tasks.SessionLocal", return_value=mock_db)
            )
            stack.enter_context(
                patch("app.tasks.gmail_tasks.process_conversation_with_llm")
            )
            stack.enter_context(
                patch("app.tasks.gmail_tasks.redis_module", mock_redis_module)
            )
            from app.tasks.gmail_tasks import process_gmail_notification
            process_gmail_notification("my-user-123", "11111")

        mock_redis.lock.assert_called_once_with(
            "cordelia:gmail_lock:my-user-123", timeout=300
        )

    def test_lock_released_in_finally_even_when_task_raises(self):
        """Lock is released even when the task body raises an exception."""
        mock_redis_module, mock_lock = _make_mock_redis(lock_acquired=True)

        mock_db = MagicMock()
        mock_db.query.side_effect = RuntimeError("DB error")

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("app.tasks.gmail_tasks.SessionLocal", return_value=mock_db)
            )
            stack.enter_context(
                patch("app.tasks.gmail_tasks.process_conversation_with_llm")
            )
            stack.enter_context(
                patch("app.tasks.gmail_tasks.redis_module", mock_redis_module)
            )
            from app.tasks.gmail_tasks import process_gmail_notification
            with pytest.raises(RuntimeError):
                process_gmail_notification("user-1", "11111")

        mock_lock.release.assert_called_once()


# ── TestInitialGmailSync ──────────────────────────────────────────────────────


class TestInitialGmailSync:
    def _make_thread_list_result(
        self, thread_ids: list[str], next_page_token: str | None = None
    ) -> ThreadListResult:
        threads = [
            ThreadSummary(thread_id=tid, snippet="snippet", history_id="h1")
            for tid in thread_ids
        ]
        return ThreadListResult(
            threads=threads,
            next_page_token=next_page_token,
            result_size_estimate=len(threads),
        )

    def _run(self, user, connector=None):
        mock_db = _make_mock_db(user)
        mock_ingest = MagicMock()
        mock_ingest.return_value = MagicMock(id="conv-1")
        mock_llm = MagicMock()

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("app.tasks.gmail_tasks.SessionLocal", return_value=mock_db)
            )
            stack.enter_context(
                patch("app.tasks.gmail_tasks.ingest", mock_ingest)
            )
            stack.enter_context(
                patch("app.tasks.gmail_tasks.process_conversation_with_llm", mock_llm)
            )
            if connector is not None:
                stack.enter_context(
                    patch("app.tasks.gmail_tasks.GmailConnector", return_value=connector)
                )
            from app.tasks.gmail_tasks import initial_gmail_sync
            initial_gmail_sync("user-1")

        return mock_db, mock_ingest, mock_llm

    def test_user_not_found_returns_early(self):
        mock_db, mock_ingest, _ = self._run(user=None)
        mock_ingest.assert_not_called()
        mock_db.close.assert_called_once()

    def test_no_refresh_token_returns_early(self):
        user = _make_mock_user(has_token=False)

        connector_cls = MagicMock(side_effect=ValueError("no token"))
        mock_db = _make_mock_db(user)
        mock_ingest = MagicMock()
        mock_llm = MagicMock()

        with (
            patch("app.tasks.gmail_tasks.SessionLocal", return_value=mock_db),
            patch("app.tasks.gmail_tasks.ingest", mock_ingest),
            patch("app.tasks.gmail_tasks.process_conversation_with_llm", mock_llm),
            patch("app.tasks.gmail_tasks.GmailConnector", connector_cls),
        ):
            from app.tasks.gmail_tasks import initial_gmail_sync
            initial_gmail_sync("user-1")

        mock_ingest.assert_not_called()
        mock_db.close.assert_called_once()

    def test_success_fetches_all_threads_and_queues_llm(self):
        """All 3 windows are always run; threads already seen are deduplicated."""
        user = _make_mock_user()
        # Window 1 returns 2 threads; windows 2 and 3 return same 2 (already seen)
        result = self._make_thread_list_result(["t1", "t2"])
        empty = self._make_thread_list_result([])

        connector = MagicMock()
        connector.list_threads.side_effect = [result, empty, empty]
        connector.get_thread.return_value = _make_thread_detail()

        _, mock_ingest, mock_llm = self._run(user=user, connector=connector)

        # All 3 windows attempted
        assert connector.list_threads.call_count == 3
        connector.list_threads.assert_any_call(
            query="newer_than:1d", max_results=50, page_token=None
        )
        connector.list_threads.assert_any_call(
            query="newer_than:3d", max_results=50, page_token=None
        )
        connector.list_threads.assert_any_call(
            query="newer_than:7d", max_results=50, page_token=None
        )
        # Only 2 unique threads ingested
        assert connector.get_thread.call_count == 2
        assert mock_ingest.call_count == 2
        mock_llm.delay.assert_called()

    def test_list_threads_api_error_stops_loop_gracefully(self):
        user = _make_mock_user()

        connector = MagicMock()
        connector.list_threads.side_effect = GmailAPIError(500, "server error")

        _, mock_ingest, _ = self._run(user=user, connector=connector)

        mock_ingest.assert_not_called()

    def test_list_threads_auth_error_stops_loop_gracefully(self):
        user = _make_mock_user()

        connector = MagicMock()
        connector.list_threads.side_effect = GmailAuthError("revoked")

        _, mock_ingest, _ = self._run(user=user, connector=connector)

        mock_ingest.assert_not_called()

    def test_per_thread_error_is_swallowed_and_others_continue(self):
        user = _make_mock_user()
        thread_result = self._make_thread_list_result(["t1", "t2", "t3"])

        connector = MagicMock()
        connector.list_threads.return_value = thread_result

        call_count = 0

        def get_thread_side_effect(thread_id):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise GmailAPIError(404, "not found")
            return _make_thread_detail(thread_id)

        connector.get_thread.side_effect = get_thread_side_effect

        _, mock_ingest, _ = self._run(user=user, connector=connector)

        # 3 threads attempted, 1 failed — 2 ingested
        assert mock_ingest.call_count == 2

    def test_pagination_follows_next_page_token(self):
        """Pagination within a window is followed; wider windows are tried if below threshold."""
        user = _make_mock_user()

        page1 = self._make_thread_list_result(["t1", "t2"], next_page_token="tok2")
        page2 = self._make_thread_list_result(["t3"], next_page_token=None)
        # 3 threads < 5 threshold → 2 more windows are tried; both return empty
        empty = self._make_thread_list_result([])

        connector = MagicMock()
        connector.list_threads.side_effect = [page1, page2, empty, empty]
        connector.get_thread.return_value = _make_thread_detail()

        _, mock_ingest, _ = self._run(user=user, connector=connector)

        # 2 calls for window-1 pagination + 1 each for windows 2 and 3
        assert connector.list_threads.call_count == 4
        connector.list_threads.assert_any_call(
            query="newer_than:1d", max_results=50, page_token=None
        )
        connector.list_threads.assert_any_call(
            query="newer_than:1d", max_results=50, page_token="tok2"
        )
        # 3 unique threads ingested (none duplicated across windows)
        assert mock_ingest.call_count == 3

    def test_pagination_error_on_second_page_stops_gracefully(self):
        """An error mid-pagination stops that window; wider windows are still tried."""
        user = _make_mock_user()

        page1 = self._make_thread_list_result(["t1"], next_page_token="tok2")
        empty = self._make_thread_list_result([])

        connector = MagicMock()
        connector.list_threads.side_effect = [
            page1,
            GmailAPIError(500, "server error"),  # page 2 of window 1 fails
            empty,  # window 2
            empty,  # window 3
        ]
        connector.get_thread.return_value = _make_thread_detail()

        _, mock_ingest, _ = self._run(user=user, connector=connector)

        # First page was processed; second failed; 2 wider windows attempted
        assert mock_ingest.call_count == 1
        assert connector.list_threads.call_count == 4

    def test_db_session_always_closed(self):
        """DB session is closed even when an unexpected error occurs."""
        mock_db = MagicMock()
        mock_db.query.side_effect = RuntimeError("unexpected DB error")

        with (
            patch("app.tasks.gmail_tasks.SessionLocal", return_value=mock_db),
            patch("app.tasks.gmail_tasks.ingest"),
            patch("app.tasks.gmail_tasks.process_conversation_with_llm"),
        ):
            from app.tasks.gmail_tasks import initial_gmail_sync
            with pytest.raises(RuntimeError):
                initial_gmail_sync("user-1")

        mock_db.close.assert_called_once()

    # ── all-windows behaviour ─────────────────────────────────────────────────

    def test_all_three_windows_always_run(self):
        """All 3 windows are always attempted regardless of how many threads are found."""
        user = _make_mock_user()
        # Even 10 threads in the 24h window doesn't stop the wider windows
        result = self._make_thread_list_result([f"t{i}" for i in range(10)])
        empty = self._make_thread_list_result([])

        connector = MagicMock()
        connector.list_threads.side_effect = [result, empty, empty]
        connector.get_thread.return_value = _make_thread_detail()

        self._run(user=user, connector=connector)

        assert connector.list_threads.call_count == 3
        connector.list_threads.assert_any_call(
            query="newer_than:7d", max_results=50, page_token=None
        )

    def test_wider_windows_find_threads_missed_by_24h(self):
        """Threads only present in the 3d/7d windows are ingested."""
        user = _make_mock_user()

        window_1d = self._make_thread_list_result(["t1", "t2"])
        # 3d window reveals 3 extra threads beyond the 24h set
        window_3d = self._make_thread_list_result(["t1", "t2", "t3", "t4", "t5"])
        empty = self._make_thread_list_result([])

        connector = MagicMock()
        connector.list_threads.side_effect = [window_1d, window_3d, empty]
        connector.get_thread.return_value = _make_thread_detail()

        _, mock_ingest, _ = self._run(user=user, connector=connector)

        # 2 from 24h + 3 new from 3d window = 5 total ingested
        assert mock_ingest.call_count == 5

    def test_deduplicates_threads_across_windows(self):
        """Threads returned in a wider window that were already seen are not re-ingested."""
        user = _make_mock_user()

        window_1d = self._make_thread_list_result(["t1", "t2"])
        overlap = self._make_thread_list_result(["t1", "t2"])

        connector = MagicMock()
        connector.list_threads.side_effect = [window_1d, overlap, overlap]
        connector.get_thread.return_value = _make_thread_detail()

        _, mock_ingest, _ = self._run(user=user, connector=connector)

        # All 3 windows tried; only 2 unique threads ingested (no duplicates)
        assert connector.list_threads.call_count == 3
        assert mock_ingest.call_count == 2

    def test_auth_error_in_window_stops_all_windows(self):
        """A GmailAuthError in any window aborts the entire sync immediately."""
        user = _make_mock_user()

        connector = MagicMock()
        connector.list_threads.side_effect = GmailAuthError("token revoked")

        _, mock_ingest, _ = self._run(user=user, connector=connector)

        # Only the first window was attempted before aborting
        connector.list_threads.assert_called_once()
        mock_ingest.assert_not_called()
