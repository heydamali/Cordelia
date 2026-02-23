from __future__ import annotations

import dataclasses
import uuid
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


class CalendarAuthError(Exception):
    """Raised when the user's Google credentials are invalid or revoked."""


class CalendarAPIError(Exception):
    """Raised when the Calendar API returns an HTTP error."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)


# ── Data structures ───────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class Attendee:
    email: str
    display_name: str
    response_status: str  # needsAction | declined | tentative | accepted
    self_: bool  # whether this is the authenticated user


@dataclasses.dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    summary: str
    description: str
    location: str
    start: datetime | None  # None for all-day events with only date
    end: datetime | None
    start_date: str  # ISO date string for all-day events
    end_date: str
    attendees: list[Attendee]
    organizer_email: str
    organizer_name: str
    status: str  # confirmed | tentative | cancelled
    html_link: str
    recurring_event_id: str  # empty if not recurring
    updated: datetime


@dataclasses.dataclass(frozen=True)
class EventListResult:
    events: list[CalendarEvent]
    next_page_token: str | None
    next_sync_token: str | None


@dataclasses.dataclass(frozen=True)
class WatchRegistration:
    channel_id: str
    resource_id: str
    expiration_ms: int


# ── Service ───────────────────────────────────────────────────────────────────


class CalendarConnector:
    """Wraps the Google Calendar API v3 for reading events and managing push notifications."""

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
            self._service = build("calendar", "v3", credentials=self._build_credentials())
        return self._service

    # ── Public API ────────────────────────────────────────────────────────

    def list_events(
        self,
        time_min: datetime | None = None,
        time_max: datetime | None = None,
        sync_token: str | None = None,
        page_token: str | None = None,
        max_results: int = 100,
    ) -> EventListResult:
        """List events from the primary calendar.

        If sync_token is provided, time_min/time_max are ignored (Google API requirement).
        """
        kwargs: dict[str, Any] = {
            "calendarId": "primary",
            "maxResults": max_results,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if sync_token:
            kwargs["syncToken"] = sync_token
        else:
            if time_min:
                kwargs["timeMin"] = time_min.isoformat()
            if time_max:
                kwargs["timeMax"] = time_max.isoformat()

        if page_token:
            kwargs["pageToken"] = page_token

        try:
            response = self._get_service().events().list(**kwargs).execute()
        except RefreshError as exc:
            raise CalendarAuthError("Google credentials expired or revoked") from exc
        except HttpError as exc:
            # If sync token is expired (410 Gone), let the caller handle it
            raise CalendarAPIError(exc.resp.status, exc._get_reason()) from exc

        events = [self._parse_event(item) for item in response.get("items", [])]

        return EventListResult(
            events=events,
            next_page_token=response.get("nextPageToken"),
            next_sync_token=response.get("nextSyncToken"),
        )

    def register_watch(
        self,
        channel_id: str,
        webhook_url: str,
    ) -> WatchRegistration:
        """Register a push notification channel for the primary calendar."""
        body = {
            "id": channel_id,
            "type": "web_hook",
            "address": webhook_url,
        }
        try:
            response = self._get_service().events().watch(
                calendarId="primary", body=body
            ).execute()
        except RefreshError as exc:
            raise CalendarAuthError("Google credentials expired or revoked") from exc
        except HttpError as exc:
            raise CalendarAPIError(exc.resp.status, exc._get_reason()) from exc

        return WatchRegistration(
            channel_id=response["id"],
            resource_id=response["resourceId"],
            expiration_ms=int(response["expiration"]),
        )

    def stop_watch(self, channel_id: str, resource_id: str) -> None:
        """Stop a push notification channel."""
        body = {"id": channel_id, "resourceId": resource_id}
        try:
            self._get_service().channels().stop(body=body).execute()
        except RefreshError as exc:
            raise CalendarAuthError("Google credentials expired or revoked") from exc
        except HttpError as exc:
            raise CalendarAPIError(exc.resp.status, exc._get_reason()) from exc

    # ── Internal parsing ──────────────────────────────────────────────────

    @staticmethod
    def _parse_event(raw: dict[str, Any]) -> CalendarEvent:
        start_raw = raw.get("start", {})
        end_raw = raw.get("end", {})

        start_dt = CalendarConnector._parse_datetime(start_raw)
        end_dt = CalendarConnector._parse_datetime(end_raw)

        attendees: list[Attendee] = []
        for a in raw.get("attendees", []):
            attendees.append(Attendee(
                email=a.get("email", ""),
                display_name=a.get("displayName", ""),
                response_status=a.get("responseStatus", "needsAction"),
                self_=a.get("self", False),
            ))

        organizer = raw.get("organizer", {})
        updated_str = raw.get("updated", "")
        try:
            updated = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            updated = datetime.now(timezone.utc)

        return CalendarEvent(
            event_id=raw.get("id", ""),
            summary=raw.get("summary", "(No title)"),
            description=raw.get("description", ""),
            location=raw.get("location", ""),
            start=start_dt,
            end=end_dt,
            start_date=start_raw.get("date", ""),
            end_date=end_raw.get("date", ""),
            attendees=attendees,
            organizer_email=organizer.get("email", ""),
            organizer_name=organizer.get("displayName", ""),
            status=raw.get("status", "confirmed"),
            html_link=raw.get("htmlLink", ""),
            recurring_event_id=raw.get("recurringEventId", ""),
            updated=updated,
        )

    @staticmethod
    def _parse_datetime(dt_raw: dict[str, Any]) -> datetime | None:
        """Parse a Google Calendar dateTime or date field."""
        if "dateTime" in dt_raw:
            try:
                return datetime.fromisoformat(dt_raw["dateTime"].replace("Z", "+00:00"))
            except ValueError:
                return None
        if "date" in dt_raw:
            try:
                return datetime.strptime(dt_raw["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        return None

    @staticmethod
    def generate_channel_id() -> str:
        return str(uuid.uuid4())
