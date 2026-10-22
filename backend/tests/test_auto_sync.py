"""Tests for automatic CRM synchronization after import and route registration."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.db.base import Base
from app.db.session import get_engine, get_session_factory, reset_engine
from app.main import app
from app.models import AgentLead, CRMConnection, Lead

BIZ_1 = "biz_001"
BIZ_2 = "biz_002"
KEY_1 = "test-key-1"
KEY_2 = "test-key-2"


def auth_headers(business: str = BIZ_1) -> dict:
    return {"X-API-Key": {BIZ_1: KEY_1, BIZ_2: KEY_2}[business]}


def _setup_db(with_crm: bool = True):
    """Create all tables and optionally seed a CRM connection."""
    Base.metadata.create_all(get_engine())
    session = get_session_factory()()
    try:
        if with_crm:
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


def _build_csv(header: list[str], rows: list[list]) -> bytes:
    lines = [",".join(f'"{c}"' for c in header)]
    for row in rows:
        lines.append(",".join(f'"{v}"' for v in row))
    return ("\n".join(lines) + "\n").encode("utf-8")


def _mock_crm_client():
    """Create a mock CRMClient."""
    mock_client = AsyncMock()
    mock_client.create_contact = AsyncMock(
        return_value={"id": "CRM-TEST-001", "full_name": "Test"}
    )
    mock_client.update_contact = AsyncMock(
        return_value={"id": "CRM-TEST-001", "full_name": "Test"}
    )
    mock_client.close = AsyncMock()
    return mock_client


@pytest.fixture()
def sync_client(settings):
    """Set up DB with CRM connection and return a test client."""
    _setup_db(with_crm=True)
    client = _get_client()
    yield client
    app.dependency_overrides.clear()
    reset_engine()
    Base.metadata.drop_all(get_engine())


@pytest.fixture()
def no_crm_client(settings):
    """Set up DB without CRM connection and return a test client."""
    _setup_db(with_crm=False)
    client = _get_client()
    yield client
    app.dependency_overrides.clear()
    reset_engine()
    Base.metadata.drop_all(get_engine())


# ---------------------------------------------------------------------------
# Route registration tests
# ---------------------------------------------------------------------------


def _openapi_paths() -> dict:
    """Resolved OpenAPI paths (this FastAPI version nests routers lazily)."""
    return app.openapi()["paths"]


def _business_operations() -> set:
    """Set of (method, path) for every /api/v1 operation."""
    ops = set()
    for path, item in _openapi_paths().items():
        if not path.startswith("/api/v1/"):
            continue
        for method in item:
            ops.add((method.upper(), path))
    return ops


class TestRouteRegistration:
    def test_exactly_15_business_routes(self):
        """The registered operation set is exactly the documented one.

        The count is asserted per module so a new module has to be
        reflected here explicitly instead of silently changing the
        total.
        """
        ops = _business_operations()
        # Module 1-5: leads, imports, CRM, agent leads, recovery agent
        # and recovery dashboard.
        legacy_ops = {op for op in ops if not op[1].startswith("/api/v1/voice/")}
        # Module 6: the outbound voice agent + knowledge base & follow-up Q&A.
        voice_ops = {op for op in ops if op[1].startswith("/api/v1/voice/")}
        assert len(legacy_ops) == 20, (
            f"Expected 20 non-voice operations, got {len(legacy_ops)}: "
            f"{sorted(legacy_ops)}"
        )
        assert len(voice_ops) == 39, (
            f"Expected 39 voice operations, got {len(voice_ops)}: "
            f"{sorted(voice_ops)}"
        )
        assert len(ops) == 59, f"Expected 59 operations, got {len(ops)}: {sorted(ops)}"

    def test_no_unversioned_leads_routes(self):
        """No unversioned /leads or /imports routes exist."""
        for path in _openapi_paths():
            assert not path.startswith("/leads"), path
            assert not path.startswith("/imports"), path

    def test_no_preview_endpoint(self):
        """The /leads/import/preview endpoint is removed."""
        assert "/api/v1/leads/import/preview" not in _openapi_paths()

    def test_no_double_prefix(self):
        """No route has /api/v1/api/v1."""
        for path in _openapi_paths():
            assert "/api/v1/api/v1" not in path

    def test_all_required_routes_present(self):
        """All required business routes are present."""
        ops = _business_operations()
        required = {
            ("POST", "/api/v1/leads/import"),
            ("POST", "/api/v1/leads"),
            ("GET", "/api/v1/leads"),
            ("GET", "/api/v1/leads/{lead_id}"),
            ("GET", "/api/v1/imports/{import_id}"),
            ("GET", "/api/v1/imports/{import_id}/errors"),
            ("GET", "/api/v1/crm/connections"),
            ("POST", "/api/v1/crm/connections"),
            ("POST", "/api/v1/crm/sync"),
            ("GET", "/api/v1/agent/leads"),
            ("GET", "/api/v1/agent/leads/{lead_id}"),
            ("GET", "/api/v1/agent/leads/by-external/{external_crm_id}"),
            ("POST", "/api/v1/agent/recovery/run"),
            ("GET", "/api/v1/agent/recovery/cases"),
            ("GET", "/api/v1/agent/recovery/cases/{case_id}"),
            ("GET", "/api/v1/agent/recovery/dashboard/summary"),
            ("GET", "/api/v1/agent/recovery/cases/{case_id}/score-breakdown"),
            ("GET", "/api/v1/agent/recovery/cases/{case_id}/outreach-content"),
            ("POST", "/api/v1/agent/recovery/cases/{case_id}/outreach-content/generate"),
            ("GET", "/api/v1/agent/recovery/cases/{case_id}/outcomes"),
        }
        assert required.issubset(ops), f"Missing: {required - ops}"


# ---------------------------------------------------------------------------
# Auto-sync with CRM connection
# ---------------------------------------------------------------------------


class TestAutoSyncWithCRM:
    def test_import_triggers_crm_sync(self, sync_client):
        """After import with CRM connection, leads are synced to CRM."""
        csv_bytes = _build_csv(
            ["Name", "Phone", "Source"],
            [["Rahul Sharma", "9876543210", "99acres"]],
        )
        mock_client = _mock_crm_client()
        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection",
            return_value=mock_client,
        ):
            resp = sync_client.post(
                "/api/v1/leads/import",
                files={"file": ("leads.csv", csv_bytes, "text/csv")},
                headers=auth_headers(BIZ_1),
            )
        assert resp.status_code == 201, resp.text
        # The external CRM was actually contacted.
        mock_client.create_contact.assert_awaited_once()
        # Verify lead was persisted with the real external CRM id.
        session = get_session_factory()()
        lead = session.query(Lead).first()
        assert lead is not None
        assert lead.external_id == "CRM-TEST-001"
        # Verify agent_leads was populated with the sync metadata.
        agent_lead = session.get(AgentLead, lead.id)
        assert agent_lead is not None
        assert agent_lead.external_crm_id == "CRM-TEST-001"
        # Verify a persisted sync record documents the result.
        from app.models import CRMSyncRecord

        record = session.query(CRMSyncRecord).one()
        assert record.lead_id == lead.id
        assert record.external_crm_id == "CRM-TEST-001"
        assert record.status == "created"
        session.close()

    def test_import_populates_agent_leads(self, sync_client):
        """Imported leads are immediately available in agent_leads."""
        csv_bytes = _build_csv(
            ["Name", "Phone", "Source"],
            [["Rahul Sharma", "9876543210", "99acres"]],
        )
        resp = sync_client.post(
            "/api/v1/leads/import",
            files={"file": ("leads.csv", csv_bytes, "text/csv")},
            headers=auth_headers(BIZ_1),
        )
        assert resp.status_code == 201, resp.text

        # Query agent_leads
        agent_resp = sync_client.get(
            "/api/v1/agent/leads",
            headers=auth_headers(BIZ_1),
        )
        assert agent_resp.status_code == 200
        data = agent_resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["name"] == "Rahul Sharma"

    def test_crm_sync_failure_does_not_affect_import(self, sync_client):
        """CRM sync failure does not affect the import response."""
        from app.modules.crm_sync.client import CRMUnavailableError

        mock_client = _mock_crm_client()
        mock_client.create_contact = AsyncMock(
            side_effect=CRMUnavailableError("connection refused")
        )

        csv_bytes = _build_csv(
            ["Name", "Phone", "Source"],
            [["Rahul Sharma", "9876543210", "99acres"]],
        )
        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection",
            return_value=mock_client,
        ):
            resp = sync_client.post(
                "/api/v1/leads/import",
                files={"file": ("leads.csv", csv_bytes, "text/csv")},
                headers=auth_headers(BIZ_1),
            )
        assert resp.status_code == 201, resp.text
        # Lead is still persisted and agent-visible; no CRM id fabricated.
        session = get_session_factory()()
        lead = session.query(Lead).first()
        assert lead is not None
        assert lead.external_id is None
        agent_lead = session.get(AgentLead, lead.id)
        assert agent_lead is not None
        assert agent_lead.external_crm_id is None
        # The failed sync attempt is recorded, not discarded.
        from app.models import CRMSyncRecord

        record = session.query(CRMSyncRecord).one()
        assert record.status == "failed"
        assert record.error_message
        session.close()


# ---------------------------------------------------------------------------
# Auto-sync without CRM connection
# ---------------------------------------------------------------------------


class TestAutoSyncNoCRM:
    def test_import_without_crm_connection_succeeds(self, no_crm_client):
        """Import succeeds when no CRM connection is configured."""
        csv_bytes = _build_csv(
            ["Name", "Phone", "Source"],
            [["Rahul Sharma", "9876543210", "99acres"]],
        )
        resp = no_crm_client.post(
            "/api/v1/leads/import",
            files={"file": ("leads.csv", csv_bytes, "text/csv")},
            headers=auth_headers(BIZ_1),
        )
        assert resp.status_code == 201, resp.text

        # Lead is persisted
        session = get_session_factory()()
        lead = session.query(Lead).first()
        assert lead is not None
        # agent_leads is populated
        agent_lead = session.get(AgentLead, lead.id)
        assert agent_lead is not None
        session.close()

    def test_import_without_crm_skips_sync(self, no_crm_client):
        """No CRM sync is attempted when no connection exists."""
        csv_bytes = _build_csv(
            ["Name", "Phone", "Source"],
            [["Rahul Sharma", "9876543210", "99acres"]],
        )
        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection"
        ) as mock_from_conn:
            resp = no_crm_client.post(
                "/api/v1/leads/import",
                files={"file": ("leads.csv", csv_bytes, "text/csv")},
                headers=auth_headers(BIZ_1),
            )
        assert resp.status_code == 201, resp.text
        mock_from_conn.assert_not_called()


# ---------------------------------------------------------------------------
# Partial failures
# ---------------------------------------------------------------------------


class TestPartialFailures:
    def test_some_leads_sync_others_fail(self, sync_client):
        """When some CRM syncs fail, others still succeed."""
        from app.modules.crm_sync.client import CRMUnavailableError

        csv_bytes = _build_csv(
            ["Name", "Phone", "Source"],
            [
                ["Rahul Sharma", "9876543210", "99acres"],
                ["Priya Patel", "9812345678", "website"],
            ],
        )

        # Key outcomes to the lead itself (not call order) so the test is
        # independent of row-processing order.
        def _create_by_lead(payload):
            if payload.get("phone") == "9876543210":
                return {"id": "CRM-001", "full_name": "Rahul"}
            raise CRMUnavailableError("connection refused")

        mock_client = _mock_crm_client()
        mock_client.create_contact = AsyncMock(side_effect=_create_by_lead)

        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection",
            return_value=mock_client,
        ):
            resp = sync_client.post(
                "/api/v1/leads/import",
                files={"file": ("leads.csv", csv_bytes, "text/csv")},
                headers=auth_headers(BIZ_1),
            )
        assert resp.status_code == 201, resp.text

        # Both leads are persisted and agent-visible.
        session = get_session_factory()()
        leads = {l.phone: l for l in session.query(Lead).all()}
        assert len(leads) == 2
        ok_lead = leads["9876543210"]
        fail_lead = leads["9812345678"]
        assert session.get(AgentLead, ok_lead.id) is not None
        assert session.get(AgentLead, fail_lead.id) is not None

        # Successful lead stored the real CRM id; failed one did not.
        assert ok_lead.external_id == "CRM-001"
        assert fail_lead.external_id is None

        from app.models import CRMSyncRecord

        records = session.query(CRMSyncRecord).all()
        assert len(records) == 2
        by_status = {r.status: r for r in records}
        assert by_status["created"].external_crm_id == "CRM-001"
        assert by_status["failed"].external_crm_id is None
        session.close()
