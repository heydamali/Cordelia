"""Tests for Celery tasks in app.tasks.gmail_tasks.

Task functions create and close their own DB sessions, so we mock
the session entirely rather than passing the test db_session.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.services.gmail_connector import (
    GmailAPIError,
    GmailAuthError,
    HistoryListResult,
    HistoryRecord,
    ThreadDetail,
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
    def _run(self, user, connector=None, history_id="99999"):
        mock_db = _make_mock_db(user)
        patches = [patch("app.tasks.gmail_tasks.SessionLocal", return_value=mock_db)]
        if connector is not None:
            patches.append(patch("app.tasks.gmail_tasks.GmailConnector", return_value=connector))

        from app.tasks.gmail_tasks import process_gmail_notification
        with patches[0]:
            if len(patches) > 1:
                with patches[1]:
                    process_gmail_notification("user-1", history_id)
            else:
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
        mock_db.commit.assert_called_once()

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
