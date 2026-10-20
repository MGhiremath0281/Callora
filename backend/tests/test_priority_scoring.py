"""Module 5 — Priority Scoring tests.

Two layers, matching the project's existing patterns:

- pure-function tests for the deterministic scoring engine
  (``priority.py``) — no database, no HTTP, no LLM: normalization and
  boundaries, level thresholds, factor math, missing-data handling,
  determinism;
- end-to-end tests through the existing sweep + cases API — ranking,
  tie-breaking, persistence and idempotency, de-ranking of leads that
  became ineligible, business isolation, pagination, backward
  compatibility of the recovery contract.

Nothing here requires a real LLM or telephony API: scoring never uses
one, and the strategist is either absent (rules-fallback) or replaced
by a test double.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tests.conftest import BIZ_1, BIZ_2, auth_headers

from app.modules.agent_storage.service import upsert_agent_lead
from app.modules.recovery_agent.detect import RecoveryPolicy
from app.modules.recovery_agent.policy import eligibility_config
from app.modules.recovery_agent.priority import (
    FACTOR_ORDER,
    LEVEL_CRITICAL,
    LEVEL_HIGH,
    LEVEL_LOW,
    LEVEL_MEDIUM,
    PRIORITY_LEVELS,
    PriorityConfig,
    priority_level_for,
    score_priority,
)

NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
POLICY = RecoveryPolicy(sla_minutes=120, stall_days=7)
CFG = PriorityConfig()
GATE = eligibility_config()

# 30 days before NOW: the never-contacted SLA and the stall window have
# indisputably elapsed relative to it.
BACKDATED = NOW - timedelta(days=30)

NEVER_SIGNALS = [
    {"signal": "never_contacted", "severity": "high", "detail": "no contact"}
]
STALL_SIGNALS = [
    {"signal": "stalled_follow_up", "severity": "medium", "detail": "last touch old"}
]


def make_lead(**overrides):
    lead = {
        "lead_id": "lead_priority000001",
        "phone": "9876543210",
        "email": "lead@example.com",
        "language": "hindi",
        "deal_value": 5_000_000,
        "last_contact_at": None,
        "created_at": BACKDATED,
        "custom_fields": {},
        "status": "New",
    }
    lead.update(overrides)
    return lead


def factor(score_result, name):
    return next(f for f in score_result.factors if f.factor == name)


# ---------------------------------------------------------------------------
# Pure engine — normalization, boundaries, determinism
# ---------------------------------------------------------------------------


class TestNormalization:
    def test_no_evidence_at_all_scores_zero(self):
        result = score_priority({}, [], POLICY, CFG, NOW)
        assert result.score == 0.0
        assert result.level == LEVEL_LOW
        for f in result.factors:
            assert f.points == 0.0

    def test_full_evidence_scores_one_hundred(self):
        config = PriorityConfig(intent_flags=frozenset({"budget_confirmed"}))
        lead = make_lead(
            deal_value=12_000_000,
            last_contact_at=NOW - timedelta(days=60),
            custom_fields={"budget_confirmed": True},
        )
        signals = [
            {"signal": "stalled_follow_up", "severity": "high", "detail": "old"}
        ]
        result = score_priority(lead, signals, POLICY, config, NOW)
        assert result.score == 100.0
        assert result.level == LEVEL_CRITICAL

    def test_score_is_clamped_to_bounds(self):
        lead = make_lead(deal_value=10**12, last_contact_at=NOW - timedelta(days=9999))
        signals = [
            {"signal": "never_contacted", "severity": "high", "detail": ""},
            {"signal": "stalled_follow_up", "severity": "high", "detail": ""},
        ]
        result = score_priority(lead, signals, POLICY, CFG, NOW)
        assert 0.0 <= result.score <= 100.0

    def test_equivalent_inputs_are_deterministic(self):
        lead = make_lead()
        first = score_priority(lead, NEVER_SIGNALS, POLICY, CFG, NOW)
        second = score_priority(make_lead(), list(NEVER_SIGNALS), POLICY, CFG, NOW)
        assert first == second  # frozen dataclass: every field compared

    def test_breakdown_points_sum_to_score(self):
        result = score_priority(make_lead(), NEVER_SIGNALS, POLICY, CFG, NOW)
        assert round(sum(f.points for f in result.factors), 2) == result.score

    def test_factors_cover_the_documented_order(self):
        result = score_priority(make_lead(), NEVER_SIGNALS, POLICY, CFG, NOW)
        assert tuple(f.factor for f in result.factors) == FACTOR_ORDER
        assert round(sum(f.max_points for f in result.factors), 2) == 100.0


# ---------------------------------------------------------------------------
# Factor math and missing-data handling
# ---------------------------------------------------------------------------


class TestBusinessValueFactor:
    def test_missing_deal_value_is_zero_and_says_so(self):
        result = score_priority(make_lead(deal_value=None), NEVER_SIGNALS, POLICY, CFG, NOW)
        f = factor(result, "business_value")
        assert f.points == 0.0
        assert "deal_value missing" in f.evidence

    def test_unparseable_deal_value_is_zero(self):
        result = score_priority(make_lead(deal_value="n/a"), NEVER_SIGNALS, POLICY, CFG, NOW)
        assert factor(result, "business_value").points == 0.0

    def test_proportional_to_reference(self):
        result = score_priority(make_lead(deal_value=5_000_000), NEVER_SIGNALS, POLICY, CFG, NOW)
        assert factor(result, "business_value").points == 15.0  # 50% of 10M ref

    def test_reference_is_configurable(self):
        config = PriorityConfig(deal_value_reference=5_000_000.0)
        result = score_priority(make_lead(deal_value=5_000_000), NEVER_SIGNALS, POLICY, config, NOW)
        assert factor(result, "business_value").points == 30.0

    def test_decimal_and_negative_values_are_handled(self):
        result = score_priority(
            make_lead(deal_value=Decimal("12000000.00")), NEVER_SIGNALS, POLICY, CFG, NOW
        )
        assert factor(result, "business_value").points == 30.0  # capped at reference
        negative = score_priority(
            make_lead(deal_value=-12_000_000), NEVER_SIGNALS, POLICY, CFG, NOW
        )
        assert factor(negative, "business_value").points == 30.0  # magnitude, not sign


class TestUrgencyFactor:
    def test_no_timing_signal_is_zero(self):
        result = score_priority(make_lead(), [], POLICY, CFG, NOW)
        f = factor(result, "urgency")
        assert f.points == 0.0
        assert "no verified" in f.evidence

    def test_halfway_breach_scores_half_the_factor(self):
        # SLA 120 min + 5040 min breach = created 5160 min ago.
        lead = make_lead(created_at=NOW - timedelta(minutes=5_160))
        result = score_priority(lead, NEVER_SIGNALS, POLICY, CFG, NOW)
        assert factor(result, "urgency").points == 12.5  # 25 × 0.5

    def test_breach_beyond_cap_is_full_points(self):
        result = score_priority(make_lead(), NEVER_SIGNALS, POLICY, CFG, NOW)  # 30 days old
        assert factor(result, "urgency").points == 25.0

    def test_stalled_scales_with_days_over_window(self):
        # Last contact 22 days ago: 15 days beyond the 7-day stall window,
        # half of the 30-day full-points cap.
        lead = make_lead(last_contact_at=NOW - timedelta(days=22))
        result = score_priority(lead, STALL_SIGNALS, POLICY, CFG, NOW)
        assert factor(result, "urgency").points == 12.5

    def test_signal_with_unknown_timestamp_is_not_time_evidence(self):
        lead = make_lead(created_at=None)
        result = score_priority(lead, NEVER_SIGNALS, POLICY, CFG, NOW)
        f = factor(result, "urgency")
        assert f.points == 0.0
        assert "unknown" in f.evidence


class TestBuyingIntentFactor:
    def test_no_engagement_evidence_is_zero(self):
        result = score_priority(make_lead(), NEVER_SIGNALS, POLICY, CFG, NOW)
        f = factor(result, "buying_intent")
        assert f.points == 0.0
        assert "no prior contact recorded" in f.evidence

    def test_prior_contact_is_worth_half(self):
        lead = make_lead(last_contact_at=NOW - timedelta(days=30))
        result = score_priority(lead, STALL_SIGNALS, POLICY, CFG, NOW)
        assert factor(result, "buying_intent").points == 10.0  # 20 × 0.5

    def test_configured_intent_flag_reaches_full(self):
        config = PriorityConfig(intent_flags=frozenset({"budget_confirmed"}))
        lead = make_lead(
            last_contact_at=NOW - timedelta(days=30),
            custom_fields={"budget_confirmed": "yes"},
        )
        result = score_priority(lead, STALL_SIGNALS, POLICY, config, NOW)
        f = factor(result, "buying_intent")
        assert f.points == 20.0
        assert "budget_confirmed" in f.evidence

    def test_unconfigured_flags_are_not_invented(self):
        lead = make_lead(custom_fields={"any_random_column": True})
        result = score_priority(lead, NEVER_SIGNALS, POLICY, CFG, NOW)
        assert factor(result, "buying_intent").points == 0.0


class TestRecoveryPotentialFactor:
    def test_severity_mapping_matches_recoverability_score(self):
        for signals, expected in (
            ([{"signal": "x", "severity": "high", "detail": ""}], 15.0),
            ([{"signal": "x", "severity": "medium", "detail": ""}], 10.5),
            ([{"signal": "x", "severity": "low", "detail": ""}], 6.0),
            ([{"signal": "x", "severity": "surprise", "detail": ""}], 6.0),
        ):
            result = score_priority(make_lead(), signals, POLICY, CFG, NOW)
            assert factor(result, "recovery_potential").points == expected

    def test_no_signals_is_zero_not_optimistic(self):
        result = score_priority(make_lead(), [], POLICY, CFG, NOW)
        f = factor(result, "recovery_potential")
        assert f.points == 0.0
        assert "no verified recovery signals" in f.evidence


class TestContactabilityFactor:
    def test_phone_only_is_partial(self):
        lead = make_lead(email=None, language=None)
        result = score_priority(lead, NEVER_SIGNALS, POLICY, CFG, NOW)
        assert factor(result, "contactability").points == 6.0

    def test_all_real_channels_reach_full(self):
        result = score_priority(make_lead(), NEVER_SIGNALS, POLICY, CFG, NOW)
        assert factor(result, "contactability").points == 10.0

    def test_blank_phone_is_not_contactability(self):
        lead = make_lead(phone="  ", email=None, language=None)
        result = score_priority(lead, NEVER_SIGNALS, POLICY, CFG, NOW)
        f = factor(result, "contactability")
        assert f.points == 0.0
        assert "no usable phone" in f.evidence


# ---------------------------------------------------------------------------
# Levels, weights, explanations
# ---------------------------------------------------------------------------


class TestPriorityLevels:
    def test_default_thresholds(self):
        for score, expected in (
            (80.0, LEVEL_CRITICAL),
            (79.99, LEVEL_HIGH),
            (60.0, LEVEL_HIGH),
            (59.99, LEVEL_MEDIUM),
            (40.0, LEVEL_MEDIUM),
            (39.99, LEVEL_LOW),
            (0.0, LEVEL_LOW),
        ):
            assert priority_level_for(score, CFG) == expected

    def test_thresholds_are_configurable(self):
        config = PriorityConfig(level_critical_min=90.0, level_high_min=70.0, level_medium_min=30.0)
        assert priority_level_for(90.0, config) == LEVEL_CRITICAL
        assert priority_level_for(70.0, config) == LEVEL_HIGH
        assert priority_level_for(30.0, config) == LEVEL_MEDIUM
        assert priority_level_for(29.9, config) == LEVEL_LOW

    def test_levels_cover_every_bucket(self):
        assert PRIORITY_LEVELS == (LEVEL_CRITICAL, LEVEL_HIGH, LEVEL_MEDIUM, LEVEL_LOW)


class TestWeights:
    def test_proportionally_scaled_weights_give_the_same_score(self):
        doubled = PriorityConfig(
            weight_business_value=60.0,
            weight_urgency=50.0,
            weight_intent=40.0,
            weight_recovery_potential=30.0,
            weight_contactability=20.0,
        )
        lead = make_lead(deal_value=7_000_000)
        base = score_priority(lead, NEVER_SIGNALS, POLICY, CFG, NOW)
        scaled = score_priority(lead, NEVER_SIGNALS, POLICY, doubled, NOW)
        assert base.score == scaled.score

    def test_zero_weights_do_not_crash(self):
        zeroed = PriorityConfig(
            weight_business_value=0.0,
            weight_urgency=0.0,
            weight_intent=0.0,
            weight_recovery_potential=0.0,
            weight_contactability=0.0,
        )
        result = score_priority(make_lead(), NEVER_SIGNALS, POLICY, zeroed, NOW)
        assert result.score == 0.0

    def test_config_reads_from_settings(self):
        from app.config import Settings

        settings = Settings(priority_weight_business_value=60.0)
        config = PriorityConfig.from_settings(settings)
        assert config.weight_business_value == 60.0
        assert config.intent_flags == frozenset()  # empty default, never invented


class TestExplanation:
    def test_explanation_names_every_factor(self):
        result = score_priority(make_lead(), NEVER_SIGNALS, POLICY, CFG, NOW)
        for name in FACTOR_ORDER:
            assert name in result.explanation
        assert f"priority {result.score:g}/100" in result.explanation

    def test_breakdown_dict_is_api_shaped(self):
        config = CFG
        result = score_priority(make_lead(), NEVER_SIGNALS, POLICY, config, NOW)
        breakdown = result.as_breakdown(config)
        assert set(breakdown) == {"factors", "explanation", "weights"}
        for f in breakdown["factors"]:
            assert set(f) == {"factor", "points", "max_points", "evidence"}
        assert breakdown["weights"]["business_value"] == 30.0


# ---------------------------------------------------------------------------
# End-to-end: sweep → persisted ranking → cases API
# ---------------------------------------------------------------------------


def seed_lead(db, lead_id="lead_priority000001", **overrides):
    fields = {
        "lead_id": lead_id,
        "business_id": BIZ_1,
        "name": f"Lead {lead_id[-6:]}",
        "phone": "9876543211",
        "email": "lead.test@example.com",
        "source": "magicbricks",
        "language": "hindi",
        "deal_value": 5_000_000,
        "last_contact_at": None,
        "custom_fields": {},
        "status": "New",
        "created_at": BACKDATED,
    }
    fields.update(overrides)
    record = upsert_agent_lead(db, **fields)
    db.commit()
    return record.lead_id


class TestSweepRanking:
    def test_sweep_scores_and_ranks_cases(self, client, db):
        seed_lead(db, "lead_rank000000001", deal_value=2_000_000)
        seed_lead(db, "lead_rank000000002", deal_value=12_000_000)
        seed_lead(db, "lead_rank000000003", deal_value=6_000_000)

        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        assert body["priority"]["scored"] == 3
        assert body["priority"]["order"] == "priority_score desc, score desc, case_id asc"

        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
        items = cases["items"]
        assert [c["priority_rank"] for c in items] == [1, 2, 3]
        scores = [c["priority_score"] for c in items]
        assert scores == sorted(scores, reverse=True)
        # Highest deal value first.
        assert items[0]["lead_name"] == "Lead 000002"
        for c in items:
            assert 0.0 <= c["priority_score"] <= 100.0
            assert c["priority_level"] in PRIORITY_LEVELS
            assert c["scored_at"] is not None
            breakdown = c["score_breakdown"]
            assert [f["factor"] for f in breakdown["factors"]] == list(FACTOR_ORDER)
            assert sum(f["points"] for f in breakdown["factors"]) == c["priority_score"]

        ranking = cases["ranking"]
        assert ranking["total_scored"] == 3
        assert sum(ranking["levels"].values()) == 3
        assert ranking["scored_at"] is not None

    def test_recoverability_score_meaning_is_preserved(self, client, db):
        seed_lead(db)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        case = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"][0]
        # ``score`` stays the Module 3 0..1 recoverability score; the
        # Module 5 score lives in its own 0..100 column.
        assert 0.4 <= case["score"] <= 1.0
        # Default 5M deal → 15 value + 25 urgency + 0 intent + 15 potential + 10 contact.
        assert case["priority_score"] == 65.0

    def test_ranking_is_deterministic_across_reruns(self, client, db):
        seed_lead(db, "lead_det0000000001", deal_value=3_000_000)
        seed_lead(db, "lead_det0000000002", deal_value=9_000_000)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        first = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"]
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        second = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"]

        key = lambda c: c["case_id"]  # noqa: E731
        first_by_id = {key(c): c for c in first}
        for c in second:
            prev = first_by_id[key(c)]
            assert prev["priority_score"] == c["priority_score"]
            assert prev["priority_rank"] == c["priority_rank"]
            assert prev["priority_level"] == c["priority_level"]
            assert prev["score_breakdown"] == c["score_breakdown"]
            assert prev["case_id"] == c["case_id"]

    def test_equal_scores_tie_break_by_case_id(self, client, db):
        # Two identical leads: identical priority scores; the stable
        # tie-break (case_id asc) decides the order and the ranks stay
        # contiguous and unique.
        seed_lead(db, "lead_tie0000000001")
        seed_lead(db, "lead_tie0000000002")
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        items = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"]
        assert len(items) == 2
        assert items[0]["priority_score"] == items[1]["priority_score"]
        assert items[0]["case_id"] < items[1]["case_id"]
        assert [c["priority_rank"] for c in items] == [1, 2]

    def test_missing_deal_value_ranks_below_and_explains(self, client, db):
        seed_lead(db, "lead_nodeal00000001", deal_value=None)
        seed_lead(db, "lead_bigdeal000001", deal_value=20_000_000)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        items = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"]
        assert items[0]["lead_id"] == "lead_bigdeal000001"
        low = items[1]
        value_factor = next(f for f in low["score_breakdown"]["factors"] if f["factor"] == "business_value")
        assert value_factor["points"] == 0.0
        assert "deal_value missing" in value_factor["evidence"]

    def test_pagination_is_stable_and_complete(self, client, db):
        for i in range(5):
            seed_lead(db, f"lead_pagepri{i:08d}", deal_value=(i + 1) * 1_000_000)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())

        def page(offset):
            return client.get(
                f"/api/v1/agent/recovery/cases?limit=2&offset={offset}",
                headers=auth_headers(),
            ).json()["items"]

        full = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"]
        paged = page(0) + page(2) + page(4)
        assert [c["case_id"] for c in paged] == [c["case_id"] for c in full]
        assert [c["priority_rank"] for c in full] == [1, 2, 3, 4, 5]
        # Pagination never distorts the business-wide totals.
        ranking = client.get(
            "/api/v1/agent/recovery/cases?limit=2", headers=auth_headers()
        ).json()["ranking"]
        assert ranking["total_scored"] == 5


class TestEligibilityPreserved:
    def test_ineligible_leads_are_never_ranked(self, client, db):
        seed_lead(db, "lead_elig0000000001", deal_value=50_000_000)  # eligible
        seed_lead(db, "lead_inel0000000001", phone="")  # no contact channel
        seed_lead(db, "lead_inel0000000002", custom_fields={"do_not_contact": True})
        seed_lead(db, "lead_inel0000000003", status="Converted")
        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        assert body["candidates"] == 1
        assert body["priority"]["scored"] == 1

        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
        assert len(cases["items"]) == 1
        assert cases["items"][0]["lead_id"] == "lead_elig0000000001"
        assert cases["ranking"]["total_scored"] == 1

    def test_lead_becoming_ineligible_is_deranked(self, client, db):
        lead_id = seed_lead(db)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        ranked = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"]
        assert ranked[0]["priority_score"] is not None

        # The lead opts out AFTER the case was created. The next sweep
        # must not rank the stale case — and must not invent a new one.
        upsert_agent_lead(
            db,
            lead_id=lead_id,
            business_id=BIZ_1,
            name="Lead 000001",
            phone="9876543211",
            source="magicbricks",
            custom_fields={"do_not_contact": True},
            status="New",
            created_at=BACKDATED,
        )
        db.commit()
        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        assert body["priority"]["scored"] == 0
        assert body["priority"]["deranked_ineligible"] == 1

        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
        assert cases["ranking"]["total_scored"] == 0
        item = cases["items"][0]
        assert item["priority_score"] is None
        assert item["priority_rank"] is None
        assert item["priority_level"] is None
        assert item["score_breakdown"] is None

    def test_act_owned_case_is_never_rescored(self, client, db):
        seed_lead(db)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        case_id = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"][0]["case_id"]

        # The ACT stage moves the case forward — its frozen priority
        # snapshot must survive later sweeps untouched.
        from app.models import RecoveryCase

        case = db.get(RecoveryCase, case_id)
        frozen_score, frozen_rank = case.priority_score, case.priority_rank
        case.status = "in_recovery"
        db.commit()

        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        assert body["priority"]["scored"] == 0
        db.expire_all()
        case = db.get(RecoveryCase, case_id)
        assert case.status == "in_recovery"
        assert case.priority_score == frozen_score
        assert case.priority_rank == frozen_rank

    def test_human_approval_requirement_survives_ranking(self, client, db, monkeypatch):
        import app.modules.recovery_agent.service as service

        def approved_plan(candidate, attempts, block, settings, policy, use_llm=True):
            return {
                "action": "escalate_human",
                "channel": "internal",
                "message": "",
                "rationale": "test double: needs review",
                "needs_human_approval": True,
                "confidence": 0.9,
                "engine": "test-double",
            }

        monkeypatch.setattr(service, "strategize", approved_plan)
        seed_lead(db)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        case = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"][0]
        assert case["needs_human_approval"] is True
        assert case["priority_score"] is not None  # ranking does not strip it


class TestRecalculationAndPersistence:
    def test_scores_recalculate_when_lead_data_changes(self, client, db):
        lead_id = seed_lead(db, deal_value=1_000_000)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        before = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"][0]

        upsert_agent_lead(
            db,
            lead_id=lead_id,
            business_id=BIZ_1,
            name=before["lead_name"],
            phone="9876543211",
            source="magicbricks",
            language="hindi",
            email="lead.test@example.com",
            deal_value=20_000_000,
            status="New",
            created_at=BACKDATED,
        )
        db.commit()
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        after = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"][0]

        assert after["case_id"] == before["case_id"]  # same row, no duplicates
        assert after["priority_score"] > before["priority_score"]
        value_factor = next(f for f in after["score_breakdown"]["factors"] if f["factor"] == "business_value")
        assert value_factor["points"] == 30.0

    def test_repeated_runs_are_idempotent(self, client, db):
        seed_lead(db)
        first = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        second = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        assert first["priority"]["scored"] == second["priority"]["scored"] == 1
        items = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"]
        assert len(items) == 1  # still one row per (business, lead)
        assert items[0]["priority_rank"] == 1

    def test_priority_summary_is_per_business(self, client, db):
        seed_lead(db, "lead_iso0000000001")
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())

        other = client.post("/api/v1/agent/recovery/run", headers=auth_headers(BIZ_2)).json()
        assert other["priority"]["scored"] == 0

        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers(BIZ_2)).json()
        assert cases["items"] == []
        assert cases["ranking"]["total_scored"] == 0
        assert cases["business_id"] == BIZ_2

        mine = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
        assert mine["ranking"]["total_scored"] == 1
        assert all(c["business_id"] == BIZ_1 for c in mine["items"])


class TestConfigurableThresholdsEndToEnd:
    def test_level_threshold_env_override_applies(self, client, db, monkeypatch):
        from app.config import get_settings

        monkeypatch.setenv("CALLORA_PRIORITY_LEVEL_CRITICAL_MIN", "90")
        get_settings.cache_clear()
        try:
            # 12M deal → exactly 80.0 → "critical" by default, "high"
            # once the critical threshold is raised to 90 via env.
            seed_lead(db, deal_value=12_000_000)
            client.post("/api/v1/agent/recovery/run", headers=auth_headers())
            body = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
            assert body["items"][0]["priority_score"] == 80.0
            assert body["items"][0]["priority_level"] == LEVEL_HIGH
            assert body["ranking"]["levels"][LEVEL_CRITICAL] == 0
            assert body["ranking"]["levels"][LEVEL_HIGH] == 1
        finally:
            get_settings.cache_clear()


class TestBackwardCompatibility:
    def test_existing_case_contract_is_unchanged(self, client, db):
        seed_lead(db)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        body = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
        assert {"items", "business_id", "limit", "offset"} <= set(body)
        case = body["items"][0]
        for key in (
            "case_id",
            "lead_id",
            "lead_name",
            "business_id",
            "score",
            "signals",
            "route",
            "action",
            "channel",
            "message",
            "rationale",
            "engine",
            "needs_human_approval",
            "status",
            "created_at",
            "updated_at",
            # additive Module 5 fields
            "priority_score",
            "priority_rank",
            "priority_level",
            "score_breakdown",
            "scored_at",
        ):
            assert key in case, key

    def test_single_case_endpoint_carries_priority_fields(self, client, db):
        seed_lead(db)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        case_id = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"][0]["case_id"]
        case = client.get(
            f"/api/v1/agent/recovery/cases/{case_id}", headers=auth_headers()
        ).json()
        assert case["priority_score"] == 65.0  # default 5M deal value seed
        assert case["priority_rank"] == 1
        assert case["priority_level"] == LEVEL_HIGH  # 65 ∈ [60, 80)
        assert case["score_breakdown"]["explanation"]

    def test_engine_remains_rules_fallback(self, client, db):
        seed_lead(db)
        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        assert body["engine"] == "rules-fallback"  # scoring adds no LLM dependency
        assert set(body["priority"]) == {
            "scored",
            "deranked_ineligible",
            "levels",
            "order",
            "scored_at",
        }
