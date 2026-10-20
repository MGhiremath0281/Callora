"""Unit tests for the Module 3 detection engine (no HTTP, no DB).

Covers the ported signals, the production fixes (tz normalization, CRM
configuration gating), status exclusion, scoring, guardrails and the
strategist's contract validation + rules fallback.
"""

from datetime import datetime, timezone

from app.modules.recovery_agent import strategist as strategist_module
from app.modules.recovery_agent.assess import recoverability
from app.modules.recovery_agent.detect import (
    DetectionContext,
    RecoveryPolicy,
    as_utc,
    detect_signals,
    is_candidate_status,
)
from app.modules.recovery_agent.guardrails import hard_block, route

NOW = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)
POLICY = RecoveryPolicy()


def make_lead(**overrides):
    lead = {
        "lead_id": "lead_000000000000001",
        "business_id": "biz_001",
        "name": "Test Lead",
        "phone": "9876543210",
        "email": "test@example.com",
        "source": "website",
        "status": "New",
        "external_crm_id": None,
        "language": "english",
        "deal_value": 5_000_000,
        "last_contact_at": None,
        "custom_fields": {},
        "created_at": "2026-10-18T10:00:00+00:00",
        "updated_at": "2026-10-18T10:00:00+00:00",
    }
    lead.update(overrides)
    return lead


class TestTimestampNormalization:
    def test_iso_string_to_aware_utc(self):
        assert as_utc("2026-10-18T10:00:00+00:00") == datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc)

    def test_naive_assumed_utc(self):
        assert as_utc("2026-10-18T10:00:00") == datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc)

    def test_offset_converted_to_utc(self):
        result = as_utc("2026-10-18T15:30:00+05:30")
        assert result == datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc)

    def test_empty_and_invalid(self):
        assert as_utc(None) is None
        assert as_utc("") is None
        assert as_utc("not-a-date") is None


class TestStatusExclusion:
    def test_new_and_unknown_are_candidates(self):
        assert is_candidate_status("New") is True
        assert is_candidate_status(None) is True
        assert is_candidate_status("Brand New State") is True

    def test_active_and_converted_are_excluded(self):
        for status in ("Contacted", "qualified", "CONVERTED", "Closed Lost", "opted out"):
            assert is_candidate_status(status) is False, status


class TestDetectSignals:
    def test_never_contacted_past_sla(self):
        signals = detect_signals(make_lead(), NOW, POLICY, DetectionContext())
        assert [s["signal"] for s in signals] == ["never_contacted"]
        assert signals[0]["severity"] == "high"

    def test_no_signal_within_sla(self):
        fresh = make_lead(created_at="2026-10-19T08:30:00+00:00")  # 30 min < 120 min SLA
        assert detect_signals(fresh, NOW, POLICY, DetectionContext()) == []

    def test_stalled_follow_up(self):
        lead = make_lead(last_contact_at="2026-10-01T10:00:00+00:00")
        signals = detect_signals(lead, NOW, POLICY, DetectionContext())
        assert [s["signal"] for s in signals] == ["stalled_follow_up"]

    def test_recent_contact_is_healthy(self):
        lead = make_lead(last_contact_at="2026-10-18T10:00:00+00:00")  # ~23h < 7 days
        assert detect_signals(lead, NOW, POLICY, DetectionContext()) == []

    def test_missing_created_at_skips_s1_without_crashing(self):
        lead = make_lead()
        del lead["created_at"]
        assert detect_signals(lead, NOW, POLICY, DetectionContext()) == []

    def test_crm_drift_only_when_crm_configured(self):
        lead = make_lead(external_crm_id=None)
        without_crm = detect_signals(lead, NOW, POLICY, DetectionContext(crm_configured=False))
        assert "crm_sync_drift" not in [s["signal"] for s in without_crm]

        with_crm = detect_signals(lead, NOW, POLICY, DetectionContext(crm_configured=True))
        assert "crm_sync_drift" in [s["signal"] for s in with_crm]

    def test_synced_lead_has_no_drift(self):
        lead = make_lead(external_crm_id="CRM-0001")
        signals = detect_signals(lead, NOW, POLICY, DetectionContext(crm_configured=True))
        assert "crm_sync_drift" not in [s["signal"] for s in signals]

    def test_event_delivery_failed(self):
        ctx = DetectionContext(failed_event_lead_ids=frozenset({"lead_000000000000001"}))
        signals = detect_signals(make_lead(), NOW, POLICY, ctx)
        assert "event_delivery_failed" in [s["signal"] for s in signals]

    def test_database_timestamps_with_tz_are_handled(self):
        # The API emits tz-aware ISO strings; naive values must behave the same.
        aware = make_lead(created_at="2026-10-18T10:00:00+00:00")
        naive = make_lead(created_at="2026-10-18T10:00:00")
        assert detect_signals(aware, NOW, POLICY, DetectionContext()) == detect_signals(
            naive, NOW, POLICY, DetectionContext()
        )


