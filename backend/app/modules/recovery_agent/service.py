"""Module 3 pipeline orchestration — the recovery sweep.

    fetch → dedupe → existing-case guard → detect → eligibility →
    assess → guardrails/decide → store → prioritize (Module 5)

The engine is synchronous and takes the already-fetched leads, so it is
unit-testable without HTTP; the router performs the async fetch and runs
this in a worker thread. Every stage is deterministic except the
Strategist, which degrades to rules without an LLM key.

After the cases are stored, Module 5 (Priority Scoring) ranks the open
(``recommended``) cases of the business — deterministic, no LLM, and
strictly downstream of eligibility: it can never reintroduce a lead the
gate excluded.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    CRMConnection,
    CRMSyncRecord,
    CRMSyncStatus,
    EventStatus,
    OutboxEvent,
    RecoveryCase,
    RecoveryCaseStatus,
)
from app.modules.recovery_agent.assess import recoverability
from app.modules.recovery_agent.detect import (
    DetectionContext,
    RecoveryPolicy,
    detect_signals,
)
from app.modules.recovery_agent.eligibility import (
    REASON_ALREADY_RECOVERED,
    REASON_DUPLICATE_ENTRY,
    REASON_EXISTING_CASE,
    REASON_IGNORED_STATUS,
    REASON_INACTIVE_STATUS,
    check_eligibility,
)
from app.modules.recovery_agent.guardrails import hard_block, route
from app.modules.recovery_agent.policy import eligibility_config
from app.modules.recovery_agent.priority import PRIORITY_LEVELS, priority_config
from app.modules.recovery_agent.repository import RANKING_ORDER, rank_open_cases, upsert_case
from app.modules.recovery_agent.strategist import llm_available, strategize

logger = logging.getLogger("callora.recovery.sweep")


def build_detection_context(db: Session, business_id: str) -> DetectionContext:
    """External-system state for one business, read from real tables.

    - ``crm_configured``: an active CRM connection exists, so a missing
      ``external_crm_id`` really is sync drift;
    - ``failed_event_lead_ids``: leads whose ``lead.created`` outbox
      event is in a terminal failed state (Module 1 outbox table);
    - ``crm_synced_lead_ids``: leads the CRM module *confirmed* as pushed
      (a terminal ``created``/``updated`` sync record) — the positive
      evidence required to classify drift without guessing.
    """
    crm_configured = db.execute(
        select(CRMConnection.id).where(
            CRMConnection.business_id == business_id,
            CRMConnection.is_active.is_(True),
        )
    ).first() is not None

    failed_events = db.execute(
        select(OutboxEvent.lead_id).where(
            OutboxEvent.business_id == business_id,
            OutboxEvent.status == EventStatus.FAILED,
            OutboxEvent.lead_id.is_not(None),
        )
    ).all()
    failed_event_lead_ids = frozenset(row[0] for row in failed_events)

    synced_records = db.execute(
        select(CRMSyncRecord.lead_id).where(
            CRMSyncRecord.business_id == business_id,
            CRMSyncRecord.status.in_(
                (CRMSyncStatus.CREATED, CRMSyncStatus.UPDATED)
            ),
            CRMSyncRecord.lead_id.is_not(None),
        )
    ).all()
    crm_synced_lead_ids = frozenset(row[0] for row in synced_records)

    return DetectionContext(
        crm_configured=crm_configured,
        failed_event_lead_ids=failed_event_lead_ids,
        crm_synced_lead_ids=crm_synced_lead_ids,
    )


def _normalize_lead(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Accept the agent-leads contract (lead_id/external_crm_id) and the
    notebook prototype's aliases (id/external_id); reject entries with no
    usable lead identifier instead of crashing the sweep."""
    lead_id = raw.get("lead_id") or raw.get("id")
    if not lead_id or not isinstance(lead_id, str):
        return None
    lead = dict(raw)
    lead["lead_id"] = lead_id
    if not lead.get("external_crm_id"):
        lead["external_crm_id"] = raw.get("external_id")
    custom = lead.get("custom_fields")
    if not isinstance(custom, dict):
        lead["custom_fields"] = {}
    return lead


