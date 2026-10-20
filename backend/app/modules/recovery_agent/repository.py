"""Repository for recovery cases (Module 3 hand-off surface).

Cases are idempotent per (business_id, lead_id): re-running the sweep
updates the recommendation in place instead of duplicating it, and a
lifecycle status that already advanced (e.g. ``in_recovery``) is never
regressed by a later sweep.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
    """Insert or update the recovery case for one lead.

    Idempotency is enforced twice: the ``uq_recovery_cases_business_lead``
    constraint guarantees uniqueness even under concurrent runs, and the
    explicit row lock (``SELECT … FOR UPDATE`` where the dialect supports
    it) closes the read-then-insert race so the second writer *updates*
    instead of colliding. ``id``/``created_at`` are never rewritten, so a
    case identifier and creation timestamp stay stable across sweeps.
    """
    lead_id = lead["lead_id"]
    existing = _load_existing_case(session, business_id, lead_id)

    if existing is not None:
        # Refresh the recommendation; never regress an advanced lifecycle.
        _apply_recommendation(existing, lead, score, signals, route, plan, now)
        return existing

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
    try:
        session.flush()
    except IntegrityError:
        # A concurrent run inserted the same (business, lead) first. Fall
        # back to updating that row — repeated/concurrent runs must never
        # surface as an error or create a duplicate. Only this one insert
        # is discarded (the session is re-used after the failed flush is
        # undone by the savepoint-equivalent rollback below).
        session.rollback()
        winner = _load_existing_case(session, business_id, lead_id)
        if winner is None:  # pragma: no cover - constraint implies a row exists
            raise
        _apply_recommendation(winner, lead, score, signals, route, plan, now)
        return winner
    return case


def _load_existing_case(
    session: Session, business_id: str, lead_id: str
) -> Optional[RecoveryCase]:
    """Load the case for (business, lead), locking the row when supported.

    The lock only matters under genuine concurrency; ``FOR UPDATE`` is a
    no-op on SQLite (tests) and correctly serializes writers on
    PostgreSQL, so the same code path is safe for both.
    """
    stmt = select(RecoveryCase).where(
        RecoveryCase.business_id == business_id,
        RecoveryCase.lead_id == lead_id,
    )
    try:
        stmt = stmt.with_for_update()
        return session.execute(stmt).scalar_one_or_none()
    except Exception:  # noqa: BLE001 - dialect without row-level locking
        return session.execute(
            select(RecoveryCase).where(
                RecoveryCase.business_id == business_id,
                RecoveryCase.lead_id == lead_id,
            )
        ).scalar_one_or_none()


def _apply_recommendation(
    existing: RecoveryCase,
    lead: Dict[str, Any],
    score: float,
    signals: List[Dict[str, str]],
    route: str,
    plan: Dict[str, Any],
    now: datetime,
) -> None:
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


def get_case_by_lead(
    session: Session, business_id: str, lead_id: str
) -> Optional[RecoveryCase]:
    """Retrieve the case for a lead, scoped to the business."""
    return session.execute(
        select(RecoveryCase).where(
            RecoveryCase.business_id == business_id,
            RecoveryCase.lead_id == lead_id,
        )
    ).scalar_one_or_none()
