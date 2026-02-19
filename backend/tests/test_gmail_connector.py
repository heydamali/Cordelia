"""Tests for the GmailConnector service — pure-function unit tests and
mocked-API integration tests.  No real Google API calls are made.
"""

import base64
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.gmail_connector import (
    EmailAddress,
    GmailAPIError,
    GmailAuthError,
    GmailConnector,
    HistoryListResult,
    HistoryRecord,
    ParsedMessage,
    ThreadDetail,
    ThreadListResult,
    ThreadSummary,
    WatchRegistration,
)


# ── Fixtures / helpers ────────────────────────────────────────────────────────


def _b64(text: str) -> str:
    """Base64url-encode a string the way Gmail does (no padding)."""
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _make_thread_list_response(count: int = 2, next_page_token: str | None = None) -> dict:
    threads = [
        {"id": f"thread_{i}", "snippet": f"Snippet {i}", "historyId": f"h{i}"}
        for i in range(count)
    ]
    resp: dict = {"threads": threads, "resultSizeEstimate": count}
    if next_page_token:
        resp["nextPageToken"] = next_page_token
    return resp


def _make_simple_message(
    msg_id: str = "msg_1",
    thread_id: str = "thread_1",
    subject: str = "Test Subject",
    sender: str = "Alice <alice@example.com>",
    to: str = "Bob <bob@example.com>",
    cc: str = "",
    body_text: str = "Hello, world!",
    labels: list | None = None,
    internal_date_ms: int = 1700000000000,
) -> dict:
    return {
        "id": msg_id,
        "threadId": thread_id,
        "labelIds": labels if labels is not None else ["INBOX"],
        "snippet": body_text[:50],
        "internalDate": str(internal_date_ms),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "To", "value": to},
                {"name": "Cc", "value": cc},
            ],
            "body": {"data": _b64(body_text), "size": len(body_text)},
        },
    }


def _make_multipart_message(
    body_plain: str = "Plain text body",
    body_html: str = "<p>HTML body</p>",
) -> dict:
    return {
        "id": "msg_2",
        "threadId": "thread_1",
        "labelIds": ["INBOX"],
        "snippet": body_plain[:50],
        "internalDate": "1700000000000",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "Subject", "value": "Multipart Subject"},
                {"name": "From", "value": "sender@test.com"},
                {"name": "To", "value": "recipient@test.com"},
                {"name": "Cc", "value": ""},
            ],
            "body": {"size": 0},
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _b64(body_plain)},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64(body_html)},
                },
            ],
        },
    }


def _make_nested_multipart_message() -> dict:
    """multipart/mixed > multipart/alternative > text/plain + text/html, plus an attachment."""
    return {
        "id": "msg_nested",
        "threadId": "thread_1",
        "labelIds": ["INBOX", "IMPORTANT"],
        "snippet": "Nested body",
        "internalDate": "1700000000000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "Subject", "value": "Nested Subject"},
                {"name": "From", "value": '"Doe, Jane" <jane@example.com>'},
                {"name": "To", "value": "bob@example.com, carol@example.com"},
                {"name": "Cc", "value": "dave@example.com"},
            ],
            "body": {"size": 0},
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "body": {"size": 0},
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": _b64("Nested plain")},
                        },
                        {
                            "mimeType": "text/html",
                            "body": {"data": _b64("<b>Nested html</b>")},
                        },
                    ],
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "report.pdf",
                    "body": {"attachmentId": "att_1", "size": 1024},
                },
            ],
        },
    }


def _mock_gmail_service(list_response: dict | None = None, get_response: dict | None = None):
    service = MagicMock()
    service.users.return_value.threads.return_value.list.return_value.execute.return_value = (
        list_response or {}
    )
    service.users.return_value.threads.return_value.get.return_value.execute.return_value = (
        get_response or {}
    )
    return service


# ── Pure-function unit tests ──────────────────────────────────────────────────


