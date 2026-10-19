"""Tests for downstream agent storage and repository functions."""

import pytest
from fastapi.testclient import TestClient

from app.db.base import Base
from app.db.session import get_engine, get_session_factory, reset_engine
from app.main import app
from app.models import AgentLead, CRMConnection, Lead
from app.modules.agent_storage.repository import (
    get_agent_lead,
    get_agent_lead_by_external_id,
    list_agent_leads,
)
from app.modules.agent_storage.service import upsert_agent_lead

BIZ_1 = "biz_001"
BIZ_2 = "biz_002"
KEY_1 = "test-key-1"
KEY_2 = "test-key-2"


def auth_headers(business: str = BIZ_1) -> dict:
    return {"X-API-Key": {BIZ_1: KEY_1, BIZ_2: KEY_2}[business]}


def _setup_db():
    Base.metadata.create_all(get_engine())
    session = get_session_factory()()
    try:
        # Create leads for BIZ_1.
        session.add(Lead(
            id="lead_agent00000000001",
            business_id=BIZ_1,
            name="Rahul Sharma",
            phone="9876543210",
            source="99acres",
        ))
        session.add(Lead(
            id="lead_agent00000000002",
            business_id=BIZ_1,
            name="Priya Patel",
            phone="9812345678",
            source="website",
        ))
        # Create a lead for BIZ_2.
        session.add(Lead(
            id="lead_agent00000000003",
            business_id=BIZ_2,
            name="Other Biz Lead",
            phone="9800000000",
            source="import",
        ))
        session.commit()
    finally:
        session.close()


@pytest.fixture()
def agent_client(settings):
    _setup_db()
    from app.db.session import get_db

    def _override_get_db():
        session = get_session_factory()()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()
    reset_engine()
    Base.metadata.drop_all(get_engine())


# ---------------------------------------------------------------------------
# Repository tests
# ---------------------------------------------------------------------------


class TestAgentRepository:
    def test_get_agent_lead(self, agent_client, db):
        upsert_agent_lead(
            db,
            lead_id="lead_agent00000000001",
            business_id=BIZ_1,
            name="Rahul Sharma",
            phone="9876543210",
            source="99acres",
        )
        db.commit()

        result = get_agent_lead(db, "lead_agent00000000001")
        assert result is not None
        assert result.name == "Rahul Sharma"

    def test_get_agent_lead_not_found(self, agent_client, db):
        result = get_agent_lead(db, "nonexistent")
        assert result is None

    def test_list_agent_leads(self, agent_client, db):
        upsert_agent_lead(
            db, "lead_agent00000000001", BIZ_1, "Rahul Sharma",
            "9876543210", "99acres",
        )
        upsert_agent_lead(
            db, "lead_agent00000000002", BIZ_1, "Priya Patel",
            "9812345678", "website",
        )
        upsert_agent_lead(
            db, "lead_agent00000000003", BIZ_2, "Other Biz",
            "9800000000", "import",
        )
        db.commit()

        results = list_agent_leads(db, BIZ_1)
        assert len(results) == 2

    def test_list_agent_leads_with_status_filter(self, agent_client, db):
        upsert_agent_lead(
            db, "lead_agent00000000001", BIZ_1, "Rahul Sharma",
            "9876543210", "99acres", status="New",
        )
        upsert_agent_lead(
            db, "lead_agent00000000002", BIZ_1, "Priya Patel",
            "9812345678", "website", status="Contacted",
        )
        db.commit()

        results = list_agent_leads(db, BIZ_1, status="New")
        assert len(results) == 1
        assert results[0].name == "Rahul Sharma"

    def test_get_agent_lead_by_external_id(self, agent_client, db):
        upsert_agent_lead(
            db, "lead_agent00000000001", BIZ_1, "Rahul Sharma",
            "9876543210", "99acres", external_crm_id="CRM-001",
        )
        db.commit()

        result = get_agent_lead_by_external_id(db, BIZ_1, "CRM-001")
        assert result is not None
        assert result.name == "Rahul Sharma"

    def test_get_agent_lead_by_external_id_isolated(self, agent_client, db):
        """BIZ_2 cannot retrieve BIZ_1's lead by external ID."""
        upsert_agent_lead(
            db, "lead_agent00000000001", BIZ_1, "Rahul Sharma",
            "9876543210", "99acres", external_crm_id="CRM-001",
        )
        db.commit()

        result = get_agent_lead_by_external_id(db, BIZ_2, "CRM-001")
        assert result is None


# ---------------------------------------------------------------------------
# Upsert behavior
# ---------------------------------------------------------------------------


