"""Tests for the eligibility-engine behaviour of the recovery sweep.

These pin the engine-level integration of the deterministic gate:

- system-signal evidence (confirmed CRM drift) reaches the gate even when
  the lead's own timestamps are fresh — detect runs before eligibility;
- a case that advanced past ``recommended`` is excluded from the sweep
  and its row is left completely untouched;
- duplicate entries in one payload are counted once and never persisted
  twice (no IntegrityError, no crash);
- the response invariant
  ``fetched == evaluated + skipped_inactive + skipped_invalid`` always
  holds;
- the gate honours pre-computed signals, so the sweep and the standalone
  gate can never disagree about the same lead.
"""

from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import BIZ_1, auth_headers

from app.models import (
    CRMConnection,
    CRMSyncRecord,
    CRMSyncStatus,
    RecoveryCase,
    RecoveryCaseStatus,
)
from app.modules.agent_storage.service import upsert_agent_lead
from app.modules.recovery_agent.detect import (
    DetectionContext,
    RecoveryPolicy,
    SIGNAL_CRM_SYNC_DRIFT,
    SIGNAL_IS_RECOVERED,
    SIGNAL_NEVER_CONTACTED,
    as_utc,
)
from app.modules.recovery_agent.eligibility import (
    REASON_NO_RECOVERY_EVIDENCE,
    check_eligibility,
)
from app.modules.recovery_agent.policy import eligibility_config
from app.modules.recovery_agent.service import run_recovery_engine

NOW = datetime.now(timezone.utc)
POLICY = RecoveryPolicy()
BACKDATED = NOW - timedelta(days=30)  # past SLA / stall window
FRESH = NOW - timedelta(minutes=5)  # inside SLA and stall window


def make_lead(**overrides):
    lead = {
        "lead_id": "lead_eng000000000001",
        "business_id": BIZ_1,
        "name": "Engine Lead",
        "phone": "9876543211",
        "email": "engine@example.com",
        "source": "website",
        "status": "New",
        "external_crm_id": None,
        "language": "english",
        "deal_value": 12_500_000,
        "last_contact_at": None,
        "custom_fields": {},
        "created_at": BACKDATED.isoformat(),
    }
    lead.update(overrides)
    return lead


def seed_lead(db, lead_id, **overrides):
    fields = {
        "lead_id": lead_id,
        "business_id": BIZ_1,
        "name": "Engine Lead",
        "phone": "9876543211",
        "source": "magicbricks",
        "language": "english",
        "deal_value": 12_500_000,
        "custom_fields": {},
        "status": "New",
        "created_at": BACKDATED,
    }
    fields.update(overrides)
    record = upsert_agent_lead(db, **fields)
    db.commit()
    return record.lead_id


def seed_crm_connection(db):
    db.add(
        CRMConnection(
            business_id=BIZ_1,
            connection_id="eng-conn",
            name="Eng CRM",
            is_active=True,
            crm_base_url="http://localhost:9000",
            crm_api_key="k",
            crm_timeout_seconds=5.0,
        )
    )
    db.commit()


def seed_confirmed_sync(db, lead_id):
    db.add(
        CRMSyncRecord(
            business_id=BIZ_1,
            lead_id=lead_id,
            connection_id="eng-conn",
            status=CRMSyncStatus.CREATED,
            external_crm_id="CRM-ENG-1",
        )
    )
    db.commit()


def case_rows(db):
    return db.query(RecoveryCase).all()


# ---------------------------------------------------------------------------
# Duplicate entries in one payload
# ---------------------------------------------------------------------------


class TestDuplicatePayloadDedupe:
    def test_duplicate_entries_are_counted_once_and_persisted_once(
        self, db, settings
    ):
        lead = make_lead()
        summary = run_recovery_engine(
            db, BIZ_1, [lead, dict(lead), dict(lead)], settings, now=NOW
        )
        assert summary["fetched"] == 3
        assert summary["evaluated"] == 1
        assert summary["skipped_invalid"] == 2  # the two duplicates
        assert summary["candidates"] == 1
        assert summary["cases_upserted"] == 1
        assert len(case_rows(db)) == 1

    def test_duplicate_via_prototype_alias_dedupes(self, db, settings):
        # One entry uses the contract field, one the notebook alias —
        # they normalize to the same lead and must collapse to one case.
        alias = make_lead()
        del alias["lead_id"]
        alias["id"] = "lead_eng000000000001"
        summary = run_recovery_engine(
            db, BIZ_1, [make_lead(), alias], settings, now=NOW
        )
        assert summary["evaluated"] == 1
        assert summary["skipped_invalid"] == 1
        assert len(case_rows(db)) == 1

    def test_ineligible_duplicates_do_not_crash_the_sweep(self, db, settings):
        lead = make_lead(phone="")  # would be skipped_invalid anyway
        summary = run_recovery_engine(db, BIZ_1, [lead, dict(lead)], settings, now=NOW)
        assert summary["candidates"] == 0
        assert summary["cases_upserted"] == 0
        assert len(case_rows(db)) == 0


