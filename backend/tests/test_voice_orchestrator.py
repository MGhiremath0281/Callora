"""Tests for the call orchestrator.

The orchestrator is the only component allowed to contact a voice
provider, so these tests pin down the order of operations: evaluate the
compliance gate, persist the decision, record the call, and only then
talk to the provider. A blocked call must never produce a provider
request.
"""

from datetime import datetime, timezone

import pytest

from app.modules.voice import repository as repo
from app.modules.voice.constants import AuditAction, CallStatus, ComplianceDecision
from app.modules.voice.errors import (
    ComplianceBlockedError,
    ProviderConnectionError,
    ProviderError,
)
from app.modules.voice.models import ComplianceDecision as DecisionRow
from app.modules.voice.orchestrator import CallOrchestrator, CallRequest
from tests.conftest import VOICE_BIZ, VOICE_LEAD_ID
from tests.fakes import FakeProvider


def _request(**kwargs):
    params = dict(business_id=VOICE_BIZ, lead_id=VOICE_LEAD_ID)
    params.update(kwargs)
    return CallRequest(**params)


@pytest.fixture()
def orchestrator(voice_settings, db, voice_business, voice_lead):
    """An orchestrator with a fake provider and outbound calling disabled.

    The consent record is deliberately *not* part of this fixture: the
    tests that need a consent record ask for it explicitly, and the
    blocked-call tests must run without one.
    """
    return CallOrchestrator(voice_settings, db, provider=FakeProvider())


# ---------------------------------------------------------------------------
# The simulated path (the default, and the only one that spends no money)
# ---------------------------------------------------------------------------


class TestSimulatedCall:
    def test_allowed_call_is_recorded_as_simulated(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, orchestrator
    ):
        result = orchestrator.initiate(_request())
        assert result.simulated is True
        assert result.call.status == CallStatus.SIMULATED
        assert result.call.provider_call_id is None
        assert result.call.to_number == "+919876543210"
        assert result.evaluation.decision == ComplianceDecision.ALLOWED

    def test_no_provider_is_contacted(
        self, voice_settings, db, voice_business, voice_lead, voice_consent
    ):
        provider = FakeProvider()
        orchestrator = CallOrchestrator(voice_settings, db, provider=provider)
        orchestrator.initiate(_request())
        assert provider.start_calls == []

    def test_the_decision_is_persisted(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, orchestrator
    ):
        result = orchestrator.initiate(_request())
        decision = db.get(DecisionRow, result.decision_id)
        assert decision is not None
        assert decision.decision == ComplianceDecision.ALLOWED
        assert decision.business_id == VOICE_BIZ
        assert decision.lead_id == VOICE_LEAD_ID
        assert decision.policy_version
        assert len(decision.checks) == 9

    def test_the_call_is_audited(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, orchestrator
    ):
        result = orchestrator.initiate(_request())
        entries = repo.list_audit_logs(db, VOICE_BIZ)
        actions = {entry.action for entry in entries}
        assert AuditAction.CALL_REQUESTED in actions
        assert AuditAction.CALL_INITIATED in actions
        request_log = next(
            e for e in entries if e.action == AuditAction.CALL_REQUESTED
        )
        assert request_log.entity_id == result.decision_id

    def test_the_language_falls_back_to_the_business_default(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, orchestrator
    ):
        result = orchestrator.initiate(_request())
        assert result.call.language == "en"

    def test_an_explicit_language_wins(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, orchestrator
    ):
        result = orchestrator.initiate(_request(language="hi"))
        assert result.call.language == "hi"


# ---------------------------------------------------------------------------
# The blocked path
# ---------------------------------------------------------------------------


