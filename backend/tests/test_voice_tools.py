"""Tests for the Bolna Agent Studio custom functions (Module 6).

Covers the Phase-7 scenarios:

* authentication (no token, wrong token, tool secret without business_id,
  cross-business mismatch, disabled integration);
* lead details (happy path, unknown lead, execution-id business check);
* call outcome (persisted status + audit, idempotent replay, conflicting
  replay, qualified-without-answers fallback, no downgrade of a higher
  status, unknown status never overwritten);
* qualification (qualified, insufficient_data);
* opportunities (not qualified refused, created, duplicate replay, force
  only with a business API key);
* callbacks (scheduled, compliance-blocked, past time, bad timezone,
  replay);
* CRM sync (not configured, success with a mocked client, provider
  failure reported honestly);
* the Bolna Agent Studio definition endpoint (six functions, no secret).
"""

import pytest
from unittest.mock import AsyncMock, patch

from app.config import get_settings
from app.db.base import Base
from app.db.session import get_engine, get_session_factory
from app.models import CRMConnection, Lead
from app.modules.crm_sync.client import CRMAuthError, CRMUnavailableError
from app.modules.voice import repository as voice_repo
from app.modules.voice.constants import AuditAction
from app.modules.voice.models import AuditLog, Call
from tests.conftest import BIZ_1, BIZ_2, KEY_1, VOICE_LEAD_ID, VOICE_PHONE

TOOL_SECRET = "voice-tool-secret"

TOOL_HEADERS = {"Authorization": f"Bearer {TOOL_SECRET}"}
API_KEY_HEADERS = {"Authorization": f"Bearer {KEY_1}"}


@pytest.fixture()
def tool_env(voice_settings, monkeypatch):
    """The voice settings plus the shared tool secret."""
    monkeypatch.setenv("CALLORA_VOICE_TOOL_SECRET", TOOL_SECRET)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture()
def tool_client(tool_env, client):
    return client