# ---------------------------------------------------------------------------
# System-signal evidence: fresh lead with confirmed CRM drift
# ---------------------------------------------------------------------------


class TestFreshLeadConfirmedDrift:
    def test_confirmed_drift_alone_is_eligible_end_to_end(self, client, db):
        seed_crm_connection(db)
        seed_lead(db, "lead_engdrift00001", external_crm_id=None, created_at=FRESH)
        seed_confirmed_sync(db, "lead_engdrift00001")

        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()

        # The lead is fresh (no never_contacted) — only the DB-confirmed
        # drift can carry it through the evidence rule.
        assert body["skipped_invalid"] == 0
        assert body["evaluated"] == 1
        assert body["candidates"] == 1
        assert body["cases_upserted"] == 1

        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
        signals = {s["signal"] for s in cases["items"][0]["signals"]}
        assert signals == {SIGNAL_CRM_SYNC_DRIFT}
        # System-only signals need no LLM composition.
        assert cases["items"][0]["route"] == "auto"

    def test_unconfirmed_fresh_lead_is_not_eligible(self, client, db):
        seed_crm_connection(db)
        # Another lead was confirmed synced; this one was not.
        seed_confirmed_sync(db, "lead_engother0000001")
        seed_lead(db, "lead_engunknown00001", external_crm_id=None, created_at=FRESH)

        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()

        assert body["evaluated"] == 0
        assert body["skipped_invalid"] == 1  # no evidence, not eligibility
        assert body["cases_upserted"] == 0
        assert case_rows(db) == []


# ---------------------------------------------------------------------------
# Cases that advanced past "recommended" are excluded and untouched
# ---------------------------------------------------------------------------


class TestAdvancedCaseExcluded:
    def _seed_case(self, db, client, status):
        seed_lead(db, "lead_engcase0000001")
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        case = case_rows(db)[0]
        case.status = status
        db.commit()
        return case

    @pytest.mark.parametrize(
        "status",
        [
            RecoveryCaseStatus.IN_RECOVERY,
            RecoveryCaseStatus.RECOVERED,
            RecoveryCaseStatus.EXHAUSTED,
            RecoveryCaseStatus.ARCHIVED,
        ],
    )
    def test_advanced_case_is_skipped_and_untouched(self, client, db, status):
        case = self._seed_case(db, client, status)
        before = (case.status, case.score, case.message, case.updated_at)

        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()

        assert body["fetched"] == 1
        assert body["evaluated"] == 0
        assert body["skipped_inactive"] == 1
        assert body["candidates"] == 0
        assert body["cases_upserted"] == 0

        db.expire_all()
        row = case_rows(db)[0]
        # SQLite re-reads timestamps naive; normalize before comparing.
        assert (row.status, row.score, row.message, as_utc(row.updated_at)) == (
            before[0],
            before[1],
            before[2],
            as_utc(before[3]),
        )
        assert len(case_rows(db)) == 1

    def test_recommended_case_is_still_refreshed(self, client, db):
        case = self._seed_case(db, client, RecoveryCaseStatus.RECOMMENDED)

        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()

        assert body["evaluated"] == 1
        assert body["cases_upserted"] == 1
        db.expire_all()
        refreshed = case_rows(db)[0]
        assert refreshed.id == case.id
        assert refreshed.status == RecoveryCaseStatus.RECOMMENDED
        assert as_utc(refreshed.updated_at) >= as_utc(case.updated_at)


# ---------------------------------------------------------------------------
# Healthy leads land in the engine bucket, never in a case
# ---------------------------------------------------------------------------


class TestHealthyLeadBucket:
    def test_healthy_lead_is_skipped_invalid_without_a_case(self, client, db):
        seed_lead(
            db,
            "lead_enghealthy0001",
            created_at=FRESH,
            last_contact_at=NOW - timedelta(hours=1),
        )

        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()

        assert body["fetched"] == 1
        assert body["evaluated"] == 0
        assert body["signals_detected"] == 0
        assert body["skipped_invalid"] == 1  # no evidence = not eligible
        assert body["skipped_inactive"] == 0
        assert body["cases_upserted"] == 0
        assert case_rows(db) == []


# ---------------------------------------------------------------------------
# Count invariant across a mixed batch
# ---------------------------------------------------------------------------