class TestBlockedCall:
    def test_a_blocked_call_raises(
        self, voice_settings, db, voice_business, voice_lead, orchestrator
    ):
        """No consent on record: the call must not happen."""
        with pytest.raises(ComplianceBlockedError) as excinfo:
            orchestrator.initiate(_request())
        assert excinfo.value.status_code == 403
        assert excinfo.value.reasons

    def test_a_blocked_call_is_never_placed(
        self, voice_settings, db, voice_business, voice_lead
    ):
        provider = FakeProvider()
        orchestrator = CallOrchestrator(voice_settings, db, provider=provider)
        with pytest.raises(ComplianceBlockedError):
            orchestrator.initiate(_request())
        assert provider.start_calls == []

    def test_a_blocked_call_is_recorded(
        self, voice_settings, db, voice_business, voice_lead, orchestrator
    ):
        with pytest.raises(ComplianceBlockedError):
            orchestrator.initiate(_request())
        calls = repo.list_calls(db, VOICE_BIZ)
        assert len(calls) == 1
        assert calls[0].status == CallStatus.BLOCKED
        assert calls[0].blocked_reason
        assert calls[0].provider_call_id is None

    def test_a_blocked_call_is_audited(
        self, voice_settings, db, voice_business, voice_lead, orchestrator
    ):
        with pytest.raises(ComplianceBlockedError):
            orchestrator.initiate(_request())
        entries = repo.list_audit_logs(db, VOICE_BIZ)
        blocked = [e for e in entries if e.action == AuditAction.CALL_BLOCKED]
        assert len(blocked) == 1
        assert blocked[0].decision == ComplianceDecision.BLOCKED
        assert blocked[0].reason_code

    def test_the_decision_is_persisted_for_a_blocked_call(
        self, voice_settings, db, voice_business, voice_lead, orchestrator
    ):
        with pytest.raises(ComplianceBlockedError) as excinfo:
            orchestrator.initiate(_request())
        decision = db.get(DecisionRow, excinfo.value.decision_id)
        assert decision is not None
        assert decision.decision == ComplianceDecision.BLOCKED
        assert decision.blocked_reasons


# ---------------------------------------------------------------------------
# The real path (with a fake provider — no network, no money)
# ---------------------------------------------------------------------------


class TestProviderCall:
    @pytest.fixture()
    def live(self, voice_settings, db, voice_business, voice_lead, voice_consent):
        """An orchestrator that is allowed to place calls."""
        voice_settings.voice_enable_outbound_calls = True
        provider = FakeProvider(execution_id="exec_123", status="queued")
        return CallOrchestrator(voice_settings, db, provider=provider), provider

    def test_the_provider_receives_the_lead_context(
        self, live, db, voice_business, voice_lead, voice_consent
    ):
        orchestrator, provider = live
        orchestrator.initiate(_request())
        assert len(provider.start_calls) == 1
        sent = provider.start_calls[0]
        assert sent.to_number == "+919876543210"
        assert sent.agent_id == "agent_test"
        assert sent.from_number == "+911800000000"
        assert sent.user_data["lead_name"] == "Rahul Sharma"
        assert sent.user_data["business_name"] == "Test Realty"
        assert "budget" in sent.user_data["qualification_questions"]

    def test_extra_context_is_injected(
        self, live, db, voice_business, voice_lead, voice_consent
    ):
        orchestrator, provider = live
        orchestrator.initiate(_request(extra_context={"offer": "flat 10%"}))
        assert provider.start_calls[0].user_data["offer"] == "flat 10%"

    def test_the_provider_handle_is_stored(
        self, live, db, voice_business, voice_lead, voice_consent
    ):
        orchestrator, _provider = live
        result = orchestrator.initiate(_request())
        assert result.simulated is False
        assert result.call.provider_call_id == "exec_123"
        assert result.call.status == CallStatus.QUEUED
        assert result.call.initiated_at is not None

    def test_the_call_is_audited(
        self, live, db, voice_business, voice_lead, voice_consent
    ):
        orchestrator, _provider = live
        result = orchestrator.initiate(_request())
        entries = repo.list_audit_logs(db, VOICE_BIZ)
        initiated = [e for e in entries if e.action == AuditAction.CALL_INITIATED]
        assert len(initiated) == 1
        assert initiated[0].entity_id == result.call.id

    def test_dry_run_never_reaches_the_provider(
        self, live, db, voice_business, voice_lead, voice_consent
    ):
        orchestrator, provider = live
        result = orchestrator.initiate(_request(dry_run=True))
        assert provider.start_calls == []
        assert result.simulated is True
        assert result.call.status == CallStatus.SIMULATED


# ---------------------------------------------------------------------------
# Provider failures
# ---------------------------------------------------------------------------


