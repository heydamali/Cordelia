"""Tests for health endpoint, User model encryption, and Google OAuth flow."""

from unittest.mock import MagicMock, patch
from urllib.parse import urlparse, parse_qs

from app.models.user import User


# ── Health endpoint ──────────────────────────────────────────────────────


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── User model – token encryption ────────────────────────────────────────


def test_set_and_get_refresh_token(db_session):
    user = User(email="enc@test.com", google_id="g1")
    user.set_refresh_token("my-secret-token")
    db_session.add(user)
    db_session.flush()

    assert user.get_refresh_token() == "my-secret-token"


def test_get_refresh_token_none(db_session):
    user = User(email="none@test.com", google_id="g2")
    db_session.add(user)
    db_session.flush()

    assert user.get_refresh_token() is None


def test_encrypted_token_differs_from_plain(db_session):
    user = User(email="diff@test.com", google_id="g3")
    user.set_refresh_token("plaintext-value")
    db_session.add(user)
    db_session.flush()

    assert user.encrypted_refresh_token != "plaintext-value"


# ── Auth redirect ────────────────────────────────────────────────────────


def _mock_flow():
    """Return a mock Flow whose authorization_url returns a deterministic URL."""
    flow = MagicMock()
    flow.authorization_url.return_value = (
        "https://accounts.google.com/o/oauth2/auth"
        "?scope=openid+email"
        "&access_type=offline"
        "&prompt=consent",
        "state-token",
    )
    return flow


def test_auth_google_redirects(client):
    with patch("app.api.auth._create_flow", return_value=_mock_flow()):
        resp = client.get("/auth/google", follow_redirects=False)

    assert resp.status_code == 307
    location = resp.headers["location"]
    parsed = urlparse(location)
    qs = parse_qs(parsed.query)

    assert parsed.hostname == "accounts.google.com"
    assert "offline" in qs.get("access_type", [])
    assert "consent" in qs.get("prompt", [])


# ── OAuth callback (mocked Google APIs) ──────────────────────────────────


def _mock_flow_with_credentials(refresh_token="fake-refresh-token"):
    """Return a mock Flow whose fetch_token populates controlled credentials."""
    creds = MagicMock()
    creds.refresh_token = refresh_token
    creds.token = "fake-access-token"

    flow = MagicMock()
    flow.credentials = creds
    return flow


def _mock_build(email="user@example.com", google_id="123", name="Test User"):
    """Return a mock ``build()`` that yields a fake oauth2 userinfo service."""
    service = MagicMock()
    service.userinfo.return_value.get.return_value.execute.return_value = {
        "email": email,
        "id": google_id,
        "name": name,
    }
    return service


