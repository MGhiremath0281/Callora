"""Resilience tests for Module 3 — Recovery Agent.

Covers the ticket's edge cases: API timeouts, network failures, 5xx,
malformed payloads, empty lists, leads that are individually invalid,
and LLM outages. The workflow must never break: failures surface as
documented error codes, and degradations fall back to rules.
"""

import httpx

from datetime import datetime, timedelta, timezone

from tests.conftest import auth_headers

from app.modules.recovery_agent import router as recovery_router
from app.modules.recovery_agent import strategist as strategist_module
from app.modules.recovery_agent.client import AgentLeadsClient
from app.modules.agent_storage.service import upsert_agent_lead


def patch_leads_api(monkeypatch, settings, handler):
    """Route the sweep's outbound agent-leads call through a mock."""

    def _build(_settings, api_key):
        return AgentLeadsClient(
            _settings, api_key, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(recovery_router, "build_client", _build)


def seed_lead(db, lead_id="lead_resilience0001"):
    upsert_agent_lead(
        db,
        lead_id=lead_id,
        business_id="biz_001",
        name="Resilient Ravi",
        phone="9876500001",
        source="website",
        language="english",
        deal_value=4_000_000,
        custom_fields={},
        created_at=datetime.now(timezone.utc) - timedelta(days=30),  # SLA long breached
    )
    db.commit()


class TestFetchFailures:
    def test_timeout_maps_to_504(self, client, monkeypatch, settings):
        def handler(request):
            raise httpx.ConnectTimeout("timed out", request=request)

        patch_leads_api(monkeypatch, settings, handler)
        response = client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        assert response.status_code == 504
        assert response.json()["error"]["code"] == "recovery_leads_timeout"

    def test_network_error_maps_to_503(self, client, monkeypatch, settings):
        def handler(request):
            raise httpx.ConnectError("connection refused", request=request)

        patch_leads_api(monkeypatch, settings, handler)
        response = client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "recovery_leads_unavailable"

    def test_upstream_5xx_maps_to_503(self, client, monkeypatch, settings):
        def handler(request):
            return httpx.Response(500, json={"error": {"code": "internal_error"}})

        patch_leads_api(monkeypatch, settings, handler)
        response = client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        assert response.status_code == 503
        assert response.json()["error"]["details"][0]["status_code"] == 500

    def test_malformed_json_maps_to_502(self, client, monkeypatch, settings):
        def handler(request):
            return httpx.Response(200, text="<html>gateway says no</html>")

        patch_leads_api(monkeypatch, settings, handler)
        response = client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "recovery_leads_invalid_response"

    def test_missing_items_key_maps_to_502(self, client, monkeypatch, settings):
        def handler(request):
            return httpx.Response(200, json={"leads": []})  # wrong envelope

        patch_leads_api(monkeypatch, settings, handler)
        response = client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        assert response.status_code == 502

    def test_non_object_items_maps_to_502(self, client, monkeypatch, settings):
        def handler(request):
            return httpx.Response(200, json={"items": ["not-a-lead"]})

        patch_leads_api(monkeypatch, settings, handler)
        response = client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        assert response.status_code == 502

    def test_errors_never_leave_partial_cases(self, client, db, monkeypatch, settings):
        seed_lead(db)

        def handler(request):
            raise httpx.ConnectError("down", request=request)

        patch_leads_api(monkeypatch, settings, handler)
        assert client.post("/api/v1/agent/recovery/run", headers=auth_headers()).status_code == 503
        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
        assert cases["items"] == []  # failed sweep persists nothing


class TestPartialAndDegradedData:
    def test_invalid_lead_entries_are_skipped_not_fatal(self, client, monkeypatch, settings, db):
        seed_lead(db)

        def handler(request):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {  # valid lead without the required identifier
                            "name": "No Id Nina",
                            "phone": "9876500002",
                        },
                        {  # valid neglected lead
                            "lead_id": "lead_valid000000001",
                            "business_id": "biz_001",
                            "name": "Valid Vicky",
                            "phone": "9876500003",
                            "source": "website",
                            "language": "english",
                            "deal_value": 5_000_000,
                            "last_contact_at": None,
                            "custom_fields": {},
                            "external_crm_id": None,
                            "status": "New",
                            "created_at": "2026-10-18T10:00:00+00:00",
                        },
                    ]
                },
            )

        patch_leads_api(monkeypatch, settings, handler)
        response = client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        assert response.status_code == 200
        body = response.json()
        assert body["fetched"] == 2
        assert body["skipped_invalid"] == 1
        assert body["cases_upserted"] == 1

    def test_notebook_alias_schema_is_accepted(self, client, monkeypatch, settings):
        """Leads using the prototype's field names (id/external_id) still work."""

        def handler(request):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "lead_alias000000001",
                            "business_id": "biz_001",
                            "name": "Alias Asha",
                            "phone": "9876500004",
                            "source": "website",
                            "language": "kannada",
                            "deal_value": "8000000.00",  # prototype used strings
                            "last_contact_at": None,
                            "custom_fields": {},
                            "external_id": None,
                            "created_at": "2026-10-18T10:00:00+00:00",
                        }
                    ]
                },
            )

        patch_leads_api(monkeypatch, settings, handler)
        response = client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        assert response.status_code == 200
        assert response.json()["cases_upserted"] == 1


class TestLLMDegradation:
    def test_sweep_uses_rules_without_key(self, client, monkeypatch, settings, db):
        seed_lead(db)
        monkeypatch.setattr(strategist_module, "Groq", None)  # package unavailable
        response = client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        assert response.status_code == 200
        assert response.json()["engine"] == "rules-fallback"

    def test_key_configured_but_client_missing_still_rules(self, settings, monkeypatch):
        settings.groq_api_key = "gsk_test"
        monkeypatch.setattr(strategist_module, "Groq", None)
        assert strategist_module.llm_available(settings) is False

    def test_llm_error_falls_back_to_rules_plan(self, settings, monkeypatch):
        candidate = {
            "lead": {
                "lead_id": "lead_x",
                "name": "X",
                "phone": "1",
                "language": "english",
                "deal_value": 1,
                "custom_fields": {},
            },
            "signals": [{"signal": "never_contacted", "severity": "high", "detail": ""}],
            "score": 0.9,
        }
        monkeypatch.setattr(strategist_module, "llm_available", lambda s: True)
        # groq_chat returns None when the API errors out (it swallows
        # exceptions internally); the strategist must fall back cleanly.
        monkeypatch.setattr(strategist_module, "groq_chat", lambda s, sys, usr: None)

        from app.modules.recovery_agent.detect import RecoveryPolicy

        plan = strategist_module.strategize(candidate, 0, None, settings, RecoveryPolicy())
        assert plan["engine"] == "rules-fallback"
        assert plan["action"] in strategist_module.ALLOWED_ACTIONS