class TestProviderFailures:
    @pytest.fixture()
    def live(self, voice_settings, db, voice_business, voice_lead, voice_consent):
        voice_settings.voice_enable_outbound_calls = True
        voice_settings.voice_provider_max_attempts = 3
        voice_settings.voice_provider_retry_backoff_seconds = 0.0
        return voice_settings, db

    def test_an_unreachable_provider_is_retried(
        self, live, voice_business, voice_lead, voice_consent
    ):
        settings, db = live
        provider = FakeProvider(fail_with=ProviderConnectionError("no route"))
        orchestrator = CallOrchestrator(settings, db, provider=provider)
        with pytest.raises(ProviderConnectionError):
            orchestrator.initiate(_request())
        assert len(provider.start_calls) == 3

    def test_a_provider_error_is_not_retried(
        self, live, voice_business, voice_lead, voice_consent
    ):
        """A timeout may have placed a billable call: never retry it."""
        settings, db = live
        provider = FakeProvider(fail_with=ProviderError("Bolna returned HTTP 500"))
        orchestrator = CallOrchestrator(settings, db, provider=provider)
        with pytest.raises(ProviderError):
            orchestrator.initiate(_request())
        assert len(provider.start_calls) == 1

    def test_a_failed_call_is_recorded(
        self, live, voice_business, voice_lead, voice_consent
    ):
        settings, db = live
        provider = FakeProvider(fail_with=ProviderError("Bolna returned HTTP 500"))
        orchestrator = CallOrchestrator(settings, db, provider=provider)
        with pytest.raises(ProviderError):
            orchestrator.initiate(_request())
        call = repo.list_calls(db, VOICE_BIZ)[0]
        assert call.status == CallStatus.ERROR
        assert call.error_message
        assert call.ended_at is not None

    def test_a_failure_is_audited(
        self, live, voice_business, voice_lead, voice_consent
    ):
        settings, db = live
        provider = FakeProvider(fail_with=ProviderError("boom"))
        orchestrator = CallOrchestrator(settings, db, provider=provider)
        with pytest.raises(ProviderError):
            orchestrator.initiate(_request())
        entries = repo.list_audit_logs(db, VOICE_BIZ)
        errors = [e for e in entries if e.action == AuditAction.CALL_PROVIDER_ERROR]
        assert len(errors) == 1

    def test_the_decision_survives_a_provider_failure(
        self, live, voice_business, voice_lead, voice_consent
    ):
        """The decision is written before the provider is contacted."""
        settings, db = live
        provider = FakeProvider(fail_with=ProviderError("boom"))
        orchestrator = CallOrchestrator(settings, db, provider=provider)
        with pytest.raises(ProviderError):
            orchestrator.initiate(_request())
        decisions = repo.list_compliance_decisions(db, VOICE_BIZ)
        assert len(decisions) == 1
        assert decisions[0].decision == ComplianceDecision.ALLOWED


# ---------------------------------------------------------------------------
# Refreshing a call from the provider
# ---------------------------------------------------------------------------


class TestRefreshStatus:
    def test_the_provider_state_is_applied(
        self, voice_settings, db, voice_business, voice_lead, voice_consent
    ):
        provider = FakeProvider(status="completed")
        orchestrator = CallOrchestrator(voice_settings, db, provider=provider)
        call = repo.create_call(
            db,
            _call_row(status=CallStatus.QUEUED, provider_call_id="exec_9"),
        )
        db.commit()
        orchestrator.refresh_status(call)
        assert call.status == CallStatus.COMPLETED
        assert provider.get_calls == ["exec_9"]

    def test_a_call_without_a_provider_handle_is_left_alone(
        self, voice_settings, db, voice_business, voice_lead, voice_consent
    ):
        provider = FakeProvider()
        orchestrator = CallOrchestrator(voice_settings, db, provider=provider)
        call = repo.create_call(db, _call_row(status=CallStatus.PENDING))
        db.commit()
        orchestrator.refresh_status(call)
        assert call.status == CallStatus.PENDING
        assert provider.get_calls == []


def _call_row(**kwargs):
    from app.modules.voice.models import Call

    params = dict(
        business_id=VOICE_BIZ,
        lead_id=VOICE_LEAD_ID,
        provider="bolna",
        status=CallStatus.PENDING,
        to_number="+919876543210",
    )
    params.update(kwargs)
    return Call(**params)