class TestAgentUpsert:
    def test_upsert_inserts_new(self, agent_client, db):
        upsert_agent_lead(
            db, "lead_agent00000000001", BIZ_1, "Rahul Sharma",
            "9876543210", "99acres",
        )
        db.commit()

        record = db.get(AgentLead, "lead_agent00000000001")
        assert record is not None
        assert record.status == "New"

    def test_upsert_updates_existing(self, agent_client, db):
        upsert_agent_lead(
            db, "lead_agent00000000001", BIZ_1, "Rahul Sharma",
            "9876543210", "99acres", external_crm_id="CRM-001",
        )
        db.commit()

        upsert_agent_lead(
            db, "lead_agent00000000001", BIZ_1, "Rahul Sharma Updated",
            "9876543210", "99acres", external_crm_id="CRM-002",
            status="Contacted",
        )
        db.commit()

        record = db.get(AgentLead, "lead_agent00000000001")
        assert record.name == "Rahul Sharma Updated"
        assert record.external_crm_id == "CRM-002"
        assert record.status == "Contacted"

    def test_no_duplicate_records(self, agent_client, db):
        """Repeated upserts do not create duplicates."""
        for _ in range(3):
            upsert_agent_lead(
                db, "lead_agent00000000001", BIZ_1, "Rahul Sharma",
                "9876543210", "99acres",
            )
            db.commit()

        count = db.query(AgentLead).filter_by(lead_id="lead_agent00000000001").count()
        assert count == 1


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestAgentAPI:
    def test_get_agent_lead_by_id(self, agent_client):
        session = get_session_factory()()
        upsert_agent_lead(
            session, "lead_agent00000000001", BIZ_1, "Rahul Sharma",
            "9876543210", "99acres",
        )
        session.commit()
        session.close()

        resp = agent_client.get(
            "/api/v1/agent/leads/lead_agent00000000001",
            headers=auth_headers(BIZ_1),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["lead_id"] == "lead_agent00000000001"
        assert data["name"] == "Rahul Sharma"

    def test_get_agent_lead_isolation(self, agent_client):
        """BIZ_2 cannot retrieve BIZ_1's lead."""
        session = get_session_factory()()
        upsert_agent_lead(
            session, "lead_agent00000000001", BIZ_1, "Rahul Sharma",
            "9876543210", "99acres",
        )
        session.commit()
        session.close()

        resp = agent_client.get(
            "/api/v1/agent/leads/lead_agent00000000001",
            headers=auth_headers(BIZ_2),
        )
        assert resp.status_code == 404

    def test_list_agent_leads(self, agent_client):
        session = get_session_factory()()
        upsert_agent_lead(
            session, "lead_agent00000000001", BIZ_1, "Rahul Sharma",
            "9876543210", "99acres",
        )
        upsert_agent_lead(
            session, "lead_agent00000000002", BIZ_1, "Priya Patel",
            "9812345678", "website",
        )
        session.commit()
        session.close()

        resp = agent_client.get(
            "/api/v1/agent/leads",
            headers=auth_headers(BIZ_1),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2

    def test_list_agent_leads_with_status(self, agent_client):
        session = get_session_factory()()
        upsert_agent_lead(
            session, "lead_agent00000000001", BIZ_1, "Rahul Sharma",
            "9876543210", "99acres", status="New",
        )
        upsert_agent_lead(
            session, "lead_agent00000000002", BIZ_1, "Priya Patel",
            "9812345678", "website", status="Contacted",
        )
        session.commit()
        session.close()

        resp = agent_client.get(
            "/api/v1/agent/leads?status=New",
            headers=auth_headers(BIZ_1),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1

    def test_get_by_external_id(self, agent_client):
        session = get_session_factory()()
        upsert_agent_lead(
            session, "lead_agent00000000001", BIZ_1, "Rahul Sharma",
            "9876543210", "99acres", external_crm_id="CRM-001",
        )
        session.commit()
        session.close()

        resp = agent_client.get(
            "/api/v1/agent/leads/by-external/CRM-001",
            headers=auth_headers(BIZ_1),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["lead_id"] == "lead_agent00000000001"

    def test_get_by_external_id_isolation(self, agent_client):
        session = get_session_factory()()
        upsert_agent_lead(
            session, "lead_agent00000000001", BIZ_1, "Rahul Sharma",
            "9876543210", "99acres", external_crm_id="CRM-001",
        )
        session.commit()
        session.close()

        resp = agent_client.get(
            "/api/v1/agent/leads/by-external/CRM-001",
            headers=auth_headers(BIZ_2),
        )
        assert resp.status_code == 404