class TestMixedBatchInvariant:
    def test_counts_partition_the_payload(self, db, settings):
        leads = [
            make_lead(),  # eligible (never_contacted)
            make_lead(lead_id="lead_enghealthy0002", created_at=FRESH.isoformat(),
                      last_contact_at=(NOW - timedelta(hours=1)).isoformat()),  # healthy
            make_lead(lead_id="lead_engactive00001", status="Contacted"),  # inactive
            make_lead(lead_id="lead_engdnc0000001",
                      custom_fields={"do_not_contact": True}),  # opted out
            make_lead(lead_id="lead_engnophone0001", phone=""),  # no channel
            make_lead(),  # duplicate of the first entry
        ]

        summary = run_recovery_engine(db, BIZ_1, leads, settings, now=NOW)

        assert summary["fetched"] == 6
        assert summary["fetched"] == (
            summary["evaluated"]
            + summary["skipped_inactive"]
            + summary["skipped_invalid"]
        )
        assert summary["evaluated"] == 1
        assert summary["skipped_inactive"] == 1
        assert summary["skipped_invalid"] == 4
        assert summary["candidates"] == 1
        assert summary["cases_upserted"] == 1
        assert len(case_rows(db)) == 1


# ---------------------------------------------------------------------------
# The gate honours pre-computed signals (sweep and gate always agree)
# ---------------------------------------------------------------------------


class TestGateWithSignals:
    @pytest.fixture()
    def cfg(self, settings):
        return eligibility_config(settings)

    def test_system_signal_makes_fresh_lead_eligible(self, cfg):
        lead = make_lead(created_at=FRESH.isoformat())
        decision = check_eligibility(
            lead,
            NOW,
            POLICY,
            cfg,
            signals=[{"signal": SIGNAL_CRM_SYNC_DRIFT, "severity": "medium", "detail": ""}],
        )
        assert decision.eligible is True

    def test_empty_signals_is_not_evidence(self, cfg):
        decision = check_eligibility(make_lead(), NOW, POLICY, cfg, signals=[])
        assert decision.eligible is False
        assert decision.reason == REASON_NO_RECOVERY_EVIDENCE

    def test_recovered_signal_alone_is_not_evidence(self, cfg):
        # The lead itself is NOT flagged recovered (rule 3 would fire);
        # only the signal list carries it — the evidence rule must still
        # reject it.
        decision = check_eligibility(
            make_lead(),
            NOW,
            POLICY,
            cfg,
            signals=[{"signal": SIGNAL_IS_RECOVERED, "severity": "low", "detail": ""}],
        )
        assert decision.eligible is False
        assert decision.reason == REASON_NO_RECOVERY_EVIDENCE

    def test_signals_take_precedence_over_timestamps(self, cfg):
        # A stale lead with no detected signals is NOT evidence-eligible
        # when the signal list is supplied: the sweep decision comes from
        # the detector, never from a second, divergent timestamp check.
        stale = make_lead(created_at=(NOW - timedelta(days=30)).isoformat())
        assert check_eligibility(stale, NOW, POLICY, cfg, signals=[]).eligible is False
        # Without a signal list the timestamp fallback still applies.
        assert check_eligibility(stale, NOW, POLICY, cfg).eligible is True

    def test_stall_window_boundary_is_strict(self, cfg):
        # Exactly at the stall window: not yet stalled (same strict
        # comparison the detector uses).
        on_boundary = make_lead(
            created_at=(NOW - timedelta(days=60)).isoformat(),
            last_contact_at=(NOW - timedelta(days=POLICY.stall_days)).isoformat(),
        )
        assert check_eligibility(on_boundary, NOW, POLICY, cfg).eligible is False
        # One second past the window: stalled.
        past_boundary = make_lead(
            created_at=(NOW - timedelta(days=60)).isoformat(),
            last_contact_at=(
                NOW - timedelta(days=POLICY.stall_days, seconds=1)
            ).isoformat(),
        )
        assert check_eligibility(past_boundary, NOW, POLICY, cfg).eligible is True


# ---------------------------------------------------------------------------
# Detection context sanity for the drift classification reused above
# ---------------------------------------------------------------------------


class TestDriftEvidenceContext:
    def test_context_requires_positive_evidence(self):
        from app.modules.recovery_agent.detect import detect_signals

        lead = make_lead(created_at=FRESH.isoformat(), external_crm_id=None)
        ctx = DetectionContext(
            crm_configured=True,
            crm_synced_lead_ids=frozenset({"lead_engother0000001"}),
        )
        signals = detect_signals(lead, NOW, POLICY, ctx)
        assert [s["signal"] for s in signals] == []

    def test_confirmed_sync_without_crm_connection_is_not_drift(self):
        from app.modules.recovery_agent.detect import detect_signals

        lead = make_lead(created_at=FRESH.isoformat(), external_crm_id=None)
        ctx = DetectionContext(
            crm_configured=False,
            crm_synced_lead_ids=frozenset({"lead_eng000000000001"}),
        )
        signals = detect_signals(lead, NOW, POLICY, ctx)
        assert SIGNAL_CRM_SYNC_DRIFT not in [s["signal"] for s in signals]

    def test_never_contacted_still_fires_for_backdated_lead(self):
        from app.modules.recovery_agent.detect import detect_signals

        lead = make_lead(created_at=BACKDATED.isoformat())
        signals = detect_signals(lead, NOW, POLICY, DetectionContext())
        assert SIGNAL_NEVER_CONTACTED in [s["signal"] for s in signals]