def _future_iso():
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class TestToolAuthentication:
    def test_no_token_rejected(self, tool_client):
        resp = tool_client.post(
            "/api/v1/voice/tools/lead-details",
            json={"lead_id": VOICE_LEAD_ID, "business_id": BIZ_1},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "AUTHENTICATION_FAILED"

    def test_wrong_token_rejected(self, tool_client):
        resp = tool_client.post(
            "/api/v1/voice/tools/lead-details",
            json={"lead_id": VOICE_LEAD_ID, "business_id": BIZ_1},
            headers={"Authorization": "Bearer not-the-secret"},
        )
        assert resp.status_code == 401

    def test_tool_secret_requires_business_id(self, tool_client):
        resp = tool_client.post(
            "/api/v1/voice/tools/lead-details",
            json={"lead_id": VOICE_LEAD_ID},
            headers=TOOL_HEADERS,
        )
        assert resp.status_code == 401

    def test_cross_business_body_mismatch_rejected(self, tool_client):
        # Authenticated as BIZ_1 (tool secret claims BIZ_1) but scoped to BIZ_2.
        resp = tool_client.post(
            "/api/v1/voice/tools/lead-details",
            json={"lead_id": VOICE_LEAD_ID, "business_id": BIZ_2},
            headers=API_KEY_HEADERS,
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "CROSS_BUSINESS_ACCESS_DENIED"

    def test_api_key_accepted_as_business(self, tool_client, voice_business, voice_lead):
        resp = tool_client.post(
            "/api/v1/voice/tools/lead-details",
            json={"lead_id": VOICE_LEAD_ID},
            headers=API_KEY_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_disabled_integration_rejects_everything(self, voice_client, voice_business, voice_lead):
        # No tool secret configured (voice_settings does not set one).
        resp = voice_client.post(
            "/api/v1/voice/tools/lead-details",
            json={"lead_id": VOICE_LEAD_ID, "business_id": BIZ_1},
            headers=TOOL_HEADERS,
        )
        assert resp.status_code == 401

    def test_execution_id_of_other_business_rejected(
        self, tool_client, voice_business, voice_lead, db
    ):
        other_lead = Lead(
            id="lead_voice_0002",
            business_id=BIZ_2,
            name="Other Tenant",
            phone="+919999999999",
            source="test",
        )
        db.add(other_lead)
        db.add(
            Call(
                business_id=BIZ_2,
                lead_id=other_lead.id,
                provider="bolna",
                provider_call_id="exec-other-biz",
                status="completed",
            )
        )
        db.commit()

        resp = tool_client.post(
            "/api/v1/voice/tools/lead-details",
            json={
                "lead_id": VOICE_LEAD_ID,
                "business_id": BIZ_1,
                "execution_id": "exec-other-biz",
            },
            headers=TOOL_HEADERS,
        )
        assert resp.status_code == 403

    def test_validation_error_uses_tool_envelope(self, tool_client, voice_business):
        resp = tool_client.post(
            "/api/v1/voice/tools/save-outcome",
            json={"lead_id": VOICE_LEAD_ID, "business_id": BIZ_1, "outcome": "bogus"},
            headers=TOOL_HEADERS,
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "REQUEST_VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# Lead details
# ---------------------------------------------------------------------------


class TestLeadDetails:
    def test_returns_verified_details(self, tool_client, voice_business, voice_lead, voice_consent, db):
        resp = tool_client.post(
            "/api/v1/voice/tools/lead-details",
            json={"lead_id": VOICE_LEAD_ID, "business_id": BIZ_1},
            headers=TOOL_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["lead_id"] == VOICE_LEAD_ID
        assert data["name"] == "Rahul Sharma"
        assert data["status"] == "new"
        assert data["known_fields"]["budget"] == "5000000"
        assert {q["key"] for q in data["qualification_questions"]} == {"budget", "timeline"}

        audits = (
            db.query(AuditLog)
            .filter_by(action=AuditAction.TOOL_LEAD_READ)
            .all()
        )
        assert audits, "the lead read must be audited"

    def test_unknown_lead_is_404(self, tool_client, voice_business, voice_lead):
        resp = tool_client.post(
            "/api/v1/voice/tools/lead-details",
            json={"lead_id": "lead_missing", "business_id": BIZ_1},
            headers=TOOL_HEADERS,
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "LEAD_NOT_FOUND"


# ---------------------------------------------------------------------------
# Call outcome
# ---------------------------------------------------------------------------


class TestSaveOutcome:
    def _post(self, tool_client, payload_overrides=None, headers=TOOL_HEADERS):
        payload = {
            "lead_id": VOICE_LEAD_ID,
            "business_id": BIZ_1,
            "outcome": "interested",
            "summary": "Customer asked about 3 BHK pricing.",
        }
        payload.update(payload_overrides or {})
        return tool_client.post(
            "/api/v1/voice/tools/save-outcome", json=payload, headers=headers
        )

    def test_outcome_saved_and_status_applied(
        self, tool_client, tool_env, voice_business, voice_lead, voice_consent, db
    ):
        resp = self._post(tool_client)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["lead_status"] == "interested"
        assert body["data"]["lead_status_applied"] is True

        db.expire_all()
        lead = db.get(Lead, VOICE_LEAD_ID)
        assert lead.custom_fields["voice_last_outcome"] == "interested"

        audits = (
            db.query(AuditLog)
            .filter_by(action=AuditAction.TOOL_OUTCOME_SAVED)
            .all()
        )
        assert audits

    def test_replay_same_payload_is_idempotent(
        self, tool_client, tool_env, voice_business, voice_lead, voice_consent
    ):
        first = self._post(tool_client)
        assert first.status_code == 200
        second = self._post(tool_client)
        assert second.status_code == 200
        body = second.json()
        assert body["success"] is True
        assert body["message"] == "the call outcome was already recorded"
        assert body["data"]["replayed"] is True

    def test_conflicting_replay_is_rejected(
        self, tool_client, tool_env, voice_business, voice_lead, voice_consent
    ):
        first = self._post(tool_client)
        assert first.status_code == 200
        conflict = self._post(
            tool_client,
            payload_overrides={"summary": "a different summary"},
        )
        # Same fallback key (no execution_id/call_id) with a different payload.
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    def test_qualified_without_answers_falls_back_to_interested(
        self, tool_client, tool_env, voice_business, voice_lead, voice_consent
    ):
        resp = self._post(tool_client, {"outcome": "qualified"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["lead_status"] == "interested"
        assert "qualification" not in data

    def test_status_never_downgraded(
        self, tool_client, tool_env, voice_business, voice_lead, voice_consent, db
    ):
        qualified = self._post(tool_client, {"outcome": "qualified", "qualification_answers": {"budget": "7500000", "timeline": "next month"}})
        assert qualified.status_code == 200
        assert qualified.json()["data"]["lead_status"] == "qualified"

        downgrade = self._post(tool_client, {"outcome": "no_answer"})
        assert downgrade.status_code == 200
        data = downgrade.json()["data"]
        assert data["lead_status_applied"] is False
        assert data["lead_status"] == "qualified"

        preserved = (
            db.query(AuditLog)
            .filter_by(action=AuditAction.TOOL_STATUS_PRESERVED)
            .all()
        )
        assert preserved

    def test_unknown_status_never_overwritten(
        self, tool_client, tool_env, voice_business, voice_lead, voice_consent, db, settings
    ):
        voice_repo.apply_lead_status(db, db.get(Lead, VOICE_LEAD_ID), "vip_customer")
        db.commit()

        resp = self._post(tool_client, {"outcome": "interested"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["lead_status_applied"] is False
        assert data["lead_status"] == "vip_customer"


# ---------------------------------------------------------------------------
# Qualification
# ---------------------------------------------------------------------------


class TestQualifyLead:
    def test_qualified_when_rules_pass(
        self, tool_client, tool_env, voice_business, voice_lead, voice_consent
    ):
        resp = tool_client.post(
            "/api/v1/voice/tools/qualify-lead",
            json={
                "lead_id": VOICE_LEAD_ID,
                "business_id": BIZ_1,
                "answers": {"budget": "7500000", "timeline": "next month"},
            },
            headers=TOOL_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["qualification"]["status"] == "qualified"
        assert body["data"]["lead_status"] == "qualified"

    def test_insufficient_data_when_required_missing(
        self, tool_client, tool_env, voice_business, voice_lead, db
    ):
        voice_business.qualification_questions = [
            {"key": "budget", "question": "Budget?", "type": "number", "required": True},
            {"key": "timeline", "question": "When?", "type": "text", "required": True},
        ]
        db.commit()

        resp = tool_client.post(
            "/api/v1/voice/tools/qualify-lead",
            json={
                "lead_id": VOICE_LEAD_ID,
                "business_id": BIZ_1,
                "answers": {"budget": "7500000"},
            },
            headers=TOOL_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        qualification = body["data"]["qualification"]
        assert qualification["status"] == "insufficient_data"
        assert qualification["unknown_keys"] == ["timeline"]
        assert "lead_status" not in body["data"]

    def test_rule_failure_is_not_qualified(
        self, tool_client, tool_env, voice_business, voice_lead, db
    ):
        business = voice_business
        business.qualification_questions = [
            {
                "key": "budget",
                "question": "Budget?",
                "type": "number",
                "required": True,
                "rules": {"min_value": 5000000},
            },
            {"key": "timeline", "question": "When?", "type": "text", "required": True},
        ]
        db.commit()

        resp = tool_client.post(
            "/api/v1/voice/tools/qualify-lead",
            json={
                "lead_id": VOICE_LEAD_ID,
                "business_id": BIZ_1,
                "answers": {"budget": "1000000", "timeline": "next month"},
            },
            headers=TOOL_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["qualification"]["status"] == "not_qualified"


# ---------------------------------------------------------------------------
# Opportunities
# ---------------------------------------------------------------------------


class TestCreateOpportunity:
    def _post(self, tool_client, overrides=None, headers=TOOL_HEADERS):
        payload = {"lead_id": VOICE_LEAD_ID, "business_id": BIZ_1}
        payload.update(overrides or {})
        return tool_client.post(
            "/api/v1/voice/tools/create-opportunity", json=payload, headers=headers
        )

    def test_refused_when_not_qualified(
        self, tool_client, tool_env, voice_business, voice_lead, db
    ):
        voice_business.qualification_questions = [
            {
                "key": "budget",
                "question": "Budget?",
                "type": "number",
                "required": True,
                "rules": {"min_value": 5000000},
            },
            {"key": "timeline", "question": "When?", "type": "text", "required": True},
        ]
        db.commit()

        resp = self._post(tool_client, {"answers": {"budget": "1000", "timeline": "later"}})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "QUALIFICATION_NOT_MET"

    def test_created_when_qualified(
        self, tool_client, tool_env, voice_business, voice_lead, voice_consent, db
    ):
        resp = self._post(
            tool_client,
            {"answers": {"budget": "7500000", "timeline": "next month"}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["created"] is True
        assert body["data"]["opportunity_id"]
        assert body["data"]["lead_status"] == "opportunity_created"

    def test_duplicate_event_replays(
        self, tool_client, tool_env, voice_business, voice_lead
    ):
        payload = {
            "lead_id": VOICE_LEAD_ID,
            "business_id": BIZ_1,
            "answers": {"budget": "7500000", "timeline": "next month"},
        }
        first = self._post(tool_client, payload)
        assert first.json()["data"]["created"] is True
        second = self._post(tool_client, payload)
        assert second.json()["data"]["created"] is False

    def test_force_requires_api_key(
        self, tool_client, tool_env, voice_business, voice_lead
    ):
        resp = self._post(tool_client, {"force": True})
        assert resp.status_code == 403

    def test_force_with_api_key_creates(
        self, tool_client, tool_env, voice_business, voice_lead
    ):
        resp = self._post(
            tool_client,
            {"force": True, "answers": {"budget": "1", "timeline": "later"}},
            headers=API_KEY_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["created"] is True


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


class TestScheduleCallback:
    def _post(self, tool_client, overrides=None):
        payload = {
            "lead_id": VOICE_LEAD_ID,
            "business_id": BIZ_1,
            "requested_at": _future_iso(),
            "timezone": "Asia/Kolkata",
            "reason": "customer is at work",
        }
        payload.update(overrides or {})
        return tool_client.post(
            "/api/v1/voice/tools/schedule-callback", json=payload, headers=TOOL_HEADERS
        )

    def test_callback_scheduled(
        self, tool_client, tool_env, voice_business, voice_lead, voice_consent, db
    ):
        resp = self._post(tool_client)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["scheduled"] is True
        assert data["requested_at"].endswith("+00:00")
        assert data["dispatch"]["mode"] == "pending_manual"

        db.expire_all()
        handoff_audits = (
            db.query(AuditLog)
            .filter_by(action=AuditAction.TOOL_CALLBACK)
            .all()
        )
        assert handoff_audits

    def test_replay_returns_original_callback(
        self, tool_client, tool_env, voice_business, voice_lead, voice_consent, db
    ):
        requested = _future_iso()
        first = self._post(tool_client, {"requested_at": requested})
        callback_id = first.json()["data"]["callback_id"]
        second = self._post(tool_client, {"requested_at": requested})
        assert second.status_code == 200
        data = second.json()["data"]
        assert data["replayed"] is True
        assert data["callback_id"] == callback_id

    def test_blocked_without_consent(
        self, tool_client, tool_env, voice_business, voice_lead
    ):
        resp = self._post(tool_client)
        assert resp.status_code == 403
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"].startswith("CONSENT")

    def test_save_outcome_keeps_outcome_when_callback_blocked(
        self, tool_client, tool_env, voice_business, voice_lead
    ):
        # No consent record: the callback is refused, the outcome stands.
        resp = tool_client.post(
            "/api/v1/voice/tools/save-outcome",
            json={
                "lead_id": VOICE_LEAD_ID,
                "business_id": BIZ_1,
                "outcome": "callback_requested",
                "callback_requested": True,
                "callback_time": _future_iso(),
                "callback_timezone": "Asia/Kolkata",
            },
            headers=TOOL_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["callback"]["scheduled"] is False
        assert body["data"]["callback"]["error"]["code"].startswith("CONSENT")

    def test_past_time_rejected(self, tool_client, tool_env, voice_business, voice_lead, voice_consent):
        resp = self._post(
            tool_client,
            {"requested_at": "2026-03-10T12:00:00", "timezone": "Asia/Kolkata"},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "INVALID_PARAMETER"

    def test_unresolvable_timezone_rejected(
        self, tool_client, tool_env, voice_business, voice_lead, voice_consent
    ):
        resp = self._post(
            tool_client,
            {"requested_at": _future_iso(), "timezone": "Mars/Olympus_Mons"},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "INVALID_PARAMETER"

    def test_far_future_time_rejected(
        self, tool_client, tool_env, voice_business, voice_lead, voice_consent
    ):
        resp = self._post(
            tool_client,
            {"requested_at": "2030-01-01T12:00:00", "timezone": "Asia/Kolkata"},
        )
        assert resp.status_code == 422

    def test_save_outcome_with_callback_request(
        self, tool_client, tool_env, voice_business, voice_lead, voice_consent
    ):
        resp = tool_client.post(
            "/api/v1/voice/tools/save-outcome",
            json={
                "lead_id": VOICE_LEAD_ID,
                "business_id": BIZ_1,
                "outcome": "callback_requested",
                "callback_requested": True,
                "callback_time": _future_iso(),
                "callback_timezone": "Asia/Kolkata",
                "callback_reason": "busy now",
            },
            headers=TOOL_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["callback"]["scheduled"] is True
        assert body["data"]["lead_status"] == "callback_requested"


# ---------------------------------------------------------------------------
# CRM sync
# ---------------------------------------------------------------------------


def _mock_crm_client(error=None):
    mock_client = AsyncMock()
    if error is not None:
        mock_client.create_contact = AsyncMock(side_effect=error)
    else:
        mock_client.create_contact = AsyncMock(
            return_value={"id": "CRM-TEST-001", "full_name": "Rahul Sharma"}
        )
    mock_client.update_contact = AsyncMock(
        return_value={"id": "CRM-TEST-001", "full_name": "Rahul Sharma"}
    )
    mock_client.close = AsyncMock()
    return mock_client


def _crm_connection(db, connection_id="default"):
    connection = CRMConnection(
        business_id=BIZ_1,
        connection_id=connection_id,
        name="Test CRM",
        is_active=True,
        crm_base_url="http://localhost:9000",
        crm_api_key="test-api-key",
        crm_timeout_seconds=5.0,
    )
    db.add(connection)
    db.commit()
    return connection


class TestCrmSync:
    def _post(self, tool_client, overrides=None):
        payload = {"lead_id": VOICE_LEAD_ID, "business_id": BIZ_1}
        payload.update(overrides or {})
        return tool_client.post(
            "/api/v1/voice/tools/crm-sync", json=payload, headers=TOOL_HEADERS
        )

    def test_not_configured_is_409(self, tool_client, tool_env, voice_business, voice_lead):
        resp = self._post(tool_client)
        assert resp.status_code == 409
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "CRM_NOT_CONFIGURED"

    def test_successful_sync_with_mocked_crm(
        self, tool_client, tool_env, voice_business, voice_lead, db
    ):
        _crm_connection(db)
        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection",
            return_value=_mock_crm_client(),
        ):
            resp = self._post(tool_client)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["status"] == "created"
        assert body["data"]["external_crm_id"] == "CRM-TEST-001"
        assert body["data"]["sync_id"]

    def test_crm_failure_reported_honestly(
        self, tool_client, tool_env, voice_business, voice_lead, db
    ):
        _crm_connection(db)
        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection",
            return_value=_mock_crm_client(error=CRMUnavailableError("down")),
        ):
            resp = self._post(tool_client)
        assert resp.status_code == 502
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "CRM_SYNC_FAILED"

    def test_crm_auth_failure_reported(
        self, tool_client, tool_env, voice_business, voice_lead, db
    ):
        _crm_connection(db)
        with patch(
            "app.modules.crm_sync.service.CRMClient.from_connection",
            return_value=_mock_crm_client(error=CRMAuthError("bad key")),
        ):
            resp = self._post(tool_client)
        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "CRM_SYNC_FAILED"


# ---------------------------------------------------------------------------
# Bolna Agent Studio definitions
# ---------------------------------------------------------------------------


class TestCustomFunctionsEndpoint:
    def test_six_functions_without_leaking_secret(self, tool_client, tool_env):
        resp = tool_client.get("/api/v1/voice/bolna/custom-functions")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["functions"]) == 6
        names = {fn["name"] for fn in body["functions"]}
        assert names == {
            "get_lead_details",
            "save_call_outcome",
            "qualify_lead",
            "create_opportunity",
            "schedule_callback",
            "sync_lead_to_crm",
        }
        for fn in body["functions"]:
            assert fn["key"] == "custom_task"
            assert fn["value"]["method"] == "POST"
            assert fn["value"]["url"].startswith("http")
            assert TOOL_SECRET not in str(fn)
            assert "<CALLORA_VOICE_TOOL_SECRET>" in fn["value"]["api_token"]
        assert body["configured"] is True

    def test_works_when_unconfigured(self, voice_client, voice_business):
        resp = voice_client.get("/api/v1/voice/bolna/custom-functions")
        assert resp.status_code == 200
        assert resp.json()["configured"] is False
