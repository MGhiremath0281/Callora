"""Repository for recovery cases (Module 3 hand-off surface).

Cases are idempotent per (business_id, lead_id): re-running the sweep
updates the recommendation in place instead of duplicating it, and a
lifecycle status that already advanced (e.g. ``in_recovery``) is never
regressed by a later sweep.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import RecoveryCase, RecoveryCaseStatus


def get_case(session: Session, business_id: str, case_id: str) -> Optional[RecoveryCase]:
    return session.execute(
        select(RecoveryCase).where(
            RecoveryCase.business_id == business_id,
            RecoveryCase.id == case_id,
        )
    ).scalar_one_or_none()


def list_cases(
    session: Session,
    business_id: str,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[RecoveryCase]:
    stmt = select(RecoveryCase).where(RecoveryCase.business_id == business_id)
    if status is not None:
        stmt = stmt.where(RecoveryCase.status == status)
    stmt = stmt.order_by(RecoveryCase.score.desc()).limit(limit).offset(offset)
    return list(session.execute(stmt).scalars().all())


def count_attempts_in_progress(session: Session, business_id: str, lead_id: str) -> int:
    """Recovery attempts already underway for a lead.

    Only cases the ACT stage moved into ``in_recovery`` count as
    attempts; a recommendation alone is not an attempt (a re-run of the
    sweep must stay ``llm``/recommendation, not escalate to a human
    merely because the sweep ran twice).
    """
    stmt = select(func.count()).select_from(RecoveryCase).where(
        RecoveryCase.business_id == business_id,
        RecoveryCase.lead_id == lead_id,
        RecoveryCase.status == RecoveryCaseStatus.IN_RECOVERY,
    )
    return session.execute(stmt).scalar_one()


def upsert_case(
    session: Session,
    business_id: str,
    lead: Dict[str, Any],
    score: float,
    signals: List[Dict[str, str]],
    route: str,
    plan: Dict[str, Any],
    now: datetime,
) -> RecoveryCase:
    """Insert or update the recovery case for one lead."""
    lead_id = lead["lead_id"]
    existing = session.execute(
        select(RecoveryCase).where(
            RecoveryCase.business_id == business_id,
            RecoveryCase.lead_id == lead_id,
        )
    ).scalar_one_or_none()

    if existing is None:
        case = RecoveryCase(
            business_id=business_id,
            lead_id=lead_id,
            lead_name=lead.get("name"),
            score=score,
            signals=signals,
            route=route,
            action=plan.get("action"),
            channel=plan.get("channel"),
            message=plan.get("message", ""),
            rationale=plan.get("rationale", ""),
            engine=plan.get("engine", "rules-fallback"),
            needs_human_approval=bool(plan.get("needs_human_approval")),
            status=RecoveryCaseStatus.RECOMMENDED,
            created_at=now,
            updated_at=now,
        )
        session.add(case)
        return case

    # Refresh the recommendation; never regress an advanced lifecycle.
    existing.lead_name = lead.get("name")
    existing.score = score
    existing.signals = signals
    existing.route = route
    existing.action = plan.get("action")
    existing.channel = plan.get("channel")
    existing.message = plan.get("message", "")
    existing.rationale = plan.get("rationale", "")
    existing.engine = plan.get("engine", "rules-fallback")
    existing.needs_human_approval = bool(plan.get("needs_human_approval"))
    existing.updated_at = now
    return existing
