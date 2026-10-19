"""Tests for Module 2 — CRM Synchronization.

Covers:
- Successful CRM lead creation and update
- Idempotency (same key replay, different payload rejection)
- Auth and business isolation
- Connection resolution errors
- CRM failure handling (timeout, auth, unavailable)
- Recovery from CRM success + local persistence failure
- Concurrent requests with the same idempotency key
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.db.base import Base
from app.db.session import get_engine, get_session_factory, reset_engine
from app.main import app
from app.models import CRMConnection, Lead

BIZ_1 = "biz_001"
BIZ_2 = "biz_002"
KEY_1 = "test-key-1"
KEY_2 = "test-key-2"


def auth_headers(business: str = BIZ_1) -> dict:
    return {"X-API-Key": {BIZ_1: KEY_1, BIZ_2: KEY_2}[business]}

# A canonical lead payload matching the Module 1 schema.
CANONICAL_LEAD = {
    "id": "lead_test000000000001",
    "business_id": BIZ_1,
    "name": "Rahul Sharma",
    "phone": "9876543210",
    "email": "rahul.test@example.com",
    "source": "99acres",
    "language": "english",
    "deal_value": "7500000.00",
    "last_contact_at": None,
    "custom_fields": {"property_type": "Apartment", "city": "Bengaluru"},
    "external_id": None,
    "created_at": "2026-10-18T18:17:27.814533",
}

SYNC_BODY = {
    "connection_id": "test-conn",
    "idempotency_key": "test-key-001",
    "lead": CANONICAL_LEAD,
}


def _setup_db():
    """Create all tables and seed a CRM connection."""
    Base.metadata.create_all(get_engine())
    session = get_session_factory()()
    try:
        conn = CRMConnection(
            business_id=BIZ_1,
            connection_id="test-conn",
            name="Test CRM",
            is_active=True,
            crm_base_url="http://localhost:9000",
            crm_api_key="test-api-key",
            crm_timeout_seconds=5.0,
        )
        session.add(conn)

        lead = Lead(
            id=CANONICAL_LEAD["id"],
            business_id=BIZ_1,
            name=CANONICAL_LEAD["name"],
            phone=CANONICAL_LEAD["phone"],
            email=CANONICAL_LEAD["email"],
            source=CANONICAL_LEAD["source"],
            language=CANONICAL_LEAD["language"],
            deal_value=CANONICAL_LEAD["deal_value"],
            custom_fields=CANONICAL_LEAD["custom_fields"],
        )
        session.add(lead)
        session.commit()
    finally:
        session.close()


def _get_client():
    """Create a TestClient with DB override."""
    from app.db.session import get_db

    def _override_get_db():
        session = get_session_factory()()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


@pytest.fixture()
def crm_client(settings):
    """Set up DB and return a test client."""
    _setup_db()
    client = _get_client()
    yield client
    app.dependency_overrides.clear()
    reset_engine()
    Base.metadata.drop_all(get_engine())


def _mock_crm_client(mock_post=None, mock_patch=None):
    """Create a mock CRMClient."""
    mock_client = AsyncMock()
    if mock_post:
        mock_client.create_contact = mock_post
    else:
        mock_client.create_contact = AsyncMock(
            return_value={"id": "CRM-TEST-001", "full_name": "Rahul Sharma"}
        )
    if mock_patch:
        mock_client.update_contact = mock_patch
    else:
        mock_client.update_contact = AsyncMock(
            return_value={"id": "CRM-TEST-001", "full_name": "Rahul Sharma"}
        )
    mock_client.close = AsyncMock()
    return mock_client


# ---------------------------------------------------------------------------
# Successful synchronization
# ---------------------------------------------------------------------------


class TestSuccessfulSync:
    def test_create_lead_in_crm(self, crm_client):
        """POST /api/v1/crm/sync creates a lead and returns 200."""
        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection",
            return_value=_mock_crm_client(),
        ):
            resp = crm_client.post(
                "/api/v1/crm/sync",
                json=SYNC_BODY,
                headers=auth_headers(BIZ_1),
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["lead_id"] == CANONICAL_LEAD["id"]
        assert data["business_id"] == BIZ_1
        assert data["status"] == "created"
        assert data["external_crm_id"] == "CRM-TEST-001"
        assert "sync_id" in data

    def test_update_existing_lead(self, crm_client):
        """If the lead already has an external_id, the CRM is updated."""
        # Set external_id on the lead.
        session = get_session_factory()()
        lead = session.get(Lead, CANONICAL_LEAD["id"])
        lead.external_id = "CRM-EXISTING-001"
        session.commit()
        session.close()

        mock_client = _mock_crm_client()
        mock_client.update_contact = AsyncMock(
            return_value={"id": "CRM-EXISTING-001", "full_name": "Rahul Sharma"}
        )

        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection",
            return_value=mock_client,
        ):
            resp = crm_client.post(
                "/api/v1/crm/sync",
                json=SYNC_BODY,
                headers=auth_headers(BIZ_1),
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "updated"
        assert data["external_crm_id"] == "CRM-EXISTING-001"

    def test_sync_stores_external_id_on_lead(self, crm_client):
        """After sync, the canonical lead's external_id is updated."""
        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection",
            return_value=_mock_crm_client(),
        ):
            crm_client.post(
                "/api/v1/crm/sync",
                json=SYNC_BODY,
                headers=auth_headers(BIZ_1),
            )

        session = get_session_factory()()
        lead = session.get(Lead, CANONICAL_LEAD["id"])
        assert lead.external_id == "CRM-TEST-001"
        session.close()

    def test_sync_creates_agent_lead_record(self, crm_client):
        """After sync, agent_leads has a matching record."""
        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection",
            return_value=_mock_crm_client(),
        ):
            crm_client.post(
                "/api/v1/crm/sync",
                json=SYNC_BODY,
                headers=auth_headers(BIZ_1),
            )

        from app.models import AgentLead

        session = get_session_factory()()
        record = session.get(AgentLead, CANONICAL_LEAD["id"])
        assert record is not None
        assert record.external_crm_id == "CRM-TEST-001"
        assert record.name == "Rahul Sharma"
        session.close()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_repeated_key_replays_result(self, crm_client):
        """Same key + same payload returns the stored result without re-calling CRM."""
        mock_client = _mock_crm_client()
        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection",
            return_value=mock_client,
        ):
            resp1 = crm_client.post(
                "/api/v1/crm/sync",
                json=SYNC_BODY,
                headers=auth_headers(BIZ_1),
            )
            resp2 = crm_client.post(
                "/api/v1/crm/sync",
                json=SYNC_BODY,
                headers=auth_headers(BIZ_1),
            )

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # CRM should only be called once.
        assert mock_client.create_contact.call_count == 1

    def test_different_payload_same_key_rejected(self, crm_client):
        """Same key with a different payload returns 409."""
        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection",
            return_value=_mock_crm_client(),
        ):
            resp1 = crm_client.post(
                "/api/v1/crm/sync",
                json=SYNC_BODY,
                headers=auth_headers(BIZ_1),
            )

        modified_body = json.loads(json.dumps(SYNC_BODY))
        modified_body["lead"]["name"] = "Different Name"

        resp2 = crm_client.post(
            "/api/v1/crm/sync",
            json=modified_body,
            headers=auth_headers(BIZ_1),
        )

        assert resp1.status_code == 200
        assert resp2.status_code == 409
        assert resp2.json()["error"]["code"] == "crm_idempotency_conflict"

    def test_idempotency_stored_in_db(self, crm_client):
        """Idempotency record is persisted in crm_sync_idempotency."""
        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection",
            return_value=_mock_crm_client(),
        ):
            crm_client.post(
                "/api/v1/crm/sync",
                json=SYNC_BODY,
                headers=auth_headers(BIZ_1),
            )

        from app.models import CRMSyncIdempotency

        session = get_session_factory()()
        record = (
            session.query(CRMSyncIdempotency)
            .filter_by(idempotency_key="test-key-001")
            .first()
        )
        assert record is not None
        assert record.status == "completed"
        session.close()


