from __future__ import annotations

import base64
import dataclasses
import email.utils
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import settings

if TYPE_CHECKING:
    from app.models.user import User


# ── Exceptions ────────────────────────────────────────────────────────────────


class GmailAuthError(Exception):
    """Raised when the user's Google credentials are invalid or revoked."""


class GmailAPIError(Exception):
    """Raised when the Gmail API returns an HTTP error."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)


# ── Data structures ───────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class EmailAddress:
    name: str
    email: str


@dataclasses.dataclass(frozen=True)
class ParsedMessage:
    message_id: str
    thread_id: str
    subject: str
    sender: EmailAddress
    to: list[EmailAddress]
    cc: list[EmailAddress]
    date: datetime
    body_plain: str
    body_html: str
    labels: list[str]
    snippet: str


@dataclasses.dataclass(frozen=True)
class ThreadSummary:
    thread_id: str
    snippet: str
    history_id: str


@dataclasses.dataclass(frozen=True)
class ThreadListResult:
    threads: list[ThreadSummary]
    next_page_token: str | None
    result_size_estimate: int


@dataclasses.dataclass(frozen=True)
class ThreadDetail:
    thread_id: str
    messages: list[ParsedMessage]
    history_id: str


@dataclasses.dataclass(frozen=True)
class WatchRegistration:
    history_id: str
    expiration_ms: int   # Unix epoch ms from Gmail API


@dataclasses.dataclass(frozen=True)
class HistoryRecord:
    history_id: str
    thread_ids_added: list[str]   # deduplicated thread IDs


@dataclasses.dataclass(frozen=True)
class HistoryListResult:
    records: list[HistoryRecord]
    history_id: str               # new cursor to store on user


# ── Service ───────────────────────────────────────────────────────────────────


class GmailConnector:
    """Wraps the Gmail API v1 for reading threads and messages.

    Instantiate with either a User ORM object or a raw refresh token string.
    The Google API service is built lazily on first use.
    """

    _TOKEN_URI = "https://oauth2.googleapis.com/token"

    def __init__(
        self,
        *,
        user: "User | None" = None,
        refresh_token: str | None = None,
    ) -> None:
        if user is not None:
            self._refresh_token = user.get_refresh_token()
        elif refresh_token is not None:
            self._refresh_token = refresh_token
        else:
            raise ValueError("Provide either a user or a refresh_token")

        if not self._refresh_token:
            raise ValueError("User has no stored refresh token")

        self._service: Any = None

    # ── Credential / service construction ────────────────────────────────

    def _build_credentials(self) -> Credentials:
        return Credentials(
            token=None,
            refresh_token=self._refresh_token,
            token_uri=self._TOKEN_URI,
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
        )

    def _get_service(self) -> Any:
        if self._service is None:
            self._service = build("gmail", "v1", credentials=self._build_credentials())
        return self._service

    # ── Public API ────────────────────────────────────────────────────────

    def list_threads(
        self,
        max_results: int = 20,
        page_token: str | None = None,
        query: str | None = None,
        label_ids: list[str] | None = None,
    ) -> ThreadListResult:
        """Return a paginated list of thread summaries from the user's inbox."""
        kwargs: dict[str, Any] = {
            "userId": "me",
            "maxResults": max_results,
            "labelIds": label_ids if label_ids is not None else ["INBOX"],
        }
        if page_token:
            kwargs["pageToken"] = page_token
        if query:
            kwargs["q"] = query

        try:
            response = self._get_service().users().threads().list(**kwargs).execute()
        except RefreshError as exc:
            raise GmailAuthError("Google credentials expired or revoked") from exc
        except HttpError as exc:
            raise GmailAPIError(exc.resp.status, exc._get_reason()) from exc

        threads = [
            ThreadSummary(
                thread_id=t["id"],
                snippet=t.get("snippet", ""),
                history_id=t.get("historyId", ""),
            )
            for t in response.get("threads", [])
        ]

        return ThreadListResult(
            threads=threads,
            next_page_token=response.get("nextPageToken"),
            result_size_estimate=response.get("resultSizeEstimate", 0),
        )

    def register_watch(
        self,
        topic_name: str,
        label_ids: list[str] | None = None,
    ) -> WatchRegistration:
        """Register a Gmail push watch and return the registration details."""
        body: dict[str, Any] = {
            "topicName": topic_name,
            "labelIds": label_ids if label_ids is not None else ["INBOX"],
            "labelFilterBehavior": "INCLUDE",
        }
        try:
            response = self._get_service().users().watch(userId="me", body=body).execute()
        except RefreshError as exc:
            raise GmailAuthError("Google credentials expired or revoked") from exc
        except HttpError as exc:
            raise GmailAPIError(exc.resp.status, exc._get_reason()) from exc

        return WatchRegistration(
            history_id=str(response["historyId"]),
            expiration_ms=int(response["expiration"]),
        )

    def list_history(
        self,
        start_history_id: str,
        label_id: str = "INBOX",
    ) -> HistoryListResult:
        """Fetch history records since start_history_id and return new thread IDs."""
        try:
            response = (
                self._get_service()
                .users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=start_history_id,
                    labelId=label_id,
                    historyTypes=["messageAdded"],
                )
                .execute()
            )
        except RefreshError as exc:
            raise GmailAuthError("Google credentials expired or revoked") from exc
        except HttpError as exc:
            raise GmailAPIError(exc.resp.status, exc._get_reason()) from exc

        records: list[HistoryRecord] = []
        for entry in response.get("history", []):
            seen_threads: set[str] = set()
            thread_ids: list[str] = []
            for msg_added in entry.get("messagesAdded", []):
                tid = msg_added.get("message", {}).get("threadId")
                if tid and tid not in seen_threads:
                    seen_threads.add(tid)
                    thread_ids.append(tid)
            if thread_ids:
                records.append(
                    HistoryRecord(
                        history_id=str(entry["id"]),
                        thread_ids_added=thread_ids,
                    )
                )

        return HistoryListResult(
            records=records,
            history_id=str(response.get("historyId", start_history_id)),
        )

    def get_thread(self, thread_id: str) -> ThreadDetail:
        """Fetch a complete thread by ID and return all messages parsed."""
        try:
            response = (
                self._get_service()
                .users()
                .threads()
                .get(userId="me", id=thread_id, format="full")
                .execute()
            )
        except RefreshError as exc:
            raise GmailAuthError("Google credentials expired or revoked") from exc
        except HttpError as exc:
            raise GmailAPIError(exc.resp.status, exc._get_reason()) from exc

        messages = [
            self._parse_message(msg) for msg in response.get("messages", [])
        ]

        return ThreadDetail(
            thread_id=response["id"],
            messages=messages,
            history_id=response.get("historyId", ""),
        )

    # ── Internal parsing helpers ──────────────────────────────────────────

    @staticmethod
    def _parse_message(raw: dict[str, Any]) -> ParsedMessage:
        payload = raw.get("payload", {})
        headers = payload.get("headers", [])

        internal_date_ms = int(raw.get("internalDate", "0"))
        date = datetime.fromtimestamp(internal_date_ms / 1000, tz=timezone.utc)

        return ParsedMessage(
            message_id=raw["id"],
            thread_id=raw["threadId"],
            subject=GmailConnector._get_header(headers, "Subject"),
            sender=GmailConnector._parse_email_address(
                GmailConnector._get_header(headers, "From")
            ),
            to=GmailConnector._parse_email_address_list(
                GmailConnector._get_header(headers, "To")
            ),
            cc=GmailConnector._parse_email_address_list(
                GmailConnector._get_header(headers, "Cc")
            ),
            date=date,
            body_plain=GmailConnector._extract_body(payload, "text/plain"),
            body_html=GmailConnector._extract_body(payload, "text/html"),
            labels=raw.get("labelIds", []),
            snippet=raw.get("snippet", ""),
        )

    @staticmethod
    def _extract_body(payload: dict[str, Any], mime_type: str = "text/plain") -> str:
        """Recursively walk the MIME tree and return the first matching body."""
        if payload.get("mimeType") == mime_type:
            data = payload.get("body", {}).get("data")
            if data:
                return GmailConnector._decode_body_data(data)

        for part in payload.get("parts", []):
            result = GmailConnector._extract_body(part, mime_type)
            if result:
                return result

        return ""

    @staticmethod
    def _decode_body_data(data: str) -> str:
        """Base64url-decode a Gmail body.data string to UTF-8 text.

        Gmail omits base64 padding ('='). Appending '==' before decoding is
        safe because the standard ignores excess padding characters.
        """
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    @staticmethod
    def _get_header(headers: list[dict[str, str]], name: str) -> str:
        """Case-insensitive header lookup; returns '' if not found."""
        name_lower = name.lower()
        for h in headers:
            if h.get("name", "").lower() == name_lower:
                return h.get("value", "")
        return ""

    @staticmethod
    def _parse_email_address(raw: str) -> EmailAddress:
        """Parse a single RFC 5322 address string into an EmailAddress."""
        if not raw:
            return EmailAddress(name="", email="")
        pairs = email.utils.getaddresses([raw])
        if pairs:
            name, addr = pairs[0]
            return EmailAddress(name=name, email=addr)
        return EmailAddress(name="", email=raw.strip())

    @staticmethod
    def _parse_email_address_list(raw: str) -> list[EmailAddress]:
        """Parse a comma-separated RFC 5322 address list."""
        if not raw:
            return []
        return [
            EmailAddress(name=name, email=addr)
            for name, addr in email.utils.getaddresses([raw])
        ]
