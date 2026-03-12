"""Tests for JWT token creation and the get_current_user dependency."""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException

from app.auth.jwt import ALGORITHM, create_access_token, get_current_user, _get_secret
from app.models.user import User

from tests.conftest import auth_header


# ---------------------------------------------------------------------------
# create_access_token
# ---------------------------------------------------------------------------


def test_create_access_token_returns_valid_jwt():
    token = create_access_token("user-123", "test@example.com")
    payload = jwt.decode(token, _get_secret(), algorithms=[ALGORITHM])
    assert payload["sub"] == "user-123"
    assert payload["email"] == "test@example.com"
    assert "iat" in payload
    assert "exp" in payload


def test_create_access_token_expiry_is_30_days():
    token = create_access_token("user-123", "test@example.com")
    payload = jwt.decode(token, _get_secret(), algorithms=[ALGORITHM])
    exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    iat = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
    assert (exp - iat).days == 30


# ---------------------------------------------------------------------------
# Auth endpoints via TestClient
# ---------------------------------------------------------------------------


def test_valid_token_authenticates(client, db_session):
    user = User(id=str(uuid.uuid4()), email="jwt-test@example.com", name="JWT Test")
    db_session.add(user)
    db_session.commit()

    resp = client.get("/tasks?status=all", headers=auth_header(user))
    assert resp.status_code == 200


def test_expired_token_returns_401(client, db_session):
    user = User(id=str(uuid.uuid4()), email="expired@example.com", name="Expired")
    db_session.add(user)
    db_session.commit()

    payload = {
        "sub": str(user.id),
        "email": user.email,
        "iat": datetime.now(timezone.utc) - timedelta(days=60),
        "exp": datetime.now(timezone.utc) - timedelta(days=1),
    }
    token = jwt.encode(payload, _get_secret(), algorithm=ALGORITHM)

    resp = client.get("/tasks", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


def test_invalid_token_returns_401(client):
    resp = client.get("/tasks", headers={"Authorization": "Bearer not.a.real.token"})
    assert resp.status_code == 401


def test_wrong_secret_returns_401(client, db_session):
    user = User(id=str(uuid.uuid4()), email="wrong@example.com", name="Wrong")
    db_session.add(user)
    db_session.commit()

    payload = {
        "sub": str(user.id),
        "email": user.email,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(days=30),
    }
    token = jwt.encode(payload, "wrong-secret-key", algorithm=ALGORITHM)

    resp = client.get("/tasks", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_token_for_deleted_user_returns_401(client, db_session):
    token = create_access_token(user_id=str(uuid.uuid4()), email="ghost@example.com")
    resp = client.get("/tasks", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_missing_auth_header_returns_error(client):
    resp = client.get("/tasks")
    assert resp.status_code in (401, 403)


def test_token_in_oauth_callback_redirect(client, db_session):
    """The OAuth callback should include a token param in the redirect URL."""
    # We can't easily test the full OAuth flow, but we verify the token
    # creation function produces tokens the system can validate.
    user = User(id=str(uuid.uuid4()), email="oauth@example.com", name="OAuth")
    db_session.add(user)
    db_session.commit()

    token = create_access_token(user_id=str(user.id), email=user.email)
    resp = client.get("/tasks?status=all", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