def test_callback_creates_user(client, db_session):
    flow = _mock_flow_with_credentials(refresh_token="refresh-abc")
    service = _mock_build(email="new@example.com", google_id="g100", name="New User")

    with (
        patch("app.api.auth._create_flow", return_value=flow),
        patch("app.api.auth.build", return_value=service),
        patch("app.api.auth.initial_gmail_sync"),
    ):
        resp = client.get("/auth/google/callback", params={"code": "fake"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "new@example.com"
    assert "user_id" in data

    # Verify the user was persisted with an encrypted token
    user = db_session.query(User).filter(User.email == "new@example.com").first()
    assert user is not None
    assert user.get_refresh_token() == "refresh-abc"


def test_callback_updates_existing_user(client, db_session):
    # Pre-insert user
    existing = User(email="existing@example.com", google_id="g200", name="Old Name")
    existing.set_refresh_token("old-token")
    db_session.add(existing)
    db_session.commit()

    flow = _mock_flow_with_credentials(refresh_token="new-refresh")
    service = _mock_build(
        email="existing@example.com", google_id="g200", name="New Name"
    )

    with (
        patch("app.api.auth._create_flow", return_value=flow),
        patch("app.api.auth.build", return_value=service),
    ):
        resp = client.get("/auth/google/callback", params={"code": "fake"})

    assert resp.status_code == 200

    # Should still be one row, not two
    users = db_session.query(User).filter(User.email == "existing@example.com").all()
    assert len(users) == 1
    assert users[0].get_refresh_token() == "new-refresh"
    assert users[0].name == "New Name"


def test_callback_no_refresh_token(client):
    flow = _mock_flow_with_credentials(refresh_token=None)
    service = _mock_build()

    with (
        patch("app.api.auth._create_flow", return_value=flow),
        patch("app.api.auth.build", return_value=service),
    ):
        resp = client.get("/auth/google/callback", params={"code": "fake"})

    assert resp.status_code == 400
    assert "refresh token" in resp.json()["detail"].lower()


# ── Watch registration during OAuth callback ──────────────────────────────────


def _mock_watch_registration(history_id="99999", expiration_ms=9999999999000):
    from app.services.gmail_connector import WatchRegistration
    return WatchRegistration(history_id=history_id, expiration_ms=expiration_ms)


def _mock_connector(history_id="99999"):
    """Return a MagicMock connector whose register_watch returns a proper WatchRegistration."""
    connector = MagicMock()
    connector.register_watch.return_value = _mock_watch_registration(history_id=history_id)
    return connector


def test_callback_registers_watch_and_stores_history_id(client, db_session):
    flow = _mock_flow_with_credentials(refresh_token="refresh-watch")
    service = _mock_build(email="watch@example.com", google_id="g_watch")
    reg = _mock_watch_registration(history_id="12345")

    mock_connector = MagicMock()
    mock_connector.register_watch.return_value = reg

    with (
        patch("app.api.auth._create_flow", return_value=flow),
        patch("app.api.auth.build", return_value=service),
        patch("app.api.auth.GmailConnector", return_value=mock_connector),
        patch("app.api.auth.initial_gmail_sync"),
    ):
        resp = client.get("/auth/google/callback", params={"code": "fake"})

    assert resp.status_code == 200
    user = db_session.query(User).filter(User.email == "watch@example.com").first()
    assert user.gmail_history_id == "12345"
    assert user.gmail_watch_expiry is not None


def test_callback_watch_failure_does_not_fail_oauth(client, db_session):
    from app.services.gmail_connector import GmailAPIError

    flow = _mock_flow_with_credentials(refresh_token="refresh-watchfail")
    service = _mock_build(email="watchfail@example.com", google_id="g_watchfail")

    mock_connector = MagicMock()
    mock_connector.register_watch.side_effect = GmailAPIError(403, "forbidden")

    with (
        patch("app.api.auth._create_flow", return_value=flow),
        patch("app.api.auth.build", return_value=service),
        patch("app.api.auth.GmailConnector", return_value=mock_connector),
        patch("app.api.auth.initial_gmail_sync"),
    ):
        resp = client.get("/auth/google/callback", params={"code": "fake"})

    # OAuth must still succeed even when watch registration fails
    assert resp.status_code == 200
    user = db_session.query(User).filter(User.email == "watchfail@example.com").first()
    assert user is not None
    assert user.gmail_history_id is None


# ── app_redirect / mobile OAuth state ─────────────────────────────────────────


def test_auth_google_with_app_redirect_encodes_state_in_authorization_url(client):
    """When app_redirect is provided the authorization URL carries an encoded state."""
    import base64

    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = (
        "https://accounts.google.com/o/oauth2/auth?foo=bar",
        "ignored-state",
    )

    with patch("app.api.auth._create_flow", return_value=mock_flow):
        resp = client.get(
            "/auth/google",
            params={"app_redirect": "cordelia://auth/callback"},
            follow_redirects=False,
        )

    assert resp.status_code == 307
    _, call_kwargs = mock_flow.authorization_url.call_args
    assert "state" in call_kwargs

    # The state must decode back to the original app_redirect URL
    state = call_kwargs["state"]
    padding = "=" * ((4 - len(state) % 4) % 4)
    decoded = base64.urlsafe_b64decode(state + padding).decode()
    assert decoded == "cordelia://auth/callback"


def test_auth_google_without_app_redirect_has_no_state(client):
    """Browser-initiated OAuth carries no state."""
    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = (
        "https://accounts.google.com/o/oauth2/auth?foo=bar",
        None,
    )

    with patch("app.api.auth._create_flow", return_value=mock_flow):
        client.get("/auth/google", follow_redirects=False)

    _, call_kwargs = mock_flow.authorization_url.call_args
    assert "state" not in call_kwargs


def test_callback_with_state_redirects_to_mobile_app(client, db_session):
    """When state carries an app_redirect, the callback issues a 307 to that URI."""
    import base64
    from urllib.parse import urlparse, parse_qs

    app_redirect = "cordelia://auth/callback"
    state = base64.urlsafe_b64encode(app_redirect.encode()).decode().rstrip("=")

    flow = _mock_flow_with_credentials(refresh_token="refresh-mobile")
    service = _mock_build(email="mobile@example.com", google_id="g_mobile")

    with (
        patch("app.api.auth._create_flow", return_value=flow),
        patch("app.api.auth.build", return_value=service),
        patch("app.api.auth.GmailConnector", return_value=_mock_connector()),
        patch("app.api.auth.initial_gmail_sync"),
    ):
        resp = client.get(
            "/auth/google/callback",
            params={"code": "fake", "state": state},
            follow_redirects=False,
        )

    assert resp.status_code == 307
    location = resp.headers["location"]
    assert location.startswith("cordelia://auth/callback")

    parsed = urlparse(location)
    qs = parse_qs(parsed.query)
    assert "user_id" in qs
    assert qs["email"][0] == "mobile@example.com"


def test_callback_without_state_returns_json(client, db_session):
    """Browser OAuth (no state) still returns JSON as before."""
    flow = _mock_flow_with_credentials(refresh_token="refresh-browser")
    service = _mock_build(email="browser@example.com", google_id="g_browser")

    with (
        patch("app.api.auth._create_flow", return_value=flow),
        patch("app.api.auth.build", return_value=service),
        patch("app.api.auth.GmailConnector", return_value=_mock_connector()),
        patch("app.api.auth.initial_gmail_sync"),
    ):
        resp = client.get("/auth/google/callback", params={"code": "fake"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "browser@example.com"
    assert "user_id" in data


# ── initial_gmail_sync trigger ─────────────────────────────────────────────────


def test_callback_new_user_enqueues_initial_gmail_sync(client, db_session):
    """A brand-new user triggers initial_gmail_sync.delay with their user_id."""
    flow = _mock_flow_with_credentials(refresh_token="refresh-newsync")
    service = _mock_build(email="newsync@example.com", google_id="g_newsync")

    with (
        patch("app.api.auth._create_flow", return_value=flow),
        patch("app.api.auth.build", return_value=service),
        patch("app.api.auth.GmailConnector", return_value=_mock_connector()),
        patch("app.api.auth.initial_gmail_sync") as mock_sync,
    ):
        resp = client.get("/auth/google/callback", params={"code": "fake"})

    assert resp.status_code == 200
    mock_sync.delay.assert_called_once()

    # The user_id passed to delay must match the created user
    called_user_id = mock_sync.delay.call_args[0][0]
    user = db_session.query(User).filter(User.email == "newsync@example.com").first()
    assert user is not None
    assert called_user_id == user.id


def test_callback_existing_user_does_not_enqueue_initial_gmail_sync(client, db_session):
    """A returning user never triggers initial_gmail_sync."""
    existing = User(email="returning-sync@example.com", google_id="g_ret_sync", name="Old")
    existing.set_refresh_token("old-token")
    db_session.add(existing)
    db_session.commit()

    flow = _mock_flow_with_credentials(refresh_token="new-refresh")
    service = _mock_build(email="returning-sync@example.com", google_id="g_ret_sync")

    with (
        patch("app.api.auth._create_flow", return_value=flow),
        patch("app.api.auth.build", return_value=service),
        patch("app.api.auth.GmailConnector", return_value=_mock_connector()),
        patch("app.api.auth.initial_gmail_sync") as mock_sync,
    ):
        resp = client.get("/auth/google/callback", params={"code": "fake"})

    assert resp.status_code == 200
    mock_sync.delay.assert_not_called()


def test_callback_returning_user_without_new_refresh_token_succeeds(client, db_session):
    """A returning user whose Google session returns no new refresh token is still accepted
    and their existing stored token is left untouched."""
    existing = User(email="reauth@example.com", google_id="g_reauth", name="Re-Auth User")
    existing.set_refresh_token("stored-token")
    db_session.add(existing)
    db_session.commit()

    # Google doesn't issue a new refresh_token on re-authentication
    flow = _mock_flow_with_credentials(refresh_token=None)
    service = _mock_build(email="reauth@example.com", google_id="g_reauth")

    with (
        patch("app.api.auth._create_flow", return_value=flow),
        patch("app.api.auth.build", return_value=service),
        patch("app.api.auth.GmailConnector", return_value=_mock_connector()),
        patch("app.api.auth.initial_gmail_sync"),
    ):
        resp = client.get("/auth/google/callback", params={"code": "fake"})

    assert resp.status_code == 200

    user = db_session.query(User).filter(User.email == "reauth@example.com").first()
    assert user.get_refresh_token() == "stored-token"  # original token preserved


def test_callback_new_user_no_refresh_token_returns_400(client, db_session):
    """A brand-new user with no refresh token in the response is rejected."""
    flow = _mock_flow_with_credentials(refresh_token=None)
    service = _mock_build(email="notoken-new@example.com", google_id="g_notoken")

    with (
        patch("app.api.auth._create_flow", return_value=flow),
        patch("app.api.auth.build", return_value=service),
    ):
        resp = client.get("/auth/google/callback", params={"code": "fake"})

    assert resp.status_code == 400
    assert "refresh token" in resp.json()["detail"].lower()
