"""Regression tests for the configurable duplicate-call cooldown.

The gap between two calls to the same lead used to be a hardcoded 60
minutes, which made a repeated local test call to the same lead
impossible to exercise in practice. It is now a setting
(``CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_SECONDS``) that a development
deployment can lower to 60 seconds, while the production default stays at
60 minutes.

These tests pin down three things:

* the setting is really read from the environment and the production
  default is still 60 minutes;
* the cooldown is enforced with the *configured* value — a shorter window
  genuinely allows an earlier second call;
* the in-flight guard stays mandatory: while Bolna still reports the
  previous call as queued, ringing or active, a second call to the same
  lead is refused regardless of how short the cooldown is.

Nothing here reaches a provider. The compliance gate never contacts one,
and the provider double records every call it would have received, so a
blocked call can be shown to have produced no outbound call at all.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import Settings
from app.modules.voice.compliance import evaluate_call
from app.modules.voice.constants import (
    CallStatus,
    ComplianceDecision,
    ReasonCode,
)
from app.modules.voice.errors import ComplianceBlockedError
from app.modules.voice.lifecycle import apply_status
from app.modules.voice.models import Call
from app.modules.voice.orchestrator import CallOrchestrator, CallRequest
from tests.conftest import VOICE_BIZ, VOICE_NOON_UTC, VOICE_PHONE
from tests.fakes import FakeProvider

#: The cooldown a local development deployment uses.
LOCAL_COOLDOWN_SECONDS = 60
#: The production default: 60 minutes.
PRODUCTION_COOLDOWN_SECONDS = 3600

#: The repository's shared dotenv file — the one the backend loads at
#: start-up.
REPO_ENV = Path(__file__).resolve().parents[2] / ".env"
#: The repository root (the compose file lives next to the dotenv file).
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def provider():
    return FakeProvider()


def _gate(settings, db, business, lead, provider, now=VOICE_NOON_UTC):
    """Evaluate the gate with every mandatory check in place."""
    return evaluate_call(settings, db, business, lead, provider, now=now)


def _reason_codes(evaluation):
    return {r.get("reason_code") for r in evaluation.blocked_reasons}


def _check(evaluation, name):
    for check in evaluation.checks:
        if check.name == name:
            return check
    raise AssertionError(f"the check '{name}' was not evaluated")


def _call(db, lead_id, status, created_at=None):
    """Record a previous call to the lead."""
    call = Call(
        business_id=VOICE_BIZ,
        lead_id=lead_id,
        provider="bolna",
        status=status,
        to_number=VOICE_PHONE,
        created_at=created_at or VOICE_NOON_UTC,
    )
    db.add(call)
    db.commit()
    return call


# ---------------------------------------------------------------------------
# The setting itself
# ---------------------------------------------------------------------------


class TestCooldownSetting:
    def test_the_production_default_is_sixty_minutes(self, monkeypatch):
        """The default must not drift: production keeps 60 minutes."""
        monkeypatch.delenv(
            "CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_SECONDS", raising=False
        )
        monkeypatch.delenv(
            "CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_MINUTES", raising=False
        )
        settings = Settings()
        assert settings.voice_duplicate_call_cooldown_seconds == (
            PRODUCTION_COOLDOWN_SECONDS
        )
        assert settings.duplicate_call_cooldown_seconds == PRODUCTION_COOLDOWN_SECONDS

    def test_the_environment_variable_is_loaded(self, monkeypatch):
        monkeypatch.setenv(
            "CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_SECONDS",
            str(LOCAL_COOLDOWN_SECONDS),
        )
        settings = Settings()
        assert settings.voice_duplicate_call_cooldown_seconds == (
            LOCAL_COOLDOWN_SECONDS
        )
        assert settings.duplicate_call_cooldown_seconds == LOCAL_COOLDOWN_SECONDS

    def test_zero_disables_the_cooldown(self, monkeypatch):
        monkeypatch.setenv("CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_SECONDS", "0")
        assert Settings().duplicate_call_cooldown_seconds == 0

    def test_the_deprecated_minutes_variable_is_still_honoured(self, monkeypatch):
        """A legacy value keeps its unit: 60 minutes stays 60 minutes."""
        monkeypatch.delenv(
            "CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_SECONDS", raising=False
        )
        monkeypatch.setenv("CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_MINUTES", "60")
        settings = Settings()
        assert settings.duplicate_call_cooldown_seconds == PRODUCTION_COOLDOWN_SECONDS

    def test_the_seconds_variable_wins_over_the_deprecated_one(self, monkeypatch):
        monkeypatch.setenv(
            "CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_SECONDS",
            str(LOCAL_COOLDOWN_SECONDS),
        )
        monkeypatch.setenv("CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_MINUTES", "60")
        settings = Settings()
        assert settings.duplicate_call_cooldown_seconds == LOCAL_COOLDOWN_SECONDS

    def test_the_repository_dotenv_keeps_the_local_loop_short(self, monkeypatch):
        """The running backend loads the repository's ``.env``.

        The local file must configure the cooldown the backend reads, and
        it must never be slower than the 60 seconds a test loop needs.
        """
        if not REPO_ENV.exists():
            pytest.skip("no .env in the repository")
        monkeypatch.delenv(
            "CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_SECONDS", raising=False
        )
        monkeypatch.delenv(
            "CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_MINUTES", raising=False
        )
        content = REPO_ENV.read_text(encoding="utf-8")
        assert "CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_SECONDS" in content, (
            "the repository .env must configure the duplicate-call cooldown"
        )
        settings = Settings(_env_file=REPO_ENV)
        assert 0 <= settings.duplicate_call_cooldown_seconds <= LOCAL_COOLDOWN_SECONDS

    def test_docker_compose_hands_the_setting_to_the_containers(self):
        """The image never reads the repository ``.env``.

        Only ``app/`` is copied into the image, so the setting has to be
        handed to the backend (and the worker) through the compose
        environment — otherwise the containerised backend silently keeps
        the 60 minute default.
        """
        compose = REPO_ROOT / "docker-compose.yml"
        content = compose.read_text(encoding="utf-8")
        occurrences = content.count("CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_SECONDS")
        assert occurrences >= 2, "the backend and the worker must both receive it"
        assert ":-3600}" in content, (
            "the compose default must stay the production value (3600 seconds)"
        )


# ---------------------------------------------------------------------------
# The cooldown is enforced with the configured value
# ---------------------------------------------------------------------------


class TestConfiguredCooldownIsEnforced:
    def test_a_call_inside_the_window_is_blocked(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_settings.voice_duplicate_call_cooldown_seconds = LOCAL_COOLDOWN_SECONDS
        _call(
            db,
            voice_lead.id,
            CallStatus.COMPLETED,
            created_at=VOICE_NOON_UTC - timedelta(seconds=30),
        )
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.DUPLICATE_CALL_COOLDOWN in _reason_codes(evaluation)
        # No outbound call was produced by the blocked request.
        assert provider.start_calls == []

    def test_a_call_after_the_window_is_allowed(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        """The whole point of the setting: 61 seconds later the call goes."""
        voice_settings.voice_duplicate_call_cooldown_seconds = LOCAL_COOLDOWN_SECONDS
        _call(
            db,
            voice_lead.id,
            CallStatus.COMPLETED,
            created_at=VOICE_NOON_UTC - timedelta(seconds=61),
        )
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.allowed is True
        assert provider.start_calls == []

    def test_the_blocked_reason_reports_the_configured_window(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_settings.voice_duplicate_call_cooldown_seconds = LOCAL_COOLDOWN_SECONDS
        _call(
            db,
            voice_lead.id,
            CallStatus.COMPLETED,
            created_at=VOICE_NOON_UTC - timedelta(seconds=30),
        )
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        check = _check(evaluation, "duplicate_call")
        assert check.details["cooldown_seconds"] == LOCAL_COOLDOWN_SECONDS
        assert "60 seconds" in (check.reason or "")

    @pytest.mark.parametrize(
        "status",
        [CallStatus.COMPLETED, CallStatus.FAILED, CallStatus.CANCELED],
    )
    def test_a_terminal_previous_call_allows_a_new_call_after_the_cooldown(
        self,
        voice_settings,
        db,
        voice_business,
        voice_lead,
        voice_consent,
        provider,
        status,
    ):
        """Completed, failed and cancelled all release the lead."""
        voice_settings.voice_duplicate_call_cooldown_seconds = LOCAL_COOLDOWN_SECONDS
        _call(
            db,
            voice_lead.id,
            status,
            created_at=VOICE_NOON_UTC - timedelta(seconds=61),
        )
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.allowed is True

    def test_a_call_older_than_the_configured_window_is_allowed(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        """The old hardcoded 60 minutes would still have blocked this."""
        voice_settings.voice_duplicate_call_cooldown_seconds = LOCAL_COOLDOWN_SECONDS
        _call(
            db,
            voice_lead.id,
            CallStatus.COMPLETED,
            created_at=VOICE_NOON_UTC - timedelta(seconds=90),
        )
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.allowed is True

    def test_the_production_window_still_blocks_a_recent_call(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        """With the production default the 60 minute window is in force."""
        voice_settings.voice_duplicate_call_cooldown_seconds = (
            PRODUCTION_COOLDOWN_SECONDS
        )
        _call(
            db,
            voice_lead.id,
            CallStatus.COMPLETED,
            created_at=VOICE_NOON_UTC - timedelta(minutes=30),
        )
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.DUPLICATE_CALL_COOLDOWN in _reason_codes(evaluation)


# ---------------------------------------------------------------------------
# The in-flight guard is mandatory and independent of the cooldown
# ---------------------------------------------------------------------------


class TestInFlightGuardIsIndependent:
    @pytest.mark.parametrize(
        "status",
        [
            CallStatus.QUEUED,
            CallStatus.RINGING,
            CallStatus.INITIATED,
            CallStatus.IN_PROGRESS,
        ],
    )
    def test_an_active_previous_call_blocks_a_second_call(
        self,
        voice_settings,
        db,
        voice_business,
        voice_lead,
        voice_consent,
        provider,
        status,
    ):
        """Bolna still reports the call as queued, ringing or active.

        The previous call was created long before the cooldown window is
        over, so only the in-flight guard can block this request — and it
        must, whatever the cooldown says.
        """
        voice_settings.voice_duplicate_call_cooldown_seconds = LOCAL_COOLDOWN_SECONDS
        _call(
            db,
            voice_lead.id,
            status,
            created_at=VOICE_NOON_UTC - timedelta(minutes=30),
        )
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.DUPLICATE_CALL_IN_FLIGHT in _reason_codes(evaluation)
        assert provider.start_calls == []

    @pytest.mark.parametrize(
        "status",
        [
            CallStatus.QUEUED,
            CallStatus.RINGING,
            CallStatus.IN_PROGRESS,
        ],
    )
    def test_an_active_previous_call_blocks_even_without_a_cooldown(
        self,
        voice_settings,
        db,
        voice_business,
        voice_lead,
        voice_consent,
        provider,
        status,
    ):
        """Disabling the cooldown never disables the in-flight guard."""
        voice_settings.voice_duplicate_call_cooldown_seconds = 0
        _call(
            db,
            voice_lead.id,
            status,
            created_at=VOICE_NOON_UTC - timedelta(minutes=30),
        )
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.DUPLICATE_CALL_IN_FLIGHT in _reason_codes(evaluation)
        assert provider.start_calls == []

    def test_a_dropped_line_still_respects_the_cooldown(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        """A disconnected call is not in flight, but it is a recent call."""
        voice_settings.voice_duplicate_call_cooldown_seconds = LOCAL_COOLDOWN_SECONDS
        _call(
            db,
            voice_lead.id,
            CallStatus.DISCONNECTED,
            created_at=VOICE_NOON_UTC - timedelta(seconds=30),
        )
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.DUPLICATE_CALL_COOLDOWN in _reason_codes(evaluation)


# ---------------------------------------------------------------------------
# The other compliance rules are untouched
# ---------------------------------------------------------------------------


class TestOtherRulesStillApply:
    def test_a_missing_consent_still_blocks_with_a_short_cooldown(
        self, voice_settings, db, voice_business, voice_lead, provider
    ):
        voice_settings.voice_duplicate_call_cooldown_seconds = LOCAL_COOLDOWN_SECONDS
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.CONSENT_MISSING in _reason_codes(evaluation)

    def test_the_concurrency_limit_still_blocks_with_a_short_cooldown(
        self, voice_settings, db, voice_business, voice_lead, voice_consent, provider
    ):
        voice_settings.voice_duplicate_call_cooldown_seconds = LOCAL_COOLDOWN_SECONDS
        voice_business.max_concurrent_calls = 1
        db.commit()
        _call(db, "lead_other_0002", CallStatus.RINGING)
        evaluation = _gate(voice_settings, db, voice_business, voice_lead, provider)
        assert evaluation.decision == ComplianceDecision.BLOCKED
        assert ReasonCode.CONCURRENCY_LIMIT_REACHED in _reason_codes(evaluation)


# ---------------------------------------------------------------------------
# The whole call flow, through the orchestrator
# ---------------------------------------------------------------------------


class _CountingProvider(FakeProvider):
    """A fake provider that hands out a fresh execution id per call.

    The database refuses two calls with the same provider execution id,
    so a test that places more than one call needs unique ids.
    """

    def __init__(self):
        super().__init__()
        self._counter = 0

    def start_call(self, request):
        self._counter += 1
        self._execution_id = f"exec_fake_{self._counter}"
        return super().start_call(request)


class TestEndToEndThroughTheOrchestrator:
    """The rule as the caller meets it: place, retry, retry again."""

    def test_the_whole_flow(
        self, voice_settings, db, voice_business, voice_lead, voice_consent
    ):
        voice_settings.voice_duplicate_call_cooldown_seconds = LOCAL_COOLDOWN_SECONDS
        # A placed call (not a simulated one) so the provider is involved.
        voice_settings.voice_enable_outbound_calls = True
        provider = _CountingProvider()
        orchestrator = CallOrchestrator(voice_settings, db, provider=provider)
        request = CallRequest(business_id=VOICE_BIZ, lead_id=voice_lead.id)

        # 1. The first call goes through.
        first = orchestrator.initiate(request)
        assert first.call.status == CallStatus.QUEUED
        assert len(provider.start_calls) == 1

        # 2. Bolna still reports it as queued: the in-flight guard blocks
        #    the retry, and no second call reaches the provider.
        with pytest.raises(ComplianceBlockedError) as in_flight:
            orchestrator.initiate(request)
        assert in_flight.value.details[0]["reason_code"] == (
            ReasonCode.DUPLICATE_CALL_IN_FLIGHT
        )
        assert len(provider.start_calls) == 1

        # 3. Bolna confirms the call finished. The lead is still protected,
        #    now by the cooldown instead of the in-flight guard.
        apply_status(db, first.call, CallStatus.COMPLETED, {"status": "completed"})
        with pytest.raises(ComplianceBlockedError) as cooling:
            orchestrator.initiate(request)
        assert cooling.value.details[0]["reason_code"] == (
            ReasonCode.DUPLICATE_CALL_COOLDOWN
        )
        assert len(provider.start_calls) == 1

        # 4. 61 seconds later the retry goes through.
        first.call.created_at = datetime.now(timezone.utc) - timedelta(seconds=61)
        db.commit()
        second = orchestrator.initiate(request)
        assert second.call.status == CallStatus.QUEUED
        assert len(provider.start_calls) == 2
