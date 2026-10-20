"""Module 3 pipeline orchestration — the recovery sweep.

    fetch → detect → assess → guardrails/decide → store

The engine is synchronous and takes the already-fetched leads, so it is
unit-testable without HTTP; the router performs the async fetch and runs
this in a worker thread. Every stage is deterministic except the
Strategist, which degrades to rules without an LLM key.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import CRMConnection, EventStatus, OutboxEvent
from app.modules.recovery_agent.assess import recoverability
from app.modules.recovery_agent.detect import (
    DetectionContext,
    RecoveryPolicy,
    detect_signals,
    is_candidate_status,
)
from app.modules.recovery_agent.guardrails import hard_block, route
from app.modules.recovery_agent.repository import (
    count_attempts_in_progress,
    upsert_case,
)
from app.modules.recovery_agent.strategist import llm_available, strategize

logger = logging.getLogger("callora.recovery.sweep")


def build_detection_context(db: Session, business_id: str) -> DetectionContext:
    """External-system state for one business, read from real tables.

    - ``crm_configured``: an active CRM connection exists, so a missing
      ``external_crm_id`` really is sync drift;
    - ``failed_event_lead_ids``: leads whose ``lead.created`` outbox
      event is in a terminal failed state (Module 1 outbox table).
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

    return DetectionContext(
        crm_configured=crm_configured,
        failed_event_lead_ids=failed_event_lead_ids,
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
    """Run the full detection pipeline over fetched leads and persist cases."""
    now = now or datetime.now(timezone.utc)
    policy = RecoveryPolicy.from_settings(settings)
    ctx = build_detection_context(db, business_id)

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

    for raw in leads:
        lead = _normalize_lead(raw)
        if lead is None:
            summary["skipped_invalid"] += 1
            continue
        if not is_candidate_status(lead.get("status")):
            # AC: active/converted leads are excluded from recovery.
            summary["skipped_inactive"] += 1
            continue
        summary["evaluated"] += 1
        signals = detect_signals(lead, now, policy, ctx)
        if signals:
            detected.append({"lead": lead, "signals": signals})

    summary["signals_detected"] = sum(len(d["signals"]) for d in detected)

    # Triage: score each detected lead, keep those above the threshold.
    candidates: List[Dict[str, Any]] = []
    for entry in detected:
        lead = entry["lead"]
        attempts = count_attempts_in_progress(db, business_id, lead["lead_id"])
        score = recoverability(lead, entry["signals"], attempts)
        if score >= policy.score_threshold:
            entry = dict(entry, score=score, attempts=attempts)
            candidates.append(entry)
    candidates.sort(key=lambda c: c["score"], reverse=True)
    summary["candidates"] = len(candidates)

    # Decide + store. Guardrails run before any LLM call.
    for candidate in candidates:
        lead = candidate["lead"]
        attempts = candidate["attempts"]
        block = hard_block(lead, now, policy, attempts)
        route_decision = route(lead, candidate["signals"], now, policy, attempts)
        plan = strategize(
            candidate,
            attempts,
            block,
            settings,
            policy,
            use_llm=(route_decision in ("llm", "hitl")),
        )
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
        summary["cases_upserted"] += 1

    db.commit()
    logger.info(
        "recovery_sweep_completed",
        extra={"business_id": business_id, **{k: summary[k] for k in
               ("fetched", "evaluated", "candidates", "cases_upserted")}},
    )
    return summary
