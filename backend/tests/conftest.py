"""Test fixtures.

Tests run against SQLite so the suite is fast and dependency-free; all
SQLAlchemy models use portable types (JSON works on both SQLite and
PostgreSQL). Each test gets an isolated database file.
"""

import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# Ensure deterministic test settings before the app module is imported.
os.environ.setdefault("CALLORA_DEBUG", "true")

from app.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db, get_engine, get_session_factory, reset_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Lead  # noqa: E402
from app.modules.voice import repository as voice_repo  # noqa: E402
from app.modules.voice.models import Business  # noqa: E402

BIZ_1 = "biz_001"
BIZ_2 = "biz_002"
KEY_1 = "test-key-1"
KEY_2 = "test-key-2"

# --- Outbound voice agent (Module 6) ---------------------------------------
VOICE_BIZ = BIZ_1
VOICE_LEAD_ID = "lead_voice_0001"
VOICE_PHONE = "+919876543210"
#: Wednesday 2026-03-11, 12:00 in Asia/Kolkata (06:30 UTC).
VOICE_NOON_UTC = datetime(2026, 3, 11, 6, 30, tzinfo=timezone.utc)


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


# ---------------------------------------------------------------------------
# Outbound voice agent (Module 6) fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def voice_settings(settings, monkeypatch):
    """Settings for the outbound voice module.

    Outbound calling is switched off, so no test can reach the voice
    provider by accident: the provider is exercised exclusively through
    the ``FakeProvider`` double from ``tests.fakes``.
    """
    monkeypatch.setenv("CALLORA_VOICE_ENABLE_OUTBOUND_CALLS", "false")
    monkeypatch.setenv("CALLORA_BOLNA_API_KEY", "test-bolna-key")
    monkeypatch.setenv("CALLORA_BOLNA_DEFAULT_AGENT_ID", "agent_test")
    monkeypatch.setenv("CALLORA_BOLNA_DEFAULT_FROM_NUMBER", "+911800000000")
    # No Redis in the test suite: the rate limiter degrades to a no-op.
    monkeypatch.setenv("CALLORA_REDIS_ENABLED", "false")
    # No LLM: the post-call workflow degrades to the provider payload.
    monkeypatch.setenv("CALLORA_VOICE_LLM_PROVIDER", "null")
    # The duplicate-call cooldown has its own test; elsewhere it would
    # mask the rule under test.
    monkeypatch.setenv("CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_MINUTES", "0")
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture()
def voice_business(db, voice_settings):
    """A permissive business voice configuration."""
    business = Business(
        id=VOICE_BIZ,
        name="Test Realty",
        timezone="Asia/Kolkata",
        default_language="en",
        supported_languages=["en", "hi", "kn"],
        # An always-open calling window: the calling-hours rule has its
        # own tests.
        calling_window_start=0,
        calling_window_end=24,
        calling_days=[],
        max_calls_per_lead_per_day=5,
        max_calls_per_business_per_day=100,
        max_concurrent_calls=5,
        require_consent=True,
        dnd_enabled=True,
        bolna_agent_id="agent_test",
        bolna_from_number="+911800000000",
        agent_instructions="You are a sales agent for Test Realty.",
        qualification_questions=[
            {"key": "budget", "question": "What is your budget?", "type": "text"},
            {"key": "timeline", "question": "When are you buying?", "type": "text"},
        ],
        is_active=True,
    )
    db.add(business)
    db.commit()
    return business


@pytest.fixture()
def voice_lead(db, voice_business):
    """A lead with a callable phone number."""
    lead = Lead(
        id=VOICE_LEAD_ID,
        business_id=VOICE_BIZ,
        name="Rahul Sharma",
        phone=VOICE_PHONE,
        source="test",
        language="hi",
        custom_fields={"budget": "5000000"},
    )
    db.add(lead)
    db.commit()
    return lead


@pytest.fixture()
def voice_consent(db, voice_business, voice_lead):
    """A granted voice consent record for the lead."""
    record = voice_repo.record_consent(
        db,
        business_id=VOICE_BIZ,
        lead_id=voice_lead.id,
        channel="voice",
        status="granted",
        phone=VOICE_PHONE,
        source="test",
        granted_at=datetime.now(timezone.utc),
    )
    db.commit()
    return record


@pytest.fixture()
def voice_client(voice_settings, client):
    """A test client configured for the voice module."""
    return client
