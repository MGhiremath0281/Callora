"""Tests for the pre-call compliance gate.

The gate is the only thing standing between an API caller and a real
phone call, so these tests cover every mandatory rule, the fail-closed
behaviour of a check that cannot be completed, and the invariant that no
mandatory rule can be skipped.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Lead
from app.modules.voice import repository as repo
from app.modules.voice.compliance import (
    CHECK_NAMES,
    DndLookup,
    evaluate_call,
)
from app.modules.voice.constants import (
    CallStatus,
    CheckOutcome,
    ComplianceDecision,
    ReasonCode,
)
from app.modules.voice.models import Call
from tests.conftest import VOICE_BIZ, VOICE_NOON_UTC, VOICE_PHONE
from tests.fakes import FakeProvider


class StubDndLookup(DndLookup):
    """An external do-not-disturb source with a scripted answer."""

    def __init__(self, on_dnd: bool = False, error: Exception | None = None):
        self.on_dnd = on_dnd
        self.error = error
        self.seen: list[str] = []

    def is_on_dnd(self, phone: str) -> bool:
        self.seen.append(phone)
        if self.error is not None:
            raise self.error
        return self.on_dnd


@pytest.fixture()
def provider():
    return FakeProvider()


def _gate(settings, db, business, lead, provider, now=VOICE_NOON_UTC, dnd_lookup=None, simulated=False):
    """Evaluate the gate for one business/lead pair."""
    return evaluate_call(
        settings,
        db,
        business,
        lead,
        provider,
        now=now,
        dnd_lookup=dnd_lookup,
        simulated=simulated,
    )


def _check(evaluation, name):
    """Return the named check of an evaluation."""
    for check in evaluation.checks:
        if check.name == name:
            return check
    raise AssertionError(f"the check '{name}' was not evaluated")


def _reason_codes(evaluation):
    return {r.get("reason_code") for r in evaluation.blocked_reasons}


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


class TestAllowedCall:
    def test_healthy_business_is_allowed(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.ALLOWED
        assert evaluation.allowed is True
        assert evaluation.blocked_reasons == []

    def test_every_mandatory_check_is_evaluated(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        """A call may not skip a check: all of them must be present."""
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert {c.name for c in evaluation.checks} == set(CHECK_NAMES)

    def test_consent_is_skipped_when_not_required(
        self, voice_settings, db, voice_business, voice_lead, provider
    ):
        """A business that does not require consent skips that check."""
        voice_business.require_consent = False
        db.commit()
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        consent = _check(evaluation, "consent")
        assert consent.outcome == CheckOutcome.SKIPPED
        assert consent.mandatory is False
        assert evaluation.allowed is True


# ---------------------------------------------------------------------------
# Tenancy and configuration
# ---------------------------------------------------------------------------


class TestConfigurationRules:
    def test_inactive_business_is_blocked(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_business.is_active = False
        db.commit()
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.BUSINESS_INACTIVE in _reason_codes(evaluation)

    def test_missing_lead_is_blocked(
        self, voice_settings, db, voice_business, voice_consent, provider
    ):
        evaluation = _gate(voice_settings, db, voice_business, None, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.LEAD_NOT_FOUND in _reason_codes(evaluation)

    def test_unusable_phone_is_blocked(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_lead.phone = "not-a-number"
        db.commit()
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.LEAD_PHONE_INVALID in _reason_codes(evaluation)

    def test_provider_without_credentials_is_blocked(
        self, voice_settings, db, voice_business, voice_lead, voice_consent
    ):
        unconfigured = FakeProvider(configured=False)
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, unconfigured)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.PROVIDER_NOT_CONFIGURED in _reason_codes(evaluation)

    def test_missing_voice_agent_is_blocked(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_business.bolna_agent_id = None
        voice_settings.bolna_default_agent_id = None
        db.commit()
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.PROVIDER_NOT_CONFIGURED in _reason_codes(evaluation)

    def test_provider_is_not_ready_blocks_a_call_that_would_be_placed(
        self, voice_settings, db, voice_business, voice_lead, voice_consent
    ):
        """A call that really reaches the provider needs credentials."""
        unconfigured = FakeProvider(configured=False)
        evaluation = _gate(
            voice_settings, db, voice_business, voice_lead, unconfigured, simulated=False
        )
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.PROVIDER_NOT_CONFIGURED in _reason_codes(evaluation)


# ---------------------------------------------------------------------------
# provider_ready in simulated mode
# ---------------------------------------------------------------------------


class TestProviderReadyInSimulatedMode:
    """A simulated call never reaches the provider.

    Missing credentials are therefore not a compliance problem — they could
    only stop the provider from being contacted. The check is reported as
    *skipped* instead of blocking, so a developer can exercise the whole
    flow without provider credentials. A call that really is placed still
    requires them (see
    ``TestConfigurationRules.test_provider_is_not_ready_blocks_a_call_that_would_be_placed``).
    """

    def test_the_check_is_skipped(
        self, voice_settings, db, voice_business, voice_lead, voice_consent
    ):
        unconfigured = FakeProvider(configured=False)
        evaluation = _gate(
            voice_settings, db, voice_business, voice_lead, unconfigured, simulated=True
        )
        provider_ready = _check(evaluation, "provider_ready")
        assert provider_ready.outcome == CheckOutcome.SKIPPED
        assert provider_ready.mandatory is False

    def test_a_missing_voice_agent_does_not_block(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_business.bolna_agent_id = None
        voice_settings.bolna_default_agent_id = None
        db.commit()
        evaluation = _gate(
            voice_settings, db, voice_business, voice_lead, provider, simulated=True
        )
        assert evaluation.decision == ComplianceDecision.ALLOWED
        assert evaluation.blocked_reasons == []

    def test_the_other_checks_still_block(
        self, voice_settings, db, voice_business, voice_lead, provider
    ):
        """Skipping provider_ready never relaxes the rest of the gate."""
        unconfigured = FakeProvider(configured=False)
        evaluation = _gate(
            voice_settings, db, voice_business, voice_lead, unconfigured, simulated=True
        )
        # No consent record for this lead.
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.CONSENT_MISSING in _reason_codes(evaluation)

    def test_the_decision_is_auditable(
        self, voice_settings, db, voice_business, voice_lead, voice_consent
    ):
        """The skipped check is still reported, so the decision is explainable."""
        unconfigured = FakeProvider(configured=False)
        evaluation = _gate(
            voice_settings, db, voice_business, voice_lead, unconfigured, simulated=True
        )
        assert {c.name for c in evaluation.checks} == set(CHECK_NAMES)
        skipped = [c for c in evaluation.checks if c.outcome == CheckOutcome.SKIPPED]
        assert [c.name for c in skipped] == ["provider_ready"]
        assert "simulated" in _check(evaluation, "provider_ready").details


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------


class TestConsentRule:
    def test_missing_consent_is_blocked(
        self, voice_settings, db, voice_business, voice_lead, provider
    ):
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.CONSENT_MISSING in _reason_codes(evaluation)

    def test_revoked_consent_is_blocked(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_consent.status = "revoked"
        voice_consent.revoked_at = datetime.now(timezone.utc)
        db.commit()
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.CONSENT_REVOKED in _reason_codes(evaluation)

    def test_expired_consent_is_blocked(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_consent.status = "expired"
        voice_consent.expires_at = VOICE_NOON_UTC - timedelta(days=1)
        db.commit()
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.CONSENT_EXPIRED in _reason_codes(evaluation)

    def test_consent_that_expires_in_the_future_is_allowed(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_consent.expires_at = VOICE_NOON_UTC + timedelta(days=30)
        db.commit()
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.allowed is True


# ---------------------------------------------------------------------------
# Do-not-disturb
# ---------------------------------------------------------------------------


class TestDndRule:
    def test_registry_entry_blocks_the_call(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        repo.add_dnd_entry(
            db,
            business_id=VOICE_BIZ,
            phone=VOICE_PHONE,
            reason="the lead asked not to be called",
            source="test",
        )
        db.commit()
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.DND_ACTIVE in _reason_codes(evaluation)

    def test_per_lead_do_not_call_flag_blocks_the_call(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        repo.upsert_lead_profile(
            db, VOICE_BIZ, voice_lead.id, {"do_not_call": True}
        )
        db.commit()
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.DND_LEAD_FLAG in _reason_codes(evaluation)

    def test_global_registry_entry_blocks_every_business(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        repo.add_dnd_entry(
            db,
            business_id="other_business",
            phone=VOICE_PHONE,
            scope="global",
            source="national-registry",
        )
        db.commit()
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.DND_ACTIVE in _reason_codes(evaluation)

    def test_deactivated_registry_entry_does_not_block(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        entry = repo.add_dnd_entry(
            db, business_id=VOICE_BIZ, phone=VOICE_PHONE, source="test"
        )
        repo.deactivate_dnd_entry(db, entry)
        db.commit()
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.allowed is True

    def test_external_dnd_source_blocks_the_call(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        lookup = StubDndLookup(on_dnd=True)
        evaluation = _gate(
            voice_settings, db, voice_business, voice_lead, provider, dnd_lookup=lookup
        )
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.DND_ACTIVE in _reason_codes(evaluation)
        assert lookup.seen == [VOICE_PHONE]

    def test_unreachable_external_dnd_source_fails_closed(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        """A mandatory check that cannot be completed blocks the call."""
        lookup = StubDndLookup(error=RuntimeError("the DND provider is down"))
        evaluation = _gate(
            voice_settings, db, voice_business, voice_lead, provider, dnd_lookup=lookup
        )
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.CHECK_UNAVAILABLE in _reason_codes(evaluation)
        assert _check(evaluation, "dnd").outcome == CheckOutcome.ERROR


# ---------------------------------------------------------------------------
# Calling window
# ---------------------------------------------------------------------------


class TestCallingWindowRule:
    def test_call_outside_the_window_is_blocked(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_business.calling_window_start = 10
        voice_business.calling_window_end = 19
        db.commit()
        # 21:00 in Asia/Kolkata == 15:30 UTC.
        evening = datetime(2026, 3, 11, 15, 30, tzinfo=timezone.utc)
        evaluation = _gate(
            voice_settings, db, voice_business, voice_lead, provider, now=evening
        )
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.OUTSIDE_CALLING_HOURS in _reason_codes(evaluation)

    def test_call_inside_the_window_is_allowed(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_business.calling_window_start = 10
        voice_business.calling_window_end = 19
        db.commit()
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.allowed is True

    def test_window_that_wraps_past_midnight(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_business.calling_window_start = 22
        voice_business.calling_window_end = 6
        db.commit()
        # 23:00 in Asia/Kolkata == 17:30 UTC.
        night = datetime(2026, 3, 11, 17, 30, tzinfo=timezone.utc)
        evaluation = _gate(
            voice_settings, db, voice_business, voice_lead, provider, now=night
        )
        assert evaluation.allowed is True

    def test_call_on_a_blocked_weekday_is_blocked(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        # Only Sunday (6) is allowed; the reference time is a Wednesday.
        voice_business.calling_days = [6]
        db.commit()
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.CALLING_DAY_BLOCKED in _reason_codes(evaluation)

    def test_zero_width_window_means_no_restriction(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_business.calling_window_start = 0
        voice_business.calling_window_end = 0
        db.commit()
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.allowed is True


# ---------------------------------------------------------------------------
# Duplicate calls, quotas and concurrency
# ---------------------------------------------------------------------------


def _call(db, business_id, lead_id, status, created_at=None, **kwargs):
    call = Call(
        business_id=business_id,
        lead_id=lead_id,
        provider="bolna",
        status=status,
        to_number=VOICE_PHONE,
        created_at=created_at or datetime.now(timezone.utc),
        **kwargs,
    )
    db.add(call)
    db.commit()
    return call


class TestDuplicateCallRule:
    def test_call_in_flight_blocks_a_second_call(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        _call(db, VOICE_BIZ, voice_lead.id, CallStatus.QUEUED)
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.DUPLICATE_CALL_IN_FLIGHT in _reason_codes(evaluation)

    def test_finished_call_does_not_block_a_second_call(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        _call(db, VOICE_BIZ, voice_lead.id, CallStatus.COMPLETED)
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.allowed is True

    def test_cooldown_blocks_a_rapid_second_call(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_settings.voice_duplicate_call_cooldown_minutes = 60
        _call(
            db,
            VOICE_BIZ,
            voice_lead.id,
            CallStatus.COMPLETED,
            created_at=VOICE_NOON_UTC - timedelta(minutes=5),
        )
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.DUPLICATE_CALL_COOLDOWN in _reason_codes(evaluation)

    def test_cooldown_expires(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_settings.voice_duplicate_call_cooldown_minutes = 60
        _call(
            db,
            VOICE_BIZ,
            voice_lead.id,
            CallStatus.COMPLETED,
            created_at=VOICE_NOON_UTC - timedelta(minutes=90),
        )
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.allowed is True


class TestQuotaRule:
    def test_daily_lead_quota_blocks_the_call(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_business.max_calls_per_lead_per_day = 1
        db.commit()
        _call(db, VOICE_BIZ, voice_lead.id, CallStatus.COMPLETED)
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.DAILY_LEAD_QUOTA_EXCEEDED in _reason_codes(evaluation)

    def test_daily_business_quota_blocks_the_call(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_business.max_calls_per_business_per_day = 1
        db.commit()
        other_lead = Lead(
            id="lead_other_0001",
            business_id=VOICE_BIZ,
            name="Priya Patel",
            phone="+919812345678",
            source="test",
        )
        db.add(other_lead)
        db.commit()
        _call(db, VOICE_BIZ, other_lead.id, CallStatus.COMPLETED)
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.DAILY_BUSINESS_QUOTA_EXCEEDED in _reason_codes(evaluation)

    def test_blocked_calls_do_not_consume_the_quota(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        """A call the gate blocked was never attempted."""
        voice_business.max_calls_per_lead_per_day = 1
        db.commit()
        _call(db, VOICE_BIZ, voice_lead.id, CallStatus.BLOCKED)
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.allowed is True


class TestConcurrencyRule:
    def test_concurrency_limit_blocks_the_call(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_business.max_concurrent_calls = 1
        db.commit()
        _call(db, VOICE_BIZ, "lead_other_0002", CallStatus.RINGING)
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.CONCURRENCY_LIMIT_REACHED in _reason_codes(evaluation)


# ---------------------------------------------------------------------------
# Fail-closed behaviour
# ---------------------------------------------------------------------------


class TestFailClosed:
    def test_a_raising_check_blocks_the_call(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        """A check that blows up is an error, never an approval."""
        from app.modules.voice.compliance import ComplianceGate

        gate = ComplianceGate(voice_settings, db)
        gate._check_quota = _boom  # type: ignore[method-assign]
        evaluation = gate.evaluate(
            _context(voice_settings, db, voice_business, voice_lead, provider)
        )
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.CHECK_UNAVAILABLE in _reason_codes(evaluation)
        assert _check(evaluation, "quota").outcome == CheckOutcome.ERROR

    def test_all_checks_still_run_after_one_fails(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        from app.modules.voice.compliance import ComplianceGate

        gate = ComplianceGate(voice_settings, db)
        gate._check_quota = _boom  # type: ignore[method-assign]
        evaluation = gate.evaluate(
            _context(voice_settings, db, voice_business, voice_lead, provider)
        )
        assert {c.name for c in evaluation.checks} == set(CHECK_NAMES)

    def test_every_mandatory_check_failure_blocks(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        """No mandatory check may fail without blocking the call."""
        from app.modules.voice.compliance import ComplianceGate

        gate = ComplianceGate(voice_settings, db)
        for name in CHECK_NAMES:
            if name in ("consent", "dnd"):
                # These are skipped (not mandatory) for this business.
                continue
            broken = _break(gate, name)
            evaluation = broken.evaluate(
                _context(voice_settings, db, voice_business, voice_lead, provider)
            )
            assert evaluation.decision == ComplianceDecision.BLOCKED, (
                f"the '{name}' check did not block the call"
            )


def _boom(ctx):
    raise RuntimeError("the check could not be completed")


def _break(gate, name):
    """Return a copy of the gate whose named check always fails."""
    from app.modules.voice.compliance import ComplianceGate

    broken = ComplianceGate(gate._settings, gate._session, gate._dnd_lookup)
    setattr(broken, f"_check_{name}", _boom)
    return broken


def _context(settings, db, business, lead, provider):
    from app.modules.voice.compliance import ComplianceContext
    from app.modules.voice.phone import normalize_phone

    return ComplianceContext(
        business=business,
        lead=lead,
        provider=provider,
        now=VOICE_NOON_UTC,
        profile=repo.get_lead_profile(db, business.id, lead.id),
        phone=normalize_phone(lead.phone),
    )