class TestRecoverability:
    def test_high_value_orders_first(self):
        big = recoverability(make_lead(deal_value=25_000_000), [{"signal": "never_contacted", "severity": "high", "detail": ""}], 0)
        small = recoverability(make_lead(deal_value=3_500_000), [{"signal": "never_contacted", "severity": "high", "detail": ""}], 0)
        assert big > small
        assert big == 0.95  # 0.35(value cap) + 0.25 + 0.20 + 0.15, no drift bonus

    def test_null_deal_value_does_not_crash(self):
        score = recoverability(make_lead(deal_value=None), [{"signal": "never_contacted", "severity": "high", "detail": ""}], 0)
        assert 0.0 <= score <= 1.0

    def test_opt_out_zeroes_reachability(self):
        signals = [{"signal": "never_contacted", "severity": "high", "detail": ""}]
        normal = recoverability(make_lead(), signals, 0)
        opted_out = recoverability(make_lead(custom_fields={"opt_out": True}), signals, 0)
        assert opted_out < normal

    def test_attempts_reduce_score(self):
        signals = [{"signal": "never_contacted", "severity": "high", "detail": ""}]
        assert recoverability(make_lead(), signals, 2) < recoverability(make_lead(), signals, 0)


class TestGuardrails:
    def test_opt_out_is_blocked(self):
        reason = hard_block(make_lead(custom_fields={"opt_out": True}), NOW, POLICY, 0)
        assert reason == "lead opted out of outreach"

    def test_max_attempts_blocked(self):
        reason = hard_block(make_lead(), NOW, RecoveryPolicy(max_attempts=2), 2)
        assert "max attempts" in reason

    def test_quiet_hours_off_by_default(self):
        night = datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc)
        assert hard_block(make_lead(), night, POLICY, 0) is None  # recommendations at night

    def test_quiet_hours_enforced_when_enabled(self):
        night = datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc)
        strict = RecoveryPolicy(enforce_quiet_hours=True)
        assert hard_block(make_lead(), night, strict, 0) == "quiet hours"

    def test_no_phone_blocked(self):
        assert hard_block(make_lead(phone=""), NOW, POLICY, 0) == "no reachable channel"

    def test_route_auto_for_system_signals_only(self):
        signals = [{"signal": "crm_sync_drift", "severity": "medium", "detail": ""}]
        assert route(make_lead(), signals, NOW, POLICY, 0) == "auto"

    def test_route_llm_for_neglected_lead(self):
        signals = [{"signal": "never_contacted", "severity": "high", "detail": ""}]
        assert route(make_lead(), signals, NOW, POLICY, 0) == "llm"

    def test_route_hitl_after_prior_attempts(self):
        signals = [{"signal": "never_contacted", "severity": "high", "detail": ""}]
        assert route(make_lead(), signals, NOW, POLICY, 1) == "hitl"

    def test_route_block_wins_everything(self):
        signals = [{"signal": "never_contacted", "severity": "high", "detail": ""}]
        lead = make_lead(custom_fields={"opt_out": True})
        assert route(lead, signals, NOW, POLICY, 0) == "block"


class TestStrategist:
    def _candidate(self, **lead_overrides):
        return {
            "lead": make_lead(**lead_overrides),
            "signals": [{"signal": "never_contacted", "severity": "high", "detail": "x"}],
            "score": 0.8,
        }

    def test_rules_fallback_without_key(self, settings):
        plan = strategist_module.strategize(self._candidate(), 0, None, settings, POLICY)
        assert plan["engine"] == "rules-fallback"
        assert plan["action"] in strategist_module.ALLOWED_ACTIONS
        assert plan["channel"] in strategist_module.VALID_CHANNELS

    def test_blocked_lead_gets_archive_plan(self, settings):
        plan = strategist_module.strategize(
            self._candidate(custom_fields={"opt_out": True}), 0, "lead opted out of outreach", settings, POLICY
        )
        assert plan["action"] == "archive"

    def test_valid_llm_output_is_tagged_and_coerced(self, settings, monkeypatch):
        monkeypatch.setattr(strategist_module, "llm_available", lambda s: True)
        monkeypatch.setattr(
            strategist_module,
            "groq_chat",
            lambda s, sys, usr: {
                "action": "re_contact_lead",
                "channel": "carrier-pigeon",  # invalid -> coerced to None
                "message": 123,  # coerced to str
                "rationale": "test",
                "needs_human_approval": 1,
                "confidence": "0.9",
            },
        )
        plan = strategist_module.strategize(self._candidate(), 0, None, settings, POLICY)
        assert plan["engine"].startswith("groq:")
        assert plan["channel"] is None
        assert plan["message"] == "123"
        assert plan["needs_human_approval"] is True
        assert plan["confidence"] == 0.9

    def test_invalid_llm_action_falls_back_to_rules(self, settings, monkeypatch):
        monkeypatch.setattr(strategist_module, "llm_available", lambda s: True)
        monkeypatch.setattr(
            strategist_module,
            "groq_chat",
            lambda s, sys, usr: {"action": "launch_missiles", "channel": None, "message": ""},
        )
        plan = strategist_module.strategize(self._candidate(), 0, None, settings, POLICY)
        assert plan["action"] in strategist_module.ALLOWED_ACTIONS
        assert plan["engine"] == "rules-fallback"
        assert "invalid LLM action" in plan["rationale"]

    def test_llm_error_falls_back_to_rules(self, settings, monkeypatch):
        monkeypatch.setattr(strategist_module, "llm_available", lambda s: True)
        monkeypatch.setattr(strategist_module, "groq_chat", lambda s, sys, usr: None)
        plan = strategist_module.strategize(self._candidate(), 0, None, settings, POLICY)
        assert plan["engine"] == "rules-fallback"

    def test_language_hint_in_fallback_message(self, settings):
        plan = strategist_module.strategize(
            self._candidate(language="hindi"), 0, None, settings, POLICY
        )
        assert plan["channel"] == "whatsapp"  # notebook parity: hindi -> whatsapp