class TestDecodeBodyData:
    def test_basic(self):
        encoded = _b64("Hello, world!")
        assert GmailConnector._decode_body_data(encoded) == "Hello, world!"

    def test_without_padding(self):
        # Gmail strips trailing '=' padding; our decoder must handle that
        raw = "SGVsbG8"  # "Hello" without padding
        assert GmailConnector._decode_body_data(raw) == "Hello"

    def test_unicode(self):
        text = "Héllo wörld 🌍"
        encoded = _b64(text)
        assert GmailConnector._decode_body_data(encoded) == text


class TestGetHeader:
    _headers = [
        {"name": "From", "value": "alice@example.com"},
        {"name": "Subject", "value": "Test"},
        {"name": "To", "value": "bob@example.com"},
    ]

    def test_found(self):
        assert GmailConnector._get_header(self._headers, "From") == "alice@example.com"

    def test_case_insensitive(self):
        assert GmailConnector._get_header(self._headers, "from") == "alice@example.com"
        assert GmailConnector._get_header(self._headers, "SUBJECT") == "Test"

    def test_missing_returns_empty_string(self):
        assert GmailConnector._get_header(self._headers, "Cc") == ""

    def test_empty_headers(self):
        assert GmailConnector._get_header([], "From") == ""


class TestParseEmailAddress:
    def test_name_and_address(self):
        result = GmailConnector._parse_email_address("Alice <alice@example.com>")
        assert result == EmailAddress(name="Alice", email="alice@example.com")

    def test_bare_address(self):
        result = GmailConnector._parse_email_address("alice@example.com")
        assert result == EmailAddress(name="", email="alice@example.com")

    def test_quoted_name_with_comma(self):
        result = GmailConnector._parse_email_address('"Doe, Jane" <jane@example.com>')
        assert result.email == "jane@example.com"
        assert "Doe" in result.name and "Jane" in result.name

    def test_empty_string(self):
        result = GmailConnector._parse_email_address("")
        assert result == EmailAddress(name="", email="")


class TestParseEmailAddressList:
    def test_multiple_addresses(self):
        raw = "Alice <alice@example.com>, Bob <bob@example.com>"
        result = GmailConnector._parse_email_address_list(raw)
        assert len(result) == 2
        assert result[0].email == "alice@example.com"
        assert result[1].email == "bob@example.com"

    def test_empty_string(self):
        assert GmailConnector._parse_email_address_list("") == []

    def test_single_address(self):
        result = GmailConnector._parse_email_address_list("alice@example.com")
        assert len(result) == 1
        assert result[0].email == "alice@example.com"


class TestExtractBody:
    def test_simple_plain(self):
        payload = {
            "mimeType": "text/plain",
            "body": {"data": _b64("Hello plain")},
        }
        assert GmailConnector._extract_body(payload, "text/plain") == "Hello plain"

    def test_multipart_alternative(self):
        payload = _make_multipart_message()["payload"]
        assert GmailConnector._extract_body(payload, "text/plain") == "Plain text body"
        assert GmailConnector._extract_body(payload, "text/html") == "<p>HTML body</p>"

    def test_nested_multipart(self):
        payload = _make_nested_multipart_message()["payload"]
        assert GmailConnector._extract_body(payload, "text/plain") == "Nested plain"
        assert GmailConnector._extract_body(payload, "text/html") == "<b>Nested html</b>"

    def test_missing_type_returns_empty(self):
        payload = {
            "mimeType": "text/plain",
            "body": {"data": _b64("Only plain")},
        }
        assert GmailConnector._extract_body(payload, "text/html") == ""

    def test_attachment_skipped(self):
        # A PDF part should not be returned when requesting text/plain
        payload = _make_nested_multipart_message()["payload"]
        result = GmailConnector._extract_body(payload, "application/pdf")
        # attachmentId-only parts have no body.data, so result should be ""
        assert result == ""


