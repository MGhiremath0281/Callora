"""API tests for the outbound voice module.

These tests drive the real FastAPI application through HTTP, so they
cover the whole stack: authentication, request validation, the
compliance gate, idempotency, the provider webhooks and the operator
endpoints.

Outbound calling is disabled in the fixture, so no test can reach a
voice provider by accident: every accepted call is recorded as
``simulated``.
"""

import pytest

from app.modules.voice import repository as repo
from app.modules.voice.constants import CallStatus, JobStatus, JobType
from app.modules.voice.models import Business, Call, CallEvent, Job
from tests.conftest import BIZ_2, VOICE_BIZ, VOICE_LEAD_ID, VOICE_PHONE, auth_headers

CALLS = "/api/v1/voice/calls"


def _payload(**overrides):
    body = {"lead_id": VOICE_LEAD_ID}
    body.update(overrides)
    return body


@pytest.fixture()
def ready(voice_settings, voice_business, voice_lead, voice_consent):
    """A business, a lead and a granted consent — the happy path."""
    return True


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class TestAuthentication:
    def test_missing_api_key_is_rejected(self, voice_client):
        response = voice_client.post(CALLS, json=_payload())
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_failed"

    def test_invalid_api_key_is_rejected(self, voice_client):
        response = voice_client.post(
            CALLS, json=_payload(), headers={"X-API-Key": "not-the-key"}
        )
        assert response.status_code == 401

    def test_a_valid_api_key_is_accepted(self, voice_client):
        response = voice_client.get("/api/v1/voice/business", headers=auth_headers())
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Call initiation
# ---------------------------------------------------------------------------


