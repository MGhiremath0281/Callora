"""End-to-end API tests for Module 3 — Recovery Agent.

The sweep fetches leads through GET /api/v1/agent/leads using the
caller's X-API-Key (in-process ASGI transport — the real route, auth
and serialization paths), then persists recovery cases.
"""

from datetime import datetime, timedelta, timezone

from tests.conftest import BIZ_1, BIZ_2, auth_headers

from app.modules.agent_storage.service import upsert_agent_lead

# All seeds are relative to "now" so the SLA/stall criteria hold
# whenever the suite runs.
BACKDATED = datetime.now(timezone.utc) - timedelta(days=30)


def seed_lead(db, lead_id="lead_aaa111bbb222ccc", **overrides):
    fields = {
        "lead_id": lead_id,
        "business_id": BIZ_1,
        "name": "Priya Patil",
        "phone": "9876543211",
        "email": "priya.test@example.com",
        "source": "magicbricks",
        "language": "hindi",
        "deal_value": 12_500_000,
        "last_contact_at": None,
        "custom_fields": {"property_type": "Villa", "city": "Pune"},
        "external_crm_id": None,
        "status": "New",
        # Backdated so the never-contacted SLA has indisputably elapsed.
        "created_at": BACKDATED,
    }
    fields.update(overrides)
    record = upsert_agent_lead(db, **fields)
    db.commit()
    return record.lead_id


class TestAuth:
    def test_run_requires_api_key(self, client):
        assert client.post("/api/v1/agent/recovery/run").status_code == 401

    def test_cases_require_api_key(self, client):
        assert client.get("/api/v1/agent/recovery/cases").status_code == 401

    def test_wrong_key_rejected(self, client):
        response = client.post(
            "/api/v1/agent/recovery/run", headers={"X-API-Key": "nope"}
        )
        assert response.status_code == 401


class TestRunSweep:
    def test_empty_lead_list_is_graceful(self, client):
        response = client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        assert response.status_code == 200
        body = response.json()
        assert body["fetched"] == 0
        assert body["candidates"] == 0
        assert body["cases_upserted"] == 0
        assert body["engine"] == "rules-fallback"  # no LLM key in tests

    def test_neglected_leads_flagged_active_excluded(self, client, db):
        neglected = seed_lead(db)  # never contacted, high value
        stalled = seed_lead(
            db,
            lead_id="lead_stalled0000001",
            name="Meera Iyer",
            last_contact_at=datetime.now(timezone.utc) - timedelta(days=30),
        )
        seed_lead(
            db,
            lead_id="lead_converted00001",
            name="Converted Charlie",
            status="Converted",  # AC: converted leads excluded
        )
        seed_lead(
            db,
            lead_id="lead_contacted0001",
            name="Active Alice",
            status="Contacted",  # AC: active leads excluded
        )

        response = client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        assert response.status_code == 200
        body = response.json()
        assert body["fetched"] == 4
        assert body["evaluated"] == 2
        assert body["skipped_inactive"] == 2
        assert body["candidates"] == 2
        assert body["cases_upserted"] == 2
        assert body["signals_detected"] >= 2

        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
        assert len(cases["items"]) == 2
        lead_ids = {c["lead_id"] for c in cases["items"]}
        assert lead_ids == {neglected, stalled}
        for case in cases["items"]:
            assert case["status"] == "recommended"
            assert case["score"] >= 0.4
            assert case["signals"]
            assert case["action"]
            assert case["engine"] == "rules-fallback"
        # Highest score first.
        scores = [c["score"] for c in cases["items"]]
        assert scores == sorted(scores, reverse=True)

    def test_sweep_is_idempotent(self, client, db):
        seed_lead(db)
        first = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        second = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        assert first["cases_upserted"] == second["cases_upserted"] == 1

        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
        assert len(cases["items"]) == 1  # no duplicates on re-run
        # A re-run must not escalate to human review merely because the
        # sweep ran twice (no outreach has actually happened).
        assert cases["items"][0]["route"] == "llm"

    def test_healthy_lead_produces_no_case(self, client, db):
        seed_lead(
            db,
            lead_id="lead_healthy0000001",
            last_contact_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        response = client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        assert response.status_code == 200
        assert response.json()["cases_upserted"] == 0

    def test_pagination_fetches_every_lead(self, client, db, monkeypatch):
        from app.config import get_settings

        monkeypatch.setenv("CALLORA_RECOVERY_FETCH_PAGE_SIZE", "2")
        get_settings.cache_clear()
        for i in range(5):
            seed_lead(db, lead_id=f"lead_page{i:012d}", name=f"Lead {i}")

        response = client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        assert response.status_code == 200
        assert response.json()["fetched"] == 5
        assert response.json()["cases_upserted"] == 5
        get_settings.cache_clear()


class TestCaseRetrieval:
    def test_get_single_case(self, client, db):
        seed_lead(db)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        case_id = client.get(
            "/api/v1/agent/recovery/cases", headers=auth_headers()
        ).json()["items"][0]["case_id"]

        response = client.get(
            f"/api/v1/agent/recovery/cases/{case_id}", headers=auth_headers()
        )
        assert response.status_code == 200
        assert response.json()["case_id"] == case_id
        assert response.json()["lead_name"] == "Priya Patil"

    def test_unknown_case_404(self, client):
        response = client.get(
            "/api/v1/agent/recovery/cases/case_doesnotexist01", headers=auth_headers()
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "lead_not_found"

    def test_status_filter(self, client, db):
        seed_lead(db)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        filtered = client.get(
            "/api/v1/agent/recovery/cases?status=recommended", headers=auth_headers()
        ).json()
        assert len(filtered["items"]) == 1
        empty = client.get(
            "/api/v1/agent/recovery/cases?status=exhausted", headers=auth_headers()
        ).json()
        assert empty["items"] == []


class TestTenantIsolation:
    def test_other_business_sees_no_cases(self, client, db):
        seed_lead(db)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())

        other = auth_headers(BIZ_2)
        cases = client.get("/api/v1/agent/recovery/cases", headers=other).json()
        assert cases["items"] == []
        assert cases["business_id"] == BIZ_2

    def test_other_business_cannot_read_case(self, client, db):
        seed_lead(db)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        case_id = client.get(
            "/api/v1/agent/recovery/cases", headers=auth_headers()
        ).json()["items"][0]["case_id"]

        response = client.get(
            f"/api/v1/agent/recovery/cases/{case_id}", headers=auth_headers(BIZ_2)
        )
        assert response.status_code == 404

    def test_sweep_scopes_to_caller_business(self, client, db):
        seed_lead(db)  # biz_001 lead
        response = client.post(
            "/api/v1/agent/recovery/run", headers=auth_headers(BIZ_2)
        )
        assert response.status_code == 200
        assert response.json()["fetched"] == 0  # biz_002 has no leads