class TestParseMessage:
    def test_simple_message(self):
        raw = _make_simple_message()
        msg = GmailConnector._parse_message(raw)

        assert msg.message_id == "msg_1"
        assert msg.thread_id == "thread_1"
        assert msg.subject == "Test Subject"
        assert msg.sender == EmailAddress(name="Alice", email="alice@example.com")
        assert msg.to[0].email == "bob@example.com"
        assert msg.body_plain == "Hello, world!"
        assert msg.labels == ["INBOX"]
        assert msg.snippet == "Hello, world!"

    def test_multipart_message(self):
        raw = _make_multipart_message()
        msg = GmailConnector._parse_message(raw)

        assert msg.body_plain == "Plain text body"
        assert msg.body_html == "<p>HTML body</p>"

    def test_nested_multipart(self):
        raw = _make_nested_multipart_message()
        msg = GmailConnector._parse_message(raw)

        assert msg.body_plain == "Nested plain"
        assert msg.body_html == "<b>Nested html</b>"
        assert msg.sender.email == "jane@example.com"
        assert len(msg.to) == 2
        assert msg.cc[0].email == "dave@example.com"
        assert "IMPORTANT" in msg.labels

    def test_timestamp_conversion(self):
        # 1700000000000 ms = 2023-11-14 22:13:20 UTC
        raw = _make_simple_message(internal_date_ms=1700000000000)
        msg = GmailConnector._parse_message(raw)

        assert msg.date == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)

    def test_empty_labels(self):
        raw = _make_simple_message()
        raw.pop("labelIds", None)
        msg = GmailConnector._parse_message(raw)
        assert msg.labels == []


# ── Integration tests (mocked Gmail API) ─────────────────────────────────────


class TestGmailConnectorInit:
    def test_with_refresh_token(self):
        connector = GmailConnector(refresh_token="fake-token")
        assert connector._refresh_token == "fake-token"

    def test_with_user(self):
        mock_user = MagicMock()
        mock_user.get_refresh_token.return_value = "user-token"
        connector = GmailConnector(user=mock_user)
        assert connector._refresh_token == "user-token"

    def test_no_args_raises(self):
        with pytest.raises(ValueError, match="Provide either"):
            GmailConnector()

    def test_user_with_no_token_raises(self):
        mock_user = MagicMock()
        mock_user.get_refresh_token.return_value = None
        with pytest.raises(ValueError, match="no stored refresh token"):
            GmailConnector(user=mock_user)


class TestListThreads:
    def test_basic(self):
        api_response = _make_thread_list_response(count=3)
        mock_service = _mock_gmail_service(list_response=api_response)

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            result = GmailConnector(refresh_token="tok").list_threads(max_results=10)

        assert isinstance(result, ThreadListResult)
        assert len(result.threads) == 3
        assert result.threads[0].thread_id == "thread_0"
        assert result.threads[0].snippet == "Snippet 0"
        assert result.next_page_token is None

    def test_pagination(self):
        api_response = _make_thread_list_response(count=2, next_page_token="page2")
        mock_service = _mock_gmail_service(list_response=api_response)

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            result = GmailConnector(refresh_token="tok").list_threads(
                max_results=2, page_token="page1"
            )

        assert result.next_page_token == "page2"
        # Verify pageToken was forwarded to the API
        call_kwargs = mock_service.users.return_value.threads.return_value.list.call_args[1]
        assert call_kwargs["pageToken"] == "page1"

    def test_query_forwarded(self):
        mock_service = _mock_gmail_service(list_response={"resultSizeEstimate": 0})

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            GmailConnector(refresh_token="tok").list_threads(query="is:unread")

        call_kwargs = mock_service.users.return_value.threads.return_value.list.call_args[1]
        assert call_kwargs["q"] == "is:unread"

    def test_empty_inbox(self):
        mock_service = _mock_gmail_service(list_response={})

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            result = GmailConnector(refresh_token="tok").list_threads()

        assert result.threads == []
        assert result.result_size_estimate == 0

    def test_auth_error_raises_gmail_auth_error(self):
        from google.auth.exceptions import RefreshError

        mock_service = MagicMock()
        mock_service.users.return_value.threads.return_value.list.return_value.execute.side_effect = RefreshError("expired")

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            with pytest.raises(GmailAuthError):
                GmailConnector(refresh_token="tok").list_threads()


