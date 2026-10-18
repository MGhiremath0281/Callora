"""Test fixtures.

Tests run against SQLite so the suite is fast and dependency-free; all
SQLAlchemy models use portable types (JSON works on both SQLite and
PostgreSQL). Each test gets an isolated database file.
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# Ensure deterministic test settings before the app module is imported.
os.environ.setdefault("CALLORA_DEBUG", "true")

from app.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db, get_engine, get_session_factory, reset_engine  # noqa: E402
from app.main import app  # noqa: E402

BIZ_1 = "biz_001"
BIZ_2 = "biz_002"
KEY_1 = "test-key-1"
KEY_2 = "test-key-2"


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("CALLORA_DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("CALLORA_API_KEYS", f"{BIZ_1}:{KEY_1},{BIZ_2}:{KEY_2}")
    monkeypatch.setenv("CALLORA_DEBUG", "true")
    get_settings.cache_clear()
    reset_engine()
    Base.metadata.create_all(get_engine())
    current = get_settings()
    yield current
    reset_engine()
    get_settings.cache_clear()


@pytest.fixture()
def db(settings) -> Session:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(settings):
    def _override_get_db():
        session = get_session_factory()()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def auth_headers(business: str = BIZ_1) -> dict:
    return {"X-API-Key": {BIZ_1: KEY_1, BIZ_2: KEY_2}[business]}