def run_recovery_engine(
    db: Session,
    business_id: str,
    leads: List[Dict[str, Any]],
    settings: Settings,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Run the full detection pipeline over fetched leads and persist cases.

    Stage order: **dedupe → existing-case guard → detect → eligibility
    gate → assess → guardrails/decide → store**. The gate is
    deterministic (no LLM, no scoring); the only stage that can touch an
    external service is recommendation generation (the Strategist), and
    it runs strictly after eligibility/score decisions are frozen.

    Two deliberate ordering choices:

    - *detect runs before the gate* — signals carry database-confirmed
      evidence (failed delivery, confirmed CRM drift) that the gate's
      evidence rule consumes, so a lead whose timestamps are fresh but
      whose CRM sync is provably drifted is still evaluated;
    - *the existing-case guard runs before the gate* — a case that
      already advanced past ``recommended`` (in recovery / recovered /
      exhausted / archived) belongs to the ACT/HITL lifecycle and is
      never re-opened, re-scored or overwritten by a sweep, whatever the
      payload says.
    """
    now = now or datetime.now(timezone.utc)
    policy = RecoveryPolicy.from_settings(settings)
    config = eligibility_config(settings)
    ctx = build_detection_context(db, business_id)
    existing_cases = _existing_case_statuses(db, business_id)

    summary: Dict[str, Any] = {
        "business_id": business_id,
        "fetched": len(leads),
        "evaluated": 0,
        "skipped_inactive": 0,
        "skipped_invalid": 0,
        "signals_detected": 0,
        "candidates": 0,
        "cases_upserted": 0,
        "engine": f"groq:{settings.groq_model}" if llm_available(settings) else "rules-fallback",
        "swept_at": now.isoformat(),
    }
    detected: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    for raw in leads:
        lead = _normalize_lead(raw)
        if lead is None:
            summary["skipped_invalid"] += 1
            continue

        # Intra-batch dedupe: the same lead twice in one payload is an
        # input error, not two opportunities (a second insert would also
        # collide with the unique (business, lead) constraint).
        if lead["lead_id"] in seen:
            summary["skipped_invalid"] += 1
            _log_exclusion(
                business_id,
                lead,
                REASON_DUPLICATE_ENTRY,
                "same lead_id appears more than once in the payload",
            )
            continue
        seen.add(lead["lead_id"])

        # A case that already advanced past "recommended" is excluded
        # before anything else — the row is left completely untouched.
        existing_status = existing_cases.get(lead["lead_id"])
        if existing_status is not None and existing_status != RecoveryCaseStatus.RECOMMENDED:
            summary["skipped_inactive"] += 1
            _log_exclusion(
                business_id,
                lead,
                REASON_EXISTING_CASE,
                f"recovery case already in status={existing_status}",
            )
            continue

        # Deterministic signal detection (pure function of the lead and
        # the pre-fetched external state — no I/O here).
        signals = detect_signals(lead, now, policy, ctx)

        # --- DETERMINISTIC ELIGIBILITY GATE (no LLM, no scoring) --------
        # Pre-computed signals are passed in so the evidence rule sees
        # system-derived evidence (e.g. confirmed CRM drift) that the
        # lead's timestamps alone cannot express.
        decision = check_eligibility(lead, now, policy, config, signals=signals)
        if not decision.eligible:
            if decision.reason in (
                REASON_INACTIVE_STATUS,
                REASON_ALREADY_RECOVERED,
                REASON_IGNORED_STATUS,
            ):
                summary["skipped_inactive"] += 1
            else:
                summary["skipped_invalid"] += 1
            _log_exclusion(business_id, lead, decision.reason, decision.detail)
            continue

        summary["evaluated"] += 1
        if signals:
            detected.append({"lead": lead, "signals": signals})

    summary["signals_detected"] = sum(len(d["signals"]) for d in detected)

    # Triage: score each detected lead, keep those above the threshold.
    # Attempts are structurally zero here: any lead with an in-progress
    # (non-recommended) case was excluded above, so no recovery attempt
    # can be underway for a surviving candidate.
    candidates: List[Dict[str, Any]] = []
    for entry in detected:
        attempts = 0
        score = recoverability(entry["lead"], entry["signals"], attempts)
        if score >= policy.score_threshold:
            candidates.append(dict(entry, score=score, attempts=attempts))
    candidates.sort(key=lambda c: c["score"], reverse=True)
    summary["candidates"] = len(candidates)

    # Decide + store. Guardrails run before any LLM call.
    for candidate in candidates:
        lead = candidate["lead"]
        attempts = candidate["attempts"]
        # Guardrails and the *decision* are deterministic and
        # independent of any external service.
        block = hard_block(lead, now, policy, attempts)
        route_decision = route(lead, candidate["signals"], now, policy, attempts)
        try:
            # Recommendation generation is isolated: any failure (LLM
            # outage, malformed output) degrades to the rules fallback
            # and can never corrupt the eligibility/score decision above.
            plan = strategize(
                candidate,
                attempts,
                block,
                settings,
                policy,
                use_llm=(route_decision in ("llm", "hitl")),
            )
        except Exception:  # noqa: BLE001 - one bad candidate must not lose the sweep
            logger.exception(
                "recovery_plan_failed",
                extra={"business_id": business_id, "lead_id": lead.get("lead_id")},
            )
            continue
        try:
            upsert_case(
                db,
                business_id,
                lead,
                score=candidate["score"],
                signals=candidate["signals"],
                route=route_decision,
                plan=plan,
                now=now,
            )
        except Exception:  # noqa: BLE001 - one bad candidate must not lose the sweep
            logger.exception(
                "recovery_case_upsert_failed",
                extra={"business_id": business_id, "lead_id": lead.get("lead_id")},
            )
            # A failed flush can take earlier pending inserts down with
            # it — roll back and let the final recount report only what
            # really survives.
            _rollback_safely(db)

    # ``db.commit()`` — the same call the sweep has always made, kept
    # here so a commit failure is reported (never silently swallowed).

    # --- MODULE 5: PRIORITY SCORING (deterministic, no LLM) ------------
    # Rank the open (recommended) cases of this business. Runs inside
    # the same transaction so cases and ranks commit together; ranking
    # consumes only cases that already passed the eligibility gate and
    # can never resurrect an excluded lead. A scoring failure is
    # reported (``scored: 0``), never silently swallowed — and never
    # takes the sweep result down with it.
    try:
        priority_result = rank_open_cases(
            db,
            business_id,
            policy,
            priority_config(settings),
            now,
            gate_config=config,
        )
    except Exception:  # noqa: BLE001 - scoring must not lose the sweep
        logger.exception(
            "priority_scoring_failed",
            extra={"business_id": business_id},
        )
        priority_result = _empty_priority_result()

    committed = _commit_safely(db)
    # Truthful recount: the unique-violation fallback inside
    # ``upsert_case`` rolls back the whole transaction, so a persisted
    # count kept in a local variable can overstate reality. The number
    # reported is always the number of candidate leads with a case row.
    summary["cases_upserted"] = (
        _count_persisted_cases(db, business_id, candidates) if committed else 0
    )
    # The ranking is only real if the transaction that carried it
    # committed — same honesty rule as ``cases_upserted``.
    summary["priority"] = priority_result if committed else _empty_priority_result()

    logger.info(
        "recovery_sweep_completed",
        extra={"business_id": business_id, **{k: summary[k] for k in
               ("fetched", "evaluated", "candidates", "cases_upserted")}},
    )
    return summary


def _existing_case_statuses(db: Session, business_id: str) -> Dict[str, str]:
    """Status of every recovery case the business already has, in one query.

    Used by the existing-case guard: a lead whose case advanced past
    ``recommended`` is excluded from the sweep entirely (row untouched).
    """
    rows = db.execute(
        select(RecoveryCase.lead_id, RecoveryCase.status).where(
            RecoveryCase.business_id == business_id
        )
    ).all()
    return {row[0]: row[1] for row in rows}


def _log_exclusion(
    business_id: str, lead: Dict[str, Any], reason: str, detail: str
) -> None:
    """Structured, PII-free exclusion log (no phone/email/message bodies)."""
    logger.info(
        "recovery_lead_excluded",
        extra={
            "business_id": business_id,
            "lead_id": lead.get("lead_id") or lead.get("id"),
            "reason": reason,
            "detail": detail,
        },
    )


def _empty_priority_result() -> Dict[str, Any]:
    """Honest zero-state when ranking did not persist (failure/rollback)."""
    return {
        "scored": 0,
        "deranked_ineligible": 0,
        "levels": {level: 0 for level in PRIORITY_LEVELS},
        "order": RANKING_ORDER,
        "scored_at": None,
    }


def _rollback_safely(db: Session) -> None:
    try:
        db.rollback()
    except Exception:  # noqa: BLE001 - reporting must not mask the original error
        logger.exception("recovery_sweep_rollback_failed")


def _commit_safely(db: Session) -> bool:
    """Commit, reporting whether it actually succeeded.

    Returns ``True`` only when the transaction committed; ``False`` when
    the commit failed (in which case the session is rolled back and the
    caller must not claim persistence).
    """
    try:
        db.commit()
        return True
    except Exception:  # noqa: BLE001
        logger.exception("recovery_sweep_commit_failed")
        _rollback_safely(db)
        return False


def _count_persisted_cases(
    db: Session, business_id: str, candidates: List[Dict[str, Any]]
) -> int:
    """Count candidate leads that now have a persisted case row."""
    lead_ids = [c["lead"]["lead_id"] for c in candidates]
    if not lead_ids:
        return 0
    from app.models import RecoveryCase

    rows = db.execute(
        select(RecoveryCase.lead_id).where(
            RecoveryCase.business_id == business_id,
            RecoveryCase.lead_id.in_(lead_ids),
        )
    ).all()
    return len(rows)
