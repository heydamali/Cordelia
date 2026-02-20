"""Shared test fixtures.

Sets environment variables BEFORE any app imports so that
``app.config.settings`` and the Fernet key in ``app.models.user``
resolve without needing a real .env file or PostgreSQL.
"""

import os

from cryptography.fernet import Fernet

# --- Environment setup (must happen before app imports) -------------------
_test_key = Fernet.generate_key().decode()

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
os.environ.setdefault("ENCRYPTION_KEY", _test_key)
os.environ.setdefault("GCP_PROJECT_ID", "test-project")
os.environ.setdefault("PUBSUB_TOPIC", "projects/test-project/topics/gmail-push")
os.environ.setdefault("PUBSUB_VERIFICATION_TOKEN", "test-verification-token")
os.environ.setdefault("INGEST_API_KEY", "test-ingest-api-key")

# --- Now it's safe to import app modules ---------------------------------
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app.database import Base, get_db
from app.main import app


# In-memory SQLite engine shared across the test session
_engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
_TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


@pytest.fixture(scope="session", autouse=True)
def _create_tables():
    """Create all tables once, drop them when the session ends."""
    Base.metadata.create_all(bind=_engine)
    yield
    Base.metadata.drop_all(bind=_engine)


@pytest.fixture()
def db_session():
    """Yield a transactional DB session that rolls back after each test."""
    connection = _engine.connect()
    transaction = connection.begin()
    session = _TestingSession(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_session):
    """FastAPI TestClient with ``get_db`` overridden to use the test session."""

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