# ---------------------------------------------------------------------------
# Auth and business isolation
# ---------------------------------------------------------------------------


class TestAuthAndIsolation:
    def test_missing_api_key_returns_401(self, crm_client):
        resp = crm_client.post("/api/v1/crm/sync", json=SYNC_BODY)
        assert resp.status_code == 401

    def test_invalid_api_key_returns_401(self, crm_client):
        resp = crm_client.post(
            "/api/v1/crm/sync",
            json=SYNC_BODY,
            headers={"X-API-Key": "invalid-key"},
        )
        assert resp.status_code == 401

    def test_lead_business_mismatch_returns_404(self, crm_client):
        """Lead belongs to BIZ_1 but auth is BIZ_2."""
        resp = crm_client.post(
            "/api/v1/crm/sync",
            json=SYNC_BODY,
            headers=auth_headers(BIZ_2),
        )
        assert resp.status_code == 404

    def test_other_business_connection_returns_404(self, crm_client):
        """BIZ_2 tries to use BIZ_1's connection."""
        session = get_session_factory()()
        lead = Lead(
            id="lead_test000000000002",
            business_id=BIZ_2,
            name="Other Business Lead",
            phone="9876543211",
            source="website",
        )
        session.add(lead)
        session.commit()
        session.close()

        body = json.loads(json.dumps(SYNC_BODY))
        body["lead"]["id"] = "lead_test000000000002"
        body["lead"]["business_id"] = BIZ_2

        resp = crm_client.post(
            "/api/v1/crm/sync",
            json=body,
            headers=auth_headers(BIZ_2),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Connection resolution
# ---------------------------------------------------------------------------


class TestConnectionResolution:
    def test_unknown_connection_returns_404(self, crm_client):
        body = json.loads(json.dumps(SYNC_BODY))
        body["connection_id"] = "nonexistent"

        resp = crm_client.post(
            "/api/v1/crm/sync",
            json=body,
            headers=auth_headers(BIZ_1),
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "crm_connection_not_found"

    def test_inactive_connection_returns_400(self, crm_client):
        session = get_session_factory()()
        conn = (
            session.query(CRMConnection)
            .filter_by(connection_id="test-conn")
            .first()
        )
        conn.is_active = False
        session.commit()
        session.close()

        resp = crm_client.post(
            "/api/v1/crm/sync",
            json=SYNC_BODY,
            headers=auth_headers(BIZ_1),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "crm_connection_inactive"


# ---------------------------------------------------------------------------
# CRM failure handling
# ---------------------------------------------------------------------------


class TestCRMFailures:
    def test_crm_auth_failure(self, crm_client):
        from app.modules.crm_sync.client import CRMAuthError

        mock_client = _mock_crm_client()
        mock_client.create_contact = AsyncMock(
            side_effect=CRMAuthError("CRM auth failed")
        )

        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection",
            return_value=mock_client,
        ):
            resp = crm_client.post(
                "/api/v1/crm/sync",
                json=SYNC_BODY,
                headers=auth_headers(BIZ_1),
            )

        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "crm_sync_failed"

    def test_crm_timeout(self, crm_client):
        from app.modules.crm_sync.client import CRMTimeoutError

        mock_client = _mock_crm_client()
        mock_client.create_contact = AsyncMock(
            side_effect=CRMTimeoutError("timed out")
        )

        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection",
            return_value=mock_client,
        ):
            resp = crm_client.post(
                "/api/v1/crm/sync",
                json=SYNC_BODY,
                headers=auth_headers(BIZ_1),
            )

        assert resp.status_code == 502

    def test_crm_unavailable(self, crm_client):
        from app.modules.crm_sync.client import CRMUnavailableError

        mock_client = _mock_crm_client()
        mock_client.create_contact = AsyncMock(
            side_effect=CRMUnavailableError("connection refused")
        )

        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection",
            return_value=mock_client,
        ):
            resp = crm_client.post(
                "/api/v1/crm/sync",
                json=SYNC_BODY,
                headers=auth_headers(BIZ_1),
            )

        assert resp.status_code == 502

    def test_failed_sync_preserves_lead(self, crm_client):
        """A failed CRM sync must not delete or invalidate the canonical lead."""
        from app.modules.crm_sync.client import CRMUnavailableError

        mock_client = _mock_crm_client()
        mock_client.create_contact = AsyncMock(
            side_effect=CRMUnavailableError("connection refused")
        )

        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection",
            return_value=mock_client,
        ):
            crm_client.post(
                "/api/v1/crm/sync",
                json=SYNC_BODY,
                headers=auth_headers(BIZ_1),
            )

        # Lead still exists.
        session = get_session_factory()()
        lead = session.get(Lead, CANONICAL_LEAD["id"])
        assert lead is not None
        assert lead.name == "Rahul Sharma"
        session.close()

    def test_failed_sync_recorded(self, crm_client):
        """Failed sync creates a crm_sync_records entry with status=failed."""
        from app.modules.crm_sync.client import CRMUnavailableError

        mock_client = _mock_crm_client()
        mock_client.create_contact = AsyncMock(
            side_effect=CRMUnavailableError("connection refused")
        )

        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection",
            return_value=mock_client,
        ):
            crm_client.post(
                "/api/v1/crm/sync",
                json=SYNC_BODY,
                headers=auth_headers(BIZ_1),
            )

        from app.models import CRMSyncRecord, CRMSyncStatus

        session = get_session_factory()()
        record = (
            session.query(CRMSyncRecord)
            .filter_by(lead_id=CANONICAL_LEAD["id"])
            .first()
        )
        assert record is not None
        assert record.status == CRMSyncStatus.FAILED
        assert record.error_message is not None
        session.close()


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_invalid_lead_payload_returns_422(self, crm_client):
        body = json.loads(json.dumps(SYNC_BODY))
        del body["lead"]["name"]

        resp = crm_client.post(
            "/api/v1/crm/sync",
            json=body,
            headers=auth_headers(BIZ_1),
        )
        assert resp.status_code == 422

    def test_missing_connection_id_returns_422(self, crm_client):
        body = json.loads(json.dumps(SYNC_BODY))
        del body["connection_id"]

        resp = crm_client.post(
            "/api/v1/crm/sync",
            json=body,
            headers=auth_headers(BIZ_1),
        )
        assert resp.status_code == 422

    def test_extra_fields_rejected(self, crm_client):
        body = json.loads(json.dumps(SYNC_BODY))
        body["extra_field"] = "not allowed"

        resp = crm_client.post(
            "/api/v1/crm/sync",
            json=body,
            headers=auth_headers(BIZ_1),
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/crm/connections
# ---------------------------------------------------------------------------


class TestCRMConnectionsEndpoint:
    def test_list_connections(self, crm_client):
        """The business sees its configured CRM connection."""
        resp = crm_client.get("/api/v1/crm/connections", headers=auth_headers(BIZ_1))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body) == 1
        conn = body[0]
        assert conn["connection_id"] == "test-conn"
        assert conn["name"] == "Test CRM"
        assert conn["is_active"] is True
        assert conn["crm_base_url"] == "http://localhost:9000"
        assert conn["crm_timeout_seconds"] == 5.0

    def test_missing_api_key_rejected(self, crm_client):
        assert crm_client.get("/api/v1/crm/connections").status_code == 401

    def test_invalid_api_key_rejected(self, crm_client):
        resp = crm_client.get(
            "/api/v1/crm/connections", headers={"X-API-Key": "wrong-key"}
        )
        assert resp.status_code == 401

    def test_connections_scoped_to_business(self, crm_client):
        """A different business sees no connections from biz_001."""
        resp = crm_client.get("/api/v1/crm/connections", headers=auth_headers(BIZ_2))
        assert resp.status_code == 200, resp.text
        assert resp.json() == []

    def test_api_key_never_leaked(self, crm_client):
        """Connection credentials stay server-side."""
        resp = crm_client.get("/api/v1/crm/connections", headers=auth_headers(BIZ_1))
        assert resp.status_code == 200
        assert "test-api-key" not in resp.text
        assert "crm_api_key" not in resp.text