class TestCallInitiation:
    def test_a_call_without_consent_is_blocked(
        self, voice_client, voice_business, voice_lead
    ):
        response = voice_client.post(
            CALLS, json=_payload(), headers=auth_headers()
        )
        assert response.status_code == 403
        body = response.json()
        assert body["error"]["code"] == "compliance_blocked"
        reasons = [r["reason_code"] for r in body["error"]["details"]]
        assert "consent_missing" in reasons

    def test_a_blocked_call_is_never_placed(
        self, voice_client, voice_business, voice_lead, db
    ):
        voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        db.rollback()
        call = db.query(Call).one()
        assert call.status == CallStatus.BLOCKED
        assert call.provider_call_id is None

    def test_an_allowed_call_is_recorded_as_simulated(
        self, voice_client, ready
    ):
        response = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["simulated"] is True
        assert body["compliance_decision"] == "allowed"
        assert body["compliance_decision_id"]
        assert body["call"]["status"] == CallStatus.SIMULATED
        assert body["call"]["to_number"] == VOICE_PHONE
        assert body["call"]["simulated"] is True

    def test_the_compliance_checks_are_reported(
        self, voice_client, ready
    ):
        response = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        checks = response.json()["compliance_checks"]
        names = {c["name"] for c in checks}
        assert {"consent", "dnd", "calling_hours", "provider_ready"} <= names
        # Every check except ``provider_ready`` passes. That one is skipped
        # because the call is simulated: the provider is never contacted, so
        # it has no credentials to check.
        by_name = {c["name"]: c for c in checks}
        assert by_name["provider_ready"]["outcome"] == "skipped"
        assert all(
            c["outcome"] == "passed"
            for name, c in by_name.items()
            if name != "provider_ready"
        )

    def test_an_unknown_lead_is_a_404(self, voice_client, ready):
        response = voice_client.post(
            CALLS, json={"lead_id": "lead_does_not_exist"}, headers=auth_headers()
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "lead_not_found"

    def test_a_call_without_provider_credentials_is_simulated(
        self, voice_settings, voice_client, ready
    ):
        """No Bolna key, but the call is simulated: nothing is dialled.

        The provider is never contacted for a simulated call, so missing
        credentials cannot cause a real-world problem.
        """
        voice_settings.bolna_api_key = None
        response = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["simulated"] is True
        assert body["compliance_decision"] == "allowed"
        provider_ready = next(
            c for c in body["compliance_checks"] if c["name"] == "provider_ready"
        )
        assert provider_ready["outcome"] == "skipped"
        assert provider_ready["mandatory"] is False

    def test_a_call_without_provider_credentials_is_blocked_when_outbound_is_enabled(
        self, voice_settings, voice_client, ready
    ):
        """With outbound calling enabled the credentials are mandatory."""
        voice_settings.bolna_api_key = None
        voice_settings.voice_enable_outbound_calls = True
        response = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        assert response.status_code == 403
        reasons = [r["reason_code"] for r in response.json()["error"]["details"]]
        assert "provider_not_configured" in reasons

    def test_a_call_without_a_voice_agent_is_blocked_when_outbound_is_enabled(
        self, voice_settings, voice_client, voice_business, voice_lead, voice_consent, db
    ):
        voice_settings.voice_enable_outbound_calls = True
        voice_settings.bolna_default_agent_id = None
        voice_business.bolna_agent_id = None
        db.commit()
        response = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        assert response.status_code == 403
        reasons = [r["reason_code"] for r in response.json()["error"]["details"]]
        assert "provider_not_configured" in reasons

    def test_a_malformed_request_is_a_422(self, voice_client, ready):
        response = voice_client.post(CALLS, json={}, headers=auth_headers())
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "request_validation_error"

    def test_a_dry_run_is_simulated(self, voice_client, ready):
        response = voice_client.post(
            CALLS, json=_payload(dry_run=True), headers=auth_headers()
        )
        assert response.status_code == 201
        assert response.json()["simulated"] is True

    def test_the_call_is_audited(self, voice_client, ready, db):
        voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        db.rollback()
        actions = [row.action for row in db.query(repo.AuditLog).all()]
        assert "call.requested" in actions
        assert "call.initiated" in actions

    def test_the_compliance_decision_is_persisted(self, voice_client, ready, db):
        response = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        db.rollback()
        decision = db.query(repo.ComplianceDecision).one()
        assert decision.id == response.json()["compliance_decision_id"]
        assert decision.decision == "allowed"
        assert decision.policy_version


# ---------------------------------------------------------------------------
# The development-only quota override
# ---------------------------------------------------------------------------


class TestDevelopmentQuotaOverride:
    """The daily quotas can be skipped in a development environment.

    The override needs two explicit opt-ins — ``CALLORA_DISABLE_CALL_QUOTAS``
    *and* a development ``CALLORA_ENVIRONMENT`` — and it relaxes nothing but
    the two daily quotas.
    """

    @staticmethod
    def _completed_call(db, lead_id=VOICE_LEAD_ID):
        db.add(
            Call(
                business_id=VOICE_BIZ,
                lead_id=lead_id,
                provider="bolna",
                status=CallStatus.COMPLETED,
                to_number=VOICE_PHONE,
            )
        )
        db.commit()

    def test_the_daily_quota_blocks_the_call(
        self, voice_client, voice_business, voice_lead, voice_consent, db
    ):
        voice_business.max_calls_per_lead_per_day = 1
        db.commit()
        self._completed_call(db)
        response = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        assert response.status_code == 403
        reasons = [r["reason_code"] for r in response.json()["error"]["details"]]
        assert "daily_lead_quota_exceeded" in reasons

    def test_the_daily_quota_is_skipped_in_a_development_environment(
        self, voice_settings, voice_client, voice_business, voice_lead, voice_consent, db
    ):
        voice_settings.environment = "local"
        voice_settings.disable_call_quotas = True
        voice_business.max_calls_per_lead_per_day = 1
        db.commit()
        self._completed_call(db)
        response = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["compliance_decision"] == "allowed"
        assert body["simulated"] is True
        quota = next(c for c in body["compliance_checks"] if c["name"] == "quota")
        assert quota["outcome"] == "skipped"
        assert quota["details"]["disabled"] is True

    def test_the_override_is_refused_in_production(
        self, voice_settings, voice_client, voice_business, voice_lead, voice_consent, db
    ):
        voice_settings.environment = "production"
        voice_settings.disable_call_quotas = True
        voice_business.max_calls_per_lead_per_day = 1
        db.commit()
        self._completed_call(db)
        response = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        assert response.status_code == 403
        reasons = [r["reason_code"] for r in response.json()["error"]["details"]]
        assert "daily_lead_quota_exceeded" in reasons

    def test_consent_still_blocks_the_call(
        self, voice_settings, voice_client, voice_business, voice_lead, db
    ):
        """Skipping the quotas never relaxes the consent rule."""
        voice_settings.environment = "local"
        voice_settings.disable_call_quotas = True
        voice_business.max_calls_per_lead_per_day = 1
        db.commit()
        self._completed_call(db)
        response = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        assert response.status_code == 403
        reasons = [r["reason_code"] for r in response.json()["error"]["details"]]
        assert "consent_missing" in reasons

    def test_a_dnd_number_is_still_never_called(
        self, voice_settings, voice_client, voice_business, voice_lead, voice_consent, db
    ):
        """Skipping the quotas never relaxes the do-not-disturb rule."""
        voice_settings.environment = "local"
        voice_settings.disable_call_quotas = True
        repo.add_dnd_entry(
            db,
            business_id=VOICE_BIZ,
            phone=VOICE_PHONE,
            reason="the lead asked not to be called",
            source="test",
        )
        db.commit()
        response = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        assert response.status_code == 403
        reasons = [r["reason_code"] for r in response.json()["error"]["details"]]
        assert "dnd_active" in reasons

    def test_the_configuration_reports_the_override(
        self, voice_settings, voice_client, voice_business
    ):
        response = voice_client.get("/api/v1/voice/business", headers=auth_headers())
        assert response.json()["call_quotas_disabled"] is False
        voice_settings.environment = "local"
        voice_settings.disable_call_quotas = True
        response = voice_client.get("/api/v1/voice/business", headers=auth_headers())
        assert response.json()["call_quotas_disabled"] is True


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_the_same_key_returns_the_same_call(self, voice_client, ready):
        headers = {**auth_headers(), "Idempotency-Key": "key-1"}
        first = voice_client.post(CALLS, json=_payload(), headers=headers)
        second = voice_client.post(CALLS, json=_payload(), headers=headers)
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["call"]["id"] == second.json()["call"]["id"]
        assert "idempotent replay" in second.json()["message"]

    def test_the_same_key_with_a_different_payload_is_a_conflict(
        self, voice_client, ready
    ):
        headers = {**auth_headers(), "Idempotency-Key": "key-2"}
        voice_client.post(CALLS, json=_payload(), headers=headers)
        response = voice_client.post(
            CALLS, json=_payload(language="ta"), headers=headers
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "idempotency_conflict"

    def test_a_replay_creates_no_second_call(self, voice_client, ready, db):
        headers = {**auth_headers(), "Idempotency-Key": "key-3"}
        voice_client.post(CALLS, json=_payload(), headers=headers)
        voice_client.post(CALLS, json=_payload(), headers=headers)
        db.rollback()
        assert db.query(Call).count() == 1

    def test_a_different_key_creates_a_second_call(self, voice_client, ready, db):
        base = auth_headers()
        voice_client.post(
            CALLS, json=_payload(), headers={**base, "Idempotency-Key": "key-a"}
        )
        voice_client.post(
            CALLS, json=_payload(), headers={**base, "Idempotency-Key": "key-b"}
        )
        db.rollback()
        assert db.query(Call).count() == 2

    def test_an_empty_idempotency_key_is_ignored(self, voice_client, ready):
        headers = {**auth_headers(), "Idempotency-Key": "   "}
        response = voice_client.post(CALLS, json=_payload(), headers=headers)
        assert response.status_code == 201

    def test_a_too_long_idempotency_key_is_rejected(self, voice_client, ready):
        headers = {**auth_headers(), "Idempotency-Key": "x" * 200}
        response = voice_client.post(CALLS, json=_payload(), headers=headers)
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class _FakeCache:
    """A cache double that counts calls, like Redis would."""

    def __init__(self, limit_hit_at: int = 2) -> None:
        self.counters = {}
        self.limit_hit_at = limit_hit_at

    def key(self, *parts: str) -> str:
        return ":".join(str(p) for p in parts)

    def incr(self, key: str, ttl_seconds: int = 60):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]


class TestRateLimit:
    def test_the_rate_limit_returns_429(self, voice_client, ready, monkeypatch):
        from app.modules.voice.api import deps

        monkeypatch.setenv("CALLORA_REDIS_ENABLED", "true")
        monkeypatch.setenv("CALLORA_VOICE_RATE_LIMIT_PER_MINUTE", "2")
        from app.config import get_settings

        get_settings.cache_clear()
        cache = _FakeCache()
        monkeypatch.setattr(deps, "get_cache", lambda settings=None: cache)
        try:
            first = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
            second = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
            third = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        finally:
            get_settings.cache_clear()
        assert first.status_code == 201
        assert second.status_code == 201
        assert third.status_code == 429
        assert third.json()["error"]["code"] == "rate_limit_exceeded"


# ---------------------------------------------------------------------------
# Reading calls
# ---------------------------------------------------------------------------


class TestReadCalls:
    def test_the_calls_of_the_business_are_listed(self, voice_client, ready):
        voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        response = voice_client.get(CALLS, headers=auth_headers())
        assert response.status_code == 200
        body = response.json()
        assert body["business_id"] == VOICE_BIZ
        assert len(body["items"]) == 1

    def test_one_call_can_be_read(self, voice_client, ready):
        created = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        call_id = created.json()["call"]["id"]
        response = voice_client.get(f"{CALLS}/{call_id}", headers=auth_headers())
        assert response.status_code == 200
        assert response.json()["id"] == call_id

    def test_an_unknown_call_is_a_404(self, voice_client, ready):
        response = voice_client.get(f"{CALLS}/call_nope", headers=auth_headers())
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "call_not_found"

    def test_another_tenant_cannot_read_the_call(self, voice_client, ready):
        created = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        call_id = created.json()["call"]["id"]
        response = voice_client.get(f"{CALLS}/{call_id}", headers=auth_headers(BIZ_2))
        assert response.status_code == 404

    def test_the_events_of_a_call_are_listed(self, voice_client, ready):
        created = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        call_id = created.json()["call"]["id"]
        response = voice_client.get(
            f"{CALLS}/{call_id}/events", headers=auth_headers()
        )
        assert response.status_code == 200
        assert response.json() == []

    def test_a_missing_outcome_is_a_404(self, voice_client, ready):
        created = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        call_id = created.json()["call"]["id"]
        response = voice_client.get(
            f"{CALLS}/{call_id}/outcome", headers=auth_headers()
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "call_outcome_not_found"

    def test_the_postcall_workflow_can_be_queued(self, voice_client, ready):
        created = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        call_id = created.json()["call"]["id"]
        response = voice_client.post(
            f"{CALLS}/{call_id}/postcall", headers=auth_headers()
        )
        assert response.status_code == 202
        body = response.json()
        assert body["created"] is True
        assert body["job_id"]

    def test_queueing_the_postcall_twice_is_deduplicated(
        self, voice_client, ready
    ):
        created = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        call_id = created.json()["call"]["id"]
        voice_client.post(f"{CALLS}/{call_id}/postcall", headers=auth_headers())
        response = voice_client.post(
            f"{CALLS}/{call_id}/postcall", headers=auth_headers()
        )
        assert response.status_code == 202
        assert response.json()["created"] is False


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


def _execution_payload(**overrides):
    payload = {
        "id": "exec_1",
        "status": "completed",
        "transcript": "Agent: Hello. Rahul: Hi.",
        "conversation_duration": 42,
        "total_cost": 1.25,
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def placed_call(voice_settings, voice_business, voice_lead, voice_consent, db):
    """A call that already exists with the provider."""
    call = Call(
        business_id=VOICE_BIZ,
        lead_id=VOICE_LEAD_ID,
        provider="bolna",
        provider_call_id="exec_1",
        status=CallStatus.IN_PROGRESS,
        to_number=VOICE_PHONE,
    )
    db.add(call)
    db.commit()
    return call


class TestWebhooks:
    URL = "/api/v1/voice/webhooks/bolna/execution"

    def test_an_event_for_an_unknown_call_is_unmatched(self, voice_client):
        response = voice_client.post(self.URL, json=_execution_payload())
        assert response.status_code == 200
        assert response.json()["status"] == "unmatched"

    def test_an_event_is_applied_to_the_call(self, voice_client, placed_call):
        response = voice_client.post(self.URL, json=_execution_payload())
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "processed"
        assert body["call_id"] == placed_call.id
        assert body["call_status"] == CallStatus.COMPLETED

    def test_a_duplicate_event_is_acknowledged_as_duplicate(
        self, voice_client, placed_call
    ):
        voice_client.post(self.URL, json=_execution_payload())
        response = voice_client.post(self.URL, json=_execution_payload())
        assert response.status_code == 200
        assert response.json()["status"] == "duplicate"

    def test_a_terminal_event_queues_the_postcall_job(self, voice_client, placed_call):
        response = voice_client.post(self.URL, json=_execution_payload())
        assert response.json()["job_id"]
        job = voice_client.get(
            f"/api/v1/voice/ops/jobs/{response.json()['job_id']}",
            headers=auth_headers(),
        )
        assert job.status_code == 200
        assert job.json()["type"] == JobType.POSTCALL_PROCESS

    def test_an_empty_body_is_rejected(self, voice_client):
        response = voice_client.post(
            self.URL, content=b"", headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "webhook_payload_invalid"

    def test_a_broken_body_is_rejected(self, voice_client):
        response = voice_client.post(
            self.URL,
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400

    def test_the_event_is_stored(self, voice_client, placed_call, db):
        voice_client.post(self.URL, json=_execution_payload())
        db.rollback()
        event = db.query(CallEvent).one()
        assert event.provider == "bolna"
        assert event.processed is True
        assert event.call_id == placed_call.id

    def test_an_out_of_order_event_does_not_move_the_call_back(
        self, voice_client, placed_call
    ):
        voice_client.post(self.URL, json=_execution_payload())
        response = voice_client.post(
            self.URL, json=_execution_payload(id="exec_1", status="initiated")
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ignored_out_of_order"

    def test_a_pre_transfer_event_creates_a_handoff(self, voice_client, placed_call):
        response = voice_client.post(
            "/api/v1/voice/webhooks/bolna/pre-transfer",
            json={
                "id": "exec_1",
                "status": "in-progress",
                "transfer_reason": "the caller asked for a human",
                "conversation_summary": "The lead wants to talk to a person.",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "processed"
        assert body["handoff_id"]


# ---------------------------------------------------------------------------
# Business configuration
# ---------------------------------------------------------------------------


class TestBusinessConfiguration:
    URL = "/api/v1/voice/business"

    def test_the_configuration_is_read(self, voice_client, voice_business):
        response = voice_client.get(self.URL, headers=auth_headers())
        assert response.status_code == 200
        body = response.json()
        assert body["business_id"] == VOICE_BIZ
        assert body["timezone"] == "Asia/Kolkata"
        assert body["outbound_calls_enabled"] is False
        assert body["provider_configured"] is True

    def test_the_configuration_is_created_on_first_read(self, voice_client, db):
        response = voice_client.get(self.URL, headers=auth_headers(BIZ_2))
        assert response.status_code == 200
        assert response.json()["business_id"] == BIZ_2
        db.rollback()
        assert db.query(Business).filter(Business.id == BIZ_2).count() == 1

    def test_the_configuration_is_updated(self, voice_client, voice_business):
        response = voice_client.patch(
            self.URL,
            json={
                "calling_window_start": 10,
                "calling_window_end": 19,
                "agent_instructions": "Be brief.",
            },
            headers=auth_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["calling_window_start"] == 10
        assert body["calling_window_end"] == 19
        assert body["agent_instructions"] == "Be brief."

    def test_an_update_is_audited(self, voice_client, voice_business, db):
        voice_client.patch(
            self.URL, json={"calling_window_start": 9}, headers=auth_headers()
        )
        db.rollback()
        actions = [row.action for row in db.query(repo.AuditLog).all()]
        assert "business.config_updated" in actions

    def test_an_unknown_field_is_rejected(self, voice_client, voice_business):
        response = voice_client.patch(
            self.URL, json={"nonsense": 1}, headers=auth_headers()
        )
        assert response.status_code == 422

    def test_an_invalid_calling_day_is_rejected(self, voice_client, voice_business):
        response = voice_client.patch(
            self.URL, json={"calling_days": [9]}, headers=auth_headers()
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Leads, consent and DND
# ---------------------------------------------------------------------------


class TestVoiceLeads:
    def test_the_leads_are_listed_with_their_voice_state(
        self, voice_client, voice_business, voice_lead, voice_consent
    ):
        response = voice_client.get("/api/v1/voice/leads", headers=auth_headers())
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["lead_id"] == VOICE_LEAD_ID
        assert items[0]["phone"] == VOICE_PHONE
        assert items[0]["consent"]["status"] == "granted"

    def test_one_lead_is_read(self, voice_client, voice_business, voice_lead):
        response = voice_client.get(
            f"/api/v1/voice/leads/{VOICE_LEAD_ID}", headers=auth_headers()
        )
        assert response.status_code == 200
        assert response.json()["lead_name"] == "Rahul Sharma"

    def test_an_unknown_lead_is_a_404(self, voice_client, voice_business):
        response = voice_client.get(
            "/api/v1/voice/leads/lead_nope", headers=auth_headers()
        )
        assert response.status_code == 404

    def test_the_profile_is_updated(self, voice_client, voice_business, voice_lead):
        response = voice_client.put(
            f"/api/v1/voice/leads/{VOICE_LEAD_ID}/profile",
            json={"preferred_language": "hi", "notes": "VIP"},
            headers=auth_headers(),
        )
        assert response.status_code == 200
        assert response.json()["preferred_language"] == "hi"
        assert response.json()["notes"] == "VIP"

    def test_the_do_not_call_flag_blocks_the_call(
        self, voice_client, ready
    ):
        voice_client.put(
            f"/api/v1/voice/leads/{VOICE_LEAD_ID}/profile",
            json={"do_not_call": True},
            headers=auth_headers(),
        )
        response = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        assert response.status_code == 403
        reasons = [r["reason_code"] for r in response.json()["error"]["details"]]
        assert "dnd_lead_flag" in reasons

    def test_consent_is_recorded(self, voice_client, voice_business, voice_lead):
        response = voice_client.put(
            f"/api/v1/voice/leads/{VOICE_LEAD_ID}/consent",
            json={"status": "granted", "source": "website", "channel": "voice"},
            headers=auth_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "granted"
        assert body["source"] == "website"

    def test_consent_is_listed(self, voice_client, voice_business, voice_lead, voice_consent):
        response = voice_client.get(
            f"/api/v1/voice/leads/{VOICE_LEAD_ID}/consent", headers=auth_headers()
        )
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_an_invalid_consent_status_is_rejected(
        self, voice_client, voice_business, voice_lead
    ):
        response = voice_client.put(
            f"/api/v1/voice/leads/{VOICE_LEAD_ID}/consent",
            json={"status": "maybe"},
            headers=auth_headers(),
        )
        assert response.status_code == 422


class TestDnd:
    def test_a_number_is_added_to_the_registry(
        self, voice_client, voice_business, voice_lead
    ):
        response = voice_client.post(
            "/api/v1/voice/dnd",
            json={"phone": "9876543210", "reason": "asked not to be called"},
            headers=auth_headers(),
        )
        assert response.status_code == 201
        body = response.json()
        assert body["phone"] == "+919876543210"
        assert body["active"] is True

    def test_the_registry_is_listed(self, voice_client, voice_business, voice_lead):
        voice_client.post(
            "/api/v1/voice/dnd", json={"phone": "9876543210"}, headers=auth_headers()
        )
        response = voice_client.get("/api/v1/voice/dnd", headers=auth_headers())
        assert response.status_code == 200
        assert len(response.json()["items"]) == 1

    def test_an_entry_is_deactivated_not_deleted(
        self, voice_client, voice_business, voice_lead
    ):
        created = voice_client.post(
            "/api/v1/voice/dnd", json={"phone": "9876543210"}, headers=auth_headers()
        )
        entry_id = created.json()["id"]
        response = voice_client.delete(
            f"/api/v1/voice/dnd/{entry_id}", headers=auth_headers()
        )
        assert response.status_code == 200
        assert response.json()["active"] is False

    def test_a_dnd_number_is_never_called(self, voice_client, ready):
        voice_client.post(
            "/api/v1/voice/dnd", json={"phone": VOICE_PHONE}, headers=auth_headers()
        )
        response = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        assert response.status_code == 403
        reasons = [r["reason_code"] for r in response.json()["error"]["details"]]
        assert "dnd_active" in reasons

    def test_an_unusable_number_is_rejected(self, voice_client, voice_business):
        response = voice_client.post(
            "/api/v1/voice/dnd", json={"phone": "12"}, headers=auth_headers()
        )
        assert response.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Handoffs
# ---------------------------------------------------------------------------


class TestHandoffs:
    URL = "/api/v1/voice/handoffs"

    def test_a_callback_is_requested(self, voice_client, voice_business, voice_lead):
        response = voice_client.post(
            self.URL,
            json={
                "lead_id": VOICE_LEAD_ID,
                "reason": "the lead asked for a callback",
                "briefing": "Wants a 2BHK in Whitefield.",
            },
            headers=auth_headers(),
        )
        assert response.status_code == 201
        body = response.json()
        assert body["lead_id"] == VOICE_LEAD_ID
        assert body["lead_name"] == "Rahul Sharma"
        assert body["status"] == "requested"

    def test_a_callback_without_a_lead_is_rejected(self, voice_client, voice_business):
        response = voice_client.post(
            self.URL, json={"reason": "nobody"}, headers=auth_headers()
        )
        assert response.status_code == 422

    def test_the_handoffs_are_listed(self, voice_client, voice_business, voice_lead):
        voice_client.post(
            self.URL, json={"lead_id": VOICE_LEAD_ID}, headers=auth_headers()
        )
        response = voice_client.get(self.URL, headers=auth_headers())
        assert response.status_code == 200
        assert len(response.json()["items"]) == 1

    def test_one_handoff_is_read(self, voice_client, voice_business, voice_lead):
        created = voice_client.post(
            self.URL, json={"lead_id": VOICE_LEAD_ID}, headers=auth_headers()
        )
        handoff_id = created.json()["id"]
        response = voice_client.get(f"{self.URL}/{handoff_id}", headers=auth_headers())
        assert response.status_code == 200
        assert response.json()["id"] == handoff_id

    def test_an_unknown_handoff_is_a_404(self, voice_client, voice_business):
        response = voice_client.get(f"{self.URL}/nope", headers=auth_headers())
        assert response.status_code == 404

    def test_a_handoff_is_completed(self, voice_client, voice_business, voice_lead):
        created = voice_client.post(
            self.URL, json={"lead_id": VOICE_LEAD_ID}, headers=auth_headers()
        )
        handoff_id = created.json()["id"]
        response = voice_client.patch(
            f"{self.URL}/{handoff_id}",
            json={"status": "completed", "outcome": "booked a site visit"},
            headers=auth_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["outcome"] == "booked a site visit"

    def test_an_invalid_status_is_rejected(self, voice_client, voice_business, voice_lead):
        created = voice_client.post(
            self.URL, json={"lead_id": VOICE_LEAD_ID}, headers=auth_headers()
        )
        handoff_id = created.json()["id"]
        response = voice_client.patch(
            f"{self.URL}/{handoff_id}",
            json={"status": "teleported"},
            headers=auth_headers(),
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


class TestOps:
    def test_the_stats_are_reported(self, voice_client, ready):
        voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        response = voice_client.get("/api/v1/voice/ops/stats", headers=auth_headers())
        assert response.status_code == 200
        body = response.json()
        assert body["business_id"] == VOICE_BIZ
        assert body["calls_total"] == 1
        assert body["calls_by_status"][CallStatus.SIMULATED] == 1
        assert body["consent_granted"] == 1

    def test_the_jobs_are_listed(self, voice_client, ready):
        voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        response = voice_client.get("/api/v1/voice/ops/jobs", headers=auth_headers())
        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_one_job_is_read(self, voice_client, ready):
        created = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        call_id = created.json()["call"]["id"]
        queued = voice_client.post(
            f"{CALLS}/{call_id}/postcall", headers=auth_headers()
        )
        job_id = queued.json()["job_id"]
        response = voice_client.get(
            f"/api/v1/voice/ops/jobs/{job_id}", headers=auth_headers()
        )
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == job_id
        assert body["status"] == JobStatus.PENDING

    def test_an_unknown_job_is_a_404(self, voice_client, ready):
        response = voice_client.get(
            "/api/v1/voice/ops/jobs/job_nope", headers=auth_headers()
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "job_not_found"

    def test_a_dead_job_can_be_retried(self, voice_client, ready, db):
        created = voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        call_id = created.json()["call"]["id"]
        queued = voice_client.post(
            f"{CALLS}/{call_id}/postcall", headers=auth_headers()
        )
        job_id = queued.json()["job_id"]
        db.rollback()
        job = db.query(Job).filter(Job.id == job_id).one()
        job.status = JobStatus.DEAD
        job.attempts = job.max_attempts
        db.commit()

        response = voice_client.post(
            f"/api/v1/voice/ops/jobs/{job_id}/retry", headers=auth_headers()
        )
        assert response.status_code == 200
        assert response.json()["status"] == JobStatus.PENDING

    def test_the_audit_trail_is_readable(self, voice_client, ready):
        voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        response = voice_client.get("/api/v1/voice/ops/audit", headers=auth_headers())
        assert response.status_code == 200
        items = response.json()["items"]
        assert any(item["action"] == "call.requested" for item in items)

    def test_the_compliance_decisions_are_readable(self, voice_client, ready):
        voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        response = voice_client.get(
            "/api/v1/voice/ops/compliance-decisions", headers=auth_headers()
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["decision"] == "allowed"

    def test_another_tenant_sees_no_audit_trail(self, voice_client, ready):
        voice_client.post(CALLS, json=_payload(), headers=auth_headers())
        response = voice_client.get(
            "/api/v1/voice/ops/audit", headers=auth_headers(BIZ_2)
        )
        assert response.status_code == 200
        assert response.json()["items"] == []
