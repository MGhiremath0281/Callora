"""Tests for the deterministic ELIGIBILITY engine (Module 3).

The gate must be:
- deterministic (same inputs -> same decision, no LLM involved),
- conservative (missing/unknown data is never eligibility evidence),
- auditable (every exclusion carries an explicit reason),
- independent of external services and the database.

Coverage maps to the ticket's required cases: eligible opportunities,
inactive leads, invalid contact info, never-contacted, overdue follow-up,
confirmed CRM drift, unknown CRM status, prior outcomes, consent
restrictions, accurate counts, duplicate prevention, id/timestamp
preservation, business isolation, API contracts and failures.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from tests.conftest import BIZ_1, BIZ_2, auth_headers

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
    detect_signals,
)
from app.modules.recovery_agent.eligibility import (
    REASON_ALREADY_RECOVERED,
    REASON_IGNORED_STATUS,
    REASON_INACTIVE_STATUS,
    REASON_MISSING_IDENTIFIER,
    REASON_NO_CONTACT_CHANNEL,
    REASON_NO_RECOVERY_EVIDENCE,
    REASON_OPTED_OUT,
    check_eligibility,
    is_eligible,
)
from app.modules.recovery_agent.policy import eligibility_config
from app.modules.recovery_agent.service import run_recovery_engine

NOW = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)
POLICY = RecoveryPolicy()
BACKDATED = datetime.now(timezone.utc) - timedelta(days=30)


def make_lead(**overrides):
    lead = {
        "lead_id": "lead_000000000000001",
        "business_id": BIZ_1,
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
    }
    lead.update(overrides)
    return lead


@pytest.fixture()
def cfg(settings):
    return eligibility_config(settings)


# ---------------------------------------------------------------------------
# 1. Eligible opportunities + determinism
# ---------------------------------------------------------------------------


class TestEligibleOpportunities:
    def test_neglected_lead_is_eligible(self, cfg):
        decision = check_eligibility(make_lead(), NOW, POLICY, cfg)
        assert decision.eligible is True
        assert decision.reason is None
        assert decision.contact_channels == ["phone"]
        assert bool(decision) is True

    def test_decision_is_deterministic(self, cfg):
        lead = make_lead()
        decisions = {check_eligibility(lead, NOW, POLICY, cfg).eligible for _ in range(25)}
        assert decisions == {True}

    def test_gate_does_not_mutate_input(self, cfg):
        lead = make_lead()
        snapshot = dict(lead)
        check_eligibility(lead, NOW, POLICY, cfg)
        assert lead == snapshot

    def test_eligible_when_explicitly_opted_in_via_false_flag(self, cfg):
        # An explicitly ``False`` opt-out flag is NOT treated as opted out.
        lead = make_lead(custom_fields={"opt_out": False, "do_not_contact": "false"})
        assert is_eligible(lead, NOW, POLICY, cfg) is True


# ---------------------------------------------------------------------------
# 2. Inactive / lifecycle leads
# ---------------------------------------------------------------------------


class TestInactiveLeads:
    @pytest.mark.parametrize(
        "status", ["Contacted", "qualified", "CONVERTED", "Closed Lost", "archived"]
    )
    def test_active_or_converted_are_excluded(self, cfg, status):
        decision = check_eligibility(make_lead(status=status), NOW, POLICY, cfg)
        assert decision.eligible is False
        assert decision.reason == REASON_INACTIVE_STATUS

    def test_unknown_status_degrades_toward_detection(self, cfg):
        # An unknown/missing status is NOT automatic evidence of inactivity.
        assert is_eligible(make_lead(status=None), NOW, POLICY, cfg) is True
        assert is_eligible(make_lead(status="Brand New"), NOW, POLICY, cfg) is True


# ---------------------------------------------------------------------------
# 3. Contact validity
# ---------------------------------------------------------------------------


class TestContactValidity:
    @pytest.mark.parametrize("phone", [None, "", "   "])
    def test_missing_or_blank_phone_is_excluded(self, cfg, phone):
        decision = check_eligibility(make_lead(phone=phone), NOW, POLICY, cfg)
        assert decision.eligible is False
        assert decision.reason == REASON_NO_CONTACT_CHANNEL

    def test_phone_only_is_sufficient(self, cfg):
        lead = make_lead(phone="9876543210", email=None)
        assert is_eligible(lead, NOW, POLICY, cfg) is True

    def test_email_only_is_not_sufficient_by_default(self, cfg):
        # The configured usable channel is ``phone``; an email alone cannot
        # satisfy the proposed recovery action.
        lead = make_lead(phone="", email="someone@example.com")
        decision = check_eligibility(lead, NOW, POLICY, cfg)
        assert decision.eligible is False
        assert decision.reason == REASON_NO_CONTACT_CHANNEL


# ---------------------------------------------------------------------------
# 4/5. Follow-up timing (never contacted / overdue)
# ---------------------------------------------------------------------------


class TestFollowUpTiming:
    def test_never_contacted_past_sla_is_eligible(self, cfg):
        lead = make_lead(last_contact_at=None, created_at="2026-10-18T10:00:00+00:00")
        # 23h since creation, SLA default 120 min → eligible.
        assert is_eligible(lead, NOW, POLICY, cfg) is True

    def test_never_contacted_within_sla_is_not_eligible(self, cfg):
        fresh = make_lead(last_contact_at=None, created_at="2026-10-19T08:30:00+00:00")
        decision = check_eligibility(fresh, NOW, POLICY, cfg)
        assert decision.eligible is False
        assert decision.reason == REASON_NO_RECOVERY_EVIDENCE

    def test_overdue_follow_up_is_eligible(self, cfg):
        lead = make_lead(last_contact_at="2026-10-01T10:00:00+00:00")
        assert is_eligible(lead, NOW, POLICY, cfg) is True

    def test_recently_contacted_is_not_eligible(self, cfg):
        lead = make_lead(last_contact_at="2026-10-18T10:00:00+00:00")  # ~23h
        decision = check_eligibility(lead, NOW, POLICY, cfg)
        assert decision.eligible is False
        assert decision.reason == REASON_NO_RECOVERY_EVIDENCE

    def test_unknown_creation_timestamp_is_not_evidence(self, cfg):
        # Missing data must never be read as eligibility.
        lead = make_lead(created_at=None, last_contact_at=None)
        assert check_eligibility(lead, NOW, POLICY, cfg).reason == REASON_NO_RECOVERY_EVIDENCE

    def test_unparseable_timestamp_is_not_evidence(self, cfg):
        lead = make_lead(created_at="not-a-date", last_contact_at=None)
        assert check_eligibility(lead, NOW, POLICY, cfg).reason == REASON_NO_RECOVERY_EVIDENCE


# ---------------------------------------------------------------------------
# 6/7. CRM sync drift vs unknown status
# ---------------------------------------------------------------------------


class TestCRMSyncEvidence:
    def test_drift_requires_configured_crm(self):
        lead = make_lead(external_crm_id=None)
        signals = detect_signals(lead, NOW, POLICY, DetectionContext(crm_configured=False))
        assert "crm_sync_drift" not in [s["signal"] for s in signals]

    def test_confirmed_synced_lead_losing_id_is_drift(self):
        lead = make_lead(lead_id="lead_synced", external_crm_id=None)
        ctx = DetectionContext(
            crm_configured=True,
            crm_synced_lead_ids=frozenset({"lead_synced"}),
        )
        signals = detect_signals(lead, NOW, POLICY, ctx)
        assert "crm_sync_drift" in [s["signal"] for s in signals]

    def test_unknown_crm_status_is_not_drift(self):
        # CRM configured, but this lead was never confirmed synced → the
        # missing external id is unknown, not drift.
        lead = make_lead(lead_id="lead_unknown", external_crm_id=None)
        ctx = DetectionContext(
            crm_configured=True,
            crm_synced_lead_ids=frozenset({"lead_other"}),
        )
        signals = detect_signals(lead, NOW, POLICY, ctx)
        assert "crm_sync_drift" not in [s["signal"] for s in signals]

    def test_synced_lead_with_external_id_is_healthy(self):
        # A confirmed-synced lead with a live external id produces no drift
        # signal; the never-contacted signal is the only one expected.
        lead = make_lead(external_crm_id="CRM-1")
        ctx = DetectionContext(
            crm_configured=True, crm_synced_lead_ids=frozenset({"lead_000000000000001"})
        )
        signals = [s["signal"] for s in detect_signals(lead, NOW, POLICY, ctx)]
        assert "crm_sync_drift" not in signals
        assert signals == ["never_contacted"]

    def test_legacy_behaviour_without_confirmation_list(self):
        # No positive evidence available → preserve the historical gate.
        lead = make_lead(external_crm_id=None)
        ctx = DetectionContext(crm_configured=True, crm_synced_lead_ids=frozenset())
        signals = detect_signals(lead, NOW, POLICY, ctx)
        assert "crm_sync_drift" in [s["signal"] for s in signals]


# ---------------------------------------------------------------------------
# 8. Previous outcomes
# ---------------------------------------------------------------------------


class TestPreviousOutcomes:
    def test_recovered_status_is_excluded(self, cfg):
        decision = check_eligibility(make_lead(status="Recovered"), NOW, POLICY, cfg)
        assert decision.eligible is False
        assert decision.reason == REASON_ALREADY_RECOVERED

    def test_recovered_flag_is_excluded(self, cfg):
        lead = make_lead(custom_fields={"is_recovered": True})
        decision = check_eligibility(lead, NOW, POLICY, cfg)
        assert decision.eligible is False
        assert decision.reason == REASON_ALREADY_RECOVERED

    @pytest.mark.parametrize("status", ["Dismissed", "Suppressed", "Completed", "Resolved"])
    def test_operator_closed_states_are_ignored(self, cfg, status):
        decision = check_eligibility(make_lead(status=status), NOW, POLICY, cfg)
        assert decision.eligible is False
        assert decision.reason == REASON_IGNORED_STATUS

    def test_in_recovery_status_is_ignored_not_reopened(self, cfg):
        decision = check_eligibility(make_lead(status="in_recovery"), NOW, POLICY, cfg)
        assert decision.eligible is False
        assert decision.reason == REASON_IGNORED_STATUS

    def test_in_recovery_requires_explicit_status_evidence(self, cfg):
        # Absence of any status is unknown, not "in recovery".
        assert is_eligible(make_lead(status=None), NOW, POLICY, cfg) is True


# ---------------------------------------------------------------------------
# 9. Consent / contact restrictions
# ---------------------------------------------------------------------------


class TestConsentRestrictions:
    @pytest.mark.parametrize(
        "flag", ["opt_out", "opted_out", "do_not_contact", "dnc", "unsubscribed", "suppressed"]
    )
    def test_configured_opt_out_flags_exclude(self, cfg, flag):
        lead = make_lead(custom_fields={flag: True})
        decision = check_eligibility(lead, NOW, POLICY, cfg)
        assert decision.eligible is False
        assert decision.reason == REASON_OPTED_OUT

    def test_status_level_opt_out_excludes(self, cfg):
        decision = check_eligibility(make_lead(status="Opted Out"), NOW, POLICY, cfg)
        assert decision.eligible is False
        assert decision.reason == REASON_INACTIVE_STATUS

    def test_string_truthy_flags_are_honoured(self, cfg):
        assert not is_eligible(make_lead(custom_fields={"do_not_contact": "true"}), NOW, POLICY, cfg)

    def test_non_boolean_flag_values_are_not_guessed(self, cfg):
        # ``"maybe"`` is unknown — not consent, and not proof of opt-out.
        assert is_eligible(make_lead(custom_fields={"do_not_contact": "maybe"}), NOW, POLICY, cfg)


# ---------------------------------------------------------------------------
# Missing identifier
# ---------------------------------------------------------------------------


class TestMissingIdentifier:
    def test_no_identifier_is_excluded(self, cfg):
        lead = make_lead()
        del lead["lead_id"]
        decision = check_eligibility(lead, NOW, POLICY, cfg)
        assert decision.eligible is False
        assert decision.reason == REASON_MISSING_IDENTIFIER

    def test_prototype_alias_id_is_accepted(self, cfg):
        lead = make_lead()
        del lead["lead_id"]
        lead["id"] = "lead_alias000000001"
        assert is_eligible(lead, NOW, POLICY, cfg) is True


# ---------------------------------------------------------------------------
# Counts and candidate selection end-to-end
# ---------------------------------------------------------------------------


def seed_lead(db, lead_id="lead_elig0000000001", **overrides):
    fields = {
        "lead_id": lead_id,
        "business_id": BIZ_1,
        "name": "Eligible Lead",
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


class TestEligibilityCounts:
    def test_counts_reflect_eligibility(self, client, db):
        seed_lead(db)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        assert body["fetched"] == 1
        assert body["evaluated"] == 1
        assert body["candidates"] == 1
        assert body["cases_upserted"] == 1

    def test_invalid_contact_counted_as_skipped(self, client, db):
        seed_lead(db, lead_id="lead_nophone0000001", phone="")
        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        assert body["fetched"] == 1
        assert body["evaluated"] == 0
        assert body["skipped_invalid"] == 1
        assert body["candidates"] == 0
        assert body["cases_upserted"] == 0

    def test_inactive_counted_as_skipped_inactive(self, client, db):
        seed_lead(db, lead_id="lead_active00000001", status="Contacted")
        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        assert body["skipped_inactive"] == 1
        assert body["evaluated"] == 0

    def test_recovered_counted_as_skipped_inactive(self, client, db):
        seed_lead(db, lead_id="lead_recovered000001", status="Recovered")
        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        assert body["skipped_inactive"] == 1
        assert body["cases_upserted"] == 0

    def test_candidate_counts_match_persisted_cases(self, client, db):
        seed_lead(db, lead_id="lead_good00000000001")
        seed_lead(db, lead_id="lead_badcontact00001", phone="")
        seed_lead(db, lead_id="lead_inactive000001", status="Converted")
        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        assert body["fetched"] == 3
        assert body["candidates"] == 1
        assert body["cases_upserted"] == 1
        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
        assert len(cases["items"]) == 1

    def test_ineligible_leads_never_become_candidates(self, client, db):
        # Even with a strong prior recommendation tempting the engine, an
        # ineligible lead must not be turned into a candidate.
        seed_lead(db, lead_id="lead_nophone0000002", phone="")
        seed_lead(db, lead_id="lead_optedout000002", custom_fields={"do_not_contact": True})
        seed_lead(db, lead_id="lead_recovered000002", status="Recovered")
        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        assert body["candidates"] == 0
        assert body["cases_upserted"] == 0
        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
        assert cases["items"] == []


# ---------------------------------------------------------------------------
# CRM drift detection end-to-end
# ---------------------------------------------------------------------------


class TestCRMDriftEndToEnd:
    def _seed_connection(self, db):
        db.add(
            CRMConnection(
                business_id=BIZ_1,
                connection_id="elig-conn",
                name="Elig CRM",
                is_active=True,
                crm_base_url="http://localhost:9000",
                crm_api_key="k",
                crm_timeout_seconds=5.0,
            )
        )
        db.commit()

    def _seed_sync(self, db, lead_id, status):
        db.add(
            CRMSyncRecord(
                business_id=BIZ_1,
                lead_id=lead_id,
                connection_id="elig-conn",
                status=status,
                external_crm_id="CRM-1" if status != CRMSyncStatus.FAILED else None,
            )
        )
        db.commit()

    def test_unknown_crm_status_does_not_count_as_drift(self, client, db):
        self._seed_connection(db)
        # A different lead was confirmed synced; this lead's status is unknown.
        self._seed_sync(db, "lead_other0000000001", CRMSyncStatus.CREATED)
        seed_lead(db, lead_id="lead_unknown00000001", external_crm_id=None)
        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
        drift_signals = [
            s for c in cases["items"] for s in c["signals"] if s["signal"] == "crm_sync_drift"
        ]
        assert drift_signals == []
        assert body["cases_upserted"] == 1  # still eligible via never_contacted

    def test_confirmed_drift_is_detected_end_to_end(self, client, db):
        self._seed_connection(db)
        seed_lead(db, lead_id="lead_drift000000001", external_crm_id=None)
        self._seed_sync(db, "lead_drift000000001", CRMSyncStatus.CREATED)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
        signals = {s["signal"] for c in cases["items"] for s in c["signals"]}
        assert "crm_sync_drift" in signals

    def test_failed_sync_is_not_confirmed_drift_evidence(self, client, db):
        self._seed_connection(db)
        seed_lead(db, lead_id="lead_failedsync00001", external_crm_id=None)
        # The only sync attempt failed, so drift cannot be confirmed; the
        # legacy "drift in scope" signal still fires for configured CRMs.
        self._seed_sync(db, "lead_failedsync00001", CRMSyncStatus.FAILED)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
        # Sanity: a case exists, driven by the never-contacted signal.
        assert len(cases["items"]) == 1


# ---------------------------------------------------------------------------
# 11/12. Duplicate prevention + identifier/timestamp preservation
# ---------------------------------------------------------------------------


class TestCasePersistence:
    def test_reruns_do_not_duplicate_or_recreate(self, client, db):
        seed_lead(db)
        for _ in range(4):
            client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
        assert len(cases["items"]) == 1

    def test_case_id_and_created_at_are_preserved(self, client, db):
        seed_lead(db)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        first = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"][0]

        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        second = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"][0]

        assert first["case_id"] == second["case_id"]
        assert first["created_at"] == second["created_at"]
        assert second["updated_at"] >= second["created_at"]

    def test_completed_case_is_not_reopened(self, client, db):
        seed_lead(db)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        case_id = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"][0]["case_id"]

        case = db.get(RecoveryCase, case_id)
        case.status = RecoveryCaseStatus.RECOVERED
        db.commit()

        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        refreshed = client.get(f"/api/v1/agent/recovery/cases/{case_id}", headers=auth_headers()).json()
        assert refreshed["status"] == RecoveryCaseStatus.RECOVERED

    def test_dismissed_case_is_not_reopened_or_resurrected(self, client, db):
        seed_lead(db)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        case_id = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()["items"][0]["case_id"]

        case = db.get(RecoveryCase, case_id)
        case.status = RecoveryCaseStatus.ARCHIVED
        db.commit()

        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
        assert len(cases["items"]) == 1
        assert cases["items"][0]["status"] == RecoveryCaseStatus.ARCHIVED

    def test_unique_constraint_prevents_duplicate_rows(self, client, db):
        seed_lead(db)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        from sqlalchemy import func, select

        count = db.execute(
            select(func.count()).select_from(RecoveryCase).where(
                RecoveryCase.business_id == BIZ_1
            )
        ).scalar_one()
        assert count == 1

    def test_upsert_recovers_from_constraint_race(self, db, monkeypatch):
        """A losing insert (UNIQUE violation) updates the winner instead."""
        from app.modules.recovery_agent import repository

        lead = {"lead_id": "lead_race00000000001", "name": "Race"}
        plan = {"action": "sync_to_crm", "channel": None, "message": "",
                "rationale": "r", "engine": "rules-fallback"}
        signals = [{"signal": "never_contacted", "severity": "high", "detail": ""}]

        # Seed the "concurrent winner" row up-front.
        winner = repository.upsert_case(db, BIZ_1, lead, 0.5, signals, "llm", plan, NOW)
        db.commit()
        winner_id = winner.id

        order = []

        def fake_load(session, business_id, lead_id):
            # First call (pre-insert lookup) sees nothing; the lookup after
            # the IntegrityError sees the row the concurrent writer inserted.
            order.append("load")
            if order.count("load") == 1:
                return None
            return repository.get_case_by_lead(db, business_id, lead_id)

        monkeypatch.setattr(repository, "_load_existing_case", fake_load)
        monkeypatch.setattr(db, "flush", lambda: (_ for _ in ()).throw(IntegrityError("s", {}, Exception("u"))))
        monkeypatch.setattr(db, "rollback", lambda: None)

        result = repository.upsert_case(db, BIZ_1, lead, 0.9, signals, "llm", plan, NOW)
        assert result.id == winner_id  # winner reused, not duplicated

    def test_concurrent_writers_do_not_duplicate_rows(self, settings):
        """Six racing sweeps converge on exactly one persisted case row."""
        import threading

        from app.db.session import get_session_factory
        from app.modules.recovery_agent.service import run_recovery_engine

        db = get_session_factory()()
        upsert_agent_lead(
            db, lead_id="lead_conc00000000001", business_id=BIZ_1, name="Conc",
            phone="9876543210", source="web", created_at=BACKDATED,
        )
        db.commit()
        db.close()

        leads = [{
            "lead_id": "lead_conc00000000001", "name": "Conc", "phone": "9876543210",
            "status": "New", "created_at": BACKDATED.isoformat(), "custom_fields": {},
        }]
        errors: list = []

        def worker():
            try:
                with get_session_factory()() as session:
                    run_recovery_engine(session, BIZ_1, leads, settings)
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        with get_session_factory()() as check:
            rows = check.query(RecoveryCase).filter_by(lead_id="lead_conc00000000001").all()
            assert len(rows) == 1


# ---------------------------------------------------------------------------
# 13. Business-level data isolation
# ---------------------------------------------------------------------------


class TestEligibilityIsolation:
    def test_sweep_scopes_drift_evidence_to_business(self, client, db):
        # BIZ_2 has a confirmed sync record; BIZ_1's identical lead must not
        # inherit that evidence.
        db.add(
            CRMSyncRecord(
                business_id=BIZ_2,
                lead_id="lead_isolation000001",
                connection_id="other-conn",
                status=CRMSyncStatus.CREATED,
                external_crm_id="CRM-X",
            )
        )
        db.commit()
        seed_lead(db, lead_id="lead_isolation000001", external_crm_id=None)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
        signals = {s["signal"] for c in cases["items"] for s in c["signals"]}
        assert "crm_sync_drift" not in signals

    def test_other_business_sees_no_cases(self, client, db):
        seed_lead(db)
        client.post("/api/v1/agent/recovery/run", headers=auth_headers())
        other = client.get("/api/v1/agent/recovery/cases", headers=auth_headers(BIZ_2)).json()
        assert other["items"] == []


# ---------------------------------------------------------------------------
# 14/15. API contract + failures
# ---------------------------------------------------------------------------


class TestAPIAndFailures:
    def test_run_response_contract_is_preserved(self, client, db):
        seed_lead(db)
        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        for key in (
            "business_id",
            "fetched",
            "evaluated",
            "skipped_inactive",
            "skipped_invalid",
            "signals_detected",
            "candidates",
            "cases_upserted",
            "engine",
            "swept_at",
        ):
            assert key in body, key

    def test_cases_response_contract_is_preserved(self, client, db):
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
        ):
            assert key in case, key

    def test_persistence_failure_is_reported_not_swallowed(self, client, db, monkeypatch):
        seed_lead(db)
        # Force the underlying commit to fail; the sweep must report the real,
        # unpersisted result instead of claiming success (and must not leak a
        # raw exception to the caller).
        from sqlalchemy.orm import Session as _Session

        def _raise(self):
            raise RuntimeError("db down")

        monkeypatch.setattr(_Session, "commit", _raise)
        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        assert body["candidates"] == 1
        assert body["cases_upserted"] == 0
        cases = client.get("/api/v1/agent/recovery/cases", headers=auth_headers()).json()
        assert cases["items"] == []

    def test_external_service_failure_does_not_corrupt_eligibility(
        self, client, db, monkeypatch
    ):
        # The LLM is the only external call inside the sweep; force it to
        # explode and confirm eligibility/score decisions still stand.
        import app.modules.recovery_agent.service as service

        def boom(*args, **kwargs):
            raise RuntimeError("llm transport down")

        monkeypatch.setattr(service, "strategize", boom)
        seed_lead(db)
        body = client.post("/api/v1/agent/recovery/run", headers=auth_headers()).json()
        # Candidate was eligible; upsert failed -> reported honestly.
        assert body["candidates"] == 1
        assert body["cases_upserted"] == 0