class TestGetThread:
    def test_basic(self):
        message = _make_simple_message()
        api_response = {
            "id": "thread_1",
            "historyId": "h99",
            "messages": [message],
        }
        mock_service = _mock_gmail_service(get_response=api_response)

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            detail = GmailConnector(refresh_token="tok").get_thread("thread_1")

        assert isinstance(detail, ThreadDetail)
        assert detail.thread_id == "thread_1"
        assert detail.history_id == "h99"
        assert len(detail.messages) == 1
        assert detail.messages[0].subject == "Test Subject"
        assert detail.messages[0].body_plain == "Hello, world!"

    def test_multiple_messages(self):
        messages = [
            _make_simple_message(msg_id="msg_1", body_text="First"),
            _make_simple_message(msg_id="msg_2", body_text="Second"),
            _make_simple_message(msg_id="msg_3", body_text="Third"),
        ]
        api_response = {"id": "thread_1", "messages": messages}
        mock_service = _mock_gmail_service(get_response=api_response)

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            detail = GmailConnector(refresh_token="tok").get_thread("thread_1")

        assert len(detail.messages) == 3
        assert [m.body_plain for m in detail.messages] == ["First", "Second", "Third"]

    def test_not_found_raises_gmail_api_error(self):
        from googleapiclient.errors import HttpError

        mock_service = MagicMock()
        http_error = HttpError(resp=MagicMock(status=404), content=b"Not Found")
        mock_service.users.return_value.threads.return_value.get.return_value.execute.side_effect = http_error

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            with pytest.raises(GmailAPIError) as exc_info:
                GmailConnector(refresh_token="tok").get_thread("nonexistent")

        assert exc_info.value.status_code == 404


class TestRegisterWatch:
    def _mock_service(self, history_id="55555", expiration="9999999999000"):
        service = MagicMock()
        service.users.return_value.watch.return_value.execute.return_value = {
            "historyId": history_id,
            "expiration": expiration,
        }
        return service

    def test_basic(self):
        mock_service = self._mock_service(history_id="77777", expiration="9000000000000")

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            result = GmailConnector(refresh_token="tok").register_watch(
                topic_name="projects/test/topics/gmail-push"
            )

        assert isinstance(result, WatchRegistration)
        assert result.history_id == "77777"
        assert result.expiration_ms == 9000000000000

    def test_topic_name_forwarded(self):
        mock_service = self._mock_service()

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            GmailConnector(refresh_token="tok").register_watch(
                topic_name="projects/my-project/topics/my-topic"
            )

        call_kwargs = mock_service.users.return_value.watch.call_args[1]
        assert call_kwargs["body"]["topicName"] == "projects/my-project/topics/my-topic"

    def test_default_label_ids(self):
        mock_service = self._mock_service()

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            GmailConnector(refresh_token="tok").register_watch(topic_name="t")

        body = mock_service.users.return_value.watch.call_args[1]["body"]
        assert body["labelIds"] == ["INBOX"]
        assert body["labelFilterBehavior"] == "INCLUDE"

    def test_custom_label_ids(self):
        mock_service = self._mock_service()

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            GmailConnector(refresh_token="tok").register_watch(
                topic_name="t", label_ids=["SENT", "STARRED"]
            )

        body = mock_service.users.return_value.watch.call_args[1]["body"]
        assert body["labelIds"] == ["SENT", "STARRED"]

    def test_auth_error_raises_gmail_auth_error(self):
        from google.auth.exceptions import RefreshError

        mock_service = MagicMock()
        mock_service.users.return_value.watch.return_value.execute.side_effect = RefreshError("expired")

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            with pytest.raises(GmailAuthError):
                GmailConnector(refresh_token="tok").register_watch(topic_name="t")

    def test_api_error_raises_gmail_api_error(self):
        from googleapiclient.errors import HttpError

        mock_service = MagicMock()
        http_error = HttpError(resp=MagicMock(status=403), content=b"Forbidden")
        mock_service.users.return_value.watch.return_value.execute.side_effect = http_error

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            with pytest.raises(GmailAPIError) as exc_info:
                GmailConnector(refresh_token="tok").register_watch(topic_name="t")

        assert exc_info.value.status_code == 403


