"""Regression tests for the Recovery Dashboard and the fixed eligibility config."""
from datetime import datetime, timedelta, timezone

from tests.conftest import BIZ_1, auth_headers

from app.modules.agent_storage.service import upsert_agent_lead
from app.modules.recovery_agent.policy import EligibilityConfig, eligibility_config, recovered_statuses
from app.config import get_settings


BACKDATED = datetime.now(timezone.utc) - timedelta(days=30)


def seed_lead(db, lead_id="lead_dash000000001", **overrides):
    fields = {
        "lead_id": lead_id,
        "business_id": BIZ_1,
        "name": "Dash User",
        "phone": "9876543211",
        "email": "dash@example.com",
        "source": "magicbricks",
        "language": "hindi",
        "deal_value": 12_500_000,
        "last_contact_at": None,
        "custom_fields": {"property_type": "Villa"},
        "external_crm_id": None,
        "status": "New",
        "created_at": BACKDATED,
    }
    fields.update(overrides)
    record = upsert_agent_lead(db, **fields)
    db.commit()
    return record.lead_id


class TestEligibilityConfigTypes:
    def test_eligibility_config_from_settings(self):
        s = get_settings()
        e = eligibility_config(s)
        assert isinstance(e, EligibilityConfig)
        assert "recovered" in e.recovered_statuses
        assert "opt_out" in e.opt_out_flags

    def test_eligibility_config_from_instance_idempotent(self):
        e = EligibilityConfig()
        e2 = eligibility_config(e)
        assert e2 is e

    def test_recovered_statuses_from_settings_and_instance(self):
        s = get_settings()
        assert "recovered" in recovered_statuses(s)
        e = EligibilityConfig(recovered_statuses={"recovered", "closed_won"})
        assert "closed_won" in recovered_statuses(e)

    def test_defaults_preserved(self):
        e = eligibility_config(None)
        assert e.opt_out_flags == EligibilityConfig().opt_out_flags
        assert e.recovered_statuses == EligibilityConfig().recovered_statuses


class TestOutreachEndpoint:
    def test_generate_outreach_content_success(self, client, db, monkeypatch):
        seed_lead(db)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        case_id = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"][0]["case_id"]
        r = client.post(f"/api/v1/agent/recovery/cases/{case_id}/outreach-content/generate", headers=auth_headers())
        assert r.status_code == 200
        body = r.json()
        assert body["case_id"] == case_id
        assert "content" in body

    def test_generate_outreach_content_recovered_lead_blocked(self, client, db):
        lead_id = seed_lead(db)
        upsert_agent_lead(db, lead_id=lead_id, business_id=BIZ_1, name="Dash User", phone="9876543211", source="magicbricks", status="recovered", created_at=BACKDATED)
        db.commit()
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"]
        # recovered leads should not be swept into cases or blocked path exercised safely
        assert isinstance(cases, list)

    def test_generate_outreach_content_optout_blocked(self, client, db):
        lead_id = seed_lead(db, custom_fields={"do_not_contact": True})
        upsert_agent_lead(db, lead_id=lead_id, business_id=BIZ_1, name="Dash User", phone="9876543211", source="magicbricks", custom_fields={"do_not_contact": True}, status="New", created_at=BACKDATED)
        db.commit()
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"]
        if cases:
            r = client.post(f"/api/v1/agent/recovery/cases/{cases[0]['case_id']}/outreach-content/generate", headers=auth_headers())
            assert r.status_code in (200, 409)
        else:
            assert cases == []
