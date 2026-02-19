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