class TestListHistory:
    def _make_history_response(self, entries: list, new_history_id: str = "99999") -> dict:
        return {"history": entries, "historyId": new_history_id}

    def _make_entry(self, entry_id: str, thread_ids: list[str]) -> dict:
        return {
            "id": entry_id,
            "messagesAdded": [
                {"message": {"id": f"msg_{tid}", "threadId": tid}}
                for tid in thread_ids
            ],
        }

    def _mock_service(self, response: dict):
        service = MagicMock()
        service.users.return_value.history.return_value.list.return_value.execute.return_value = response
        return service

    def test_basic(self):
        entry = self._make_entry("h100", ["thread_a", "thread_b"])
        response = self._make_history_response([entry], new_history_id="h200")
        mock_service = self._mock_service(response)

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            result = GmailConnector(refresh_token="tok").list_history(start_history_id="h50")

        assert isinstance(result, HistoryListResult)
        assert result.history_id == "h200"
        assert len(result.records) == 1
        assert result.records[0].thread_ids_added == ["thread_a", "thread_b"]

    def test_deduplicates_thread_ids_within_entry(self):
        # Same thread_id appearing in two messages within one history entry
        entry = {
            "id": "h100",
            "messagesAdded": [
                {"message": {"id": "msg_1", "threadId": "thread_x"}},
                {"message": {"id": "msg_2", "threadId": "thread_x"}},
            ],
        }
        response = self._make_history_response([entry])
        mock_service = self._mock_service(response)

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            result = GmailConnector(refresh_token="tok").list_history(start_history_id="h1")

        assert result.records[0].thread_ids_added == ["thread_x"]

    def test_empty_history(self):
        response = {"historyId": "h999"}  # no "history" key
        mock_service = self._mock_service(response)

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            result = GmailConnector(refresh_token="tok").list_history(start_history_id="h1")

        assert result.records == []
        assert result.history_id == "h999"

    def test_entry_with_no_messages_added_excluded(self):
        # Entry with no messagesAdded should not produce a record
        entry = {"id": "h100", "messagesAdded": []}
        response = self._make_history_response([entry])
        mock_service = self._mock_service(response)

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            result = GmailConnector(refresh_token="tok").list_history(start_history_id="h1")

        assert result.records == []

    def test_start_history_id_forwarded(self):
        response = {"historyId": "h999"}
        mock_service = self._mock_service(response)

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            GmailConnector(refresh_token="tok").list_history(start_history_id="h42")

        call_kwargs = mock_service.users.return_value.history.return_value.list.call_args[1]
        assert call_kwargs["startHistoryId"] == "h42"
        assert call_kwargs["historyTypes"] == ["messageAdded"]

    def test_404_raises_gmail_api_error(self):
        from googleapiclient.errors import HttpError

        mock_service = MagicMock()
        http_error = HttpError(resp=MagicMock(status=404), content=b"Not Found")
        mock_service.users.return_value.history.return_value.list.return_value.execute.side_effect = http_error

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            with pytest.raises(GmailAPIError) as exc_info:
                GmailConnector(refresh_token="tok").list_history(start_history_id="h1")

        assert exc_info.value.status_code == 404

    def test_auth_error_raises_gmail_auth_error(self):
        from google.auth.exceptions import RefreshError

        mock_service = MagicMock()
        mock_service.users.return_value.history.return_value.list.return_value.execute.side_effect = RefreshError("expired")

        with patch("app.services.gmail_connector.build", return_value=mock_service):
            with pytest.raises(GmailAuthError):
                GmailConnector(refresh_token="tok").list_history(start_history_id="h1")
