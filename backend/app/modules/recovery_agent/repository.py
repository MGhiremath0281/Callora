"""Repository for recovery cases (Module 3 hand-off surface).

Cases are idempotent per (business_id, lead_id): re-running the sweep
updates the recommendation in place instead of duplicating it, and a
lifecycle status that already advanced (e.g. ``in_recovery``) is never
regressed by a later sweep.

Module 5 (Priority Scoring) ranks the *open* (``recommended``) cases
through :func:`rank_open_cases` — see ``priority.py`` for the formula.
Ranking is scoped to one business and tie-broken deterministically
(priority score desc, recoverability score desc, case id asc) so the
same data always produces the same order.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AgentLead, RecoveryCase, RecoveryCaseStatus
from app.modules.recovery_agent.detect import RecoveryPolicy
from app.modules.recovery_agent.eligibility import check_eligibility
from app.modules.recovery_agent.priority import (
    PRIORITY_LEVELS,
    PriorityConfig,
    score_priority,
)

# Human-readable description of the ranking order, surfaced in API
# metadata so consumers never have to guess how ranks were assigned.
RANKING_ORDER = "priority_score desc, score desc, case_id asc"


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
    priority_level: Optional[str] = None,
    query: Optional[str] = None,
    needs_human_approval: Optional[bool] = None,
) -> List[RecoveryCase]:
    """List cases in priority order.

    Ordered by priority score (desc, unscored rows last), then the
    existing recoverability score (desc) and finally the case id (asc)
    as a stable tie-break, so pagination is deterministic for a fixed
    dataset.

    Optional dashboard filters (all backward compatible — ``None``
    reproduces the original behaviour exactly):

    ``priority_level``        only cases at that Module 5 level;
    ``query``                 case-insensitive substring match on lead
                              name, lead id or case id (dashboard search);
    ``needs_human_approval``  only cases flagged for human approval.
    """
    stmt = _apply_case_filters(
        select(RecoveryCase).where(RecoveryCase.business_id == business_id),
        status,
        priority_level,
        query,
        needs_human_approval,
    )
    stmt = stmt.order_by(
        RecoveryCase.priority_score.desc().nulls_last(),
        RecoveryCase.score.desc(),
        RecoveryCase.id.asc(),
    ).limit(limit).offset(offset)
    return list(session.execute(stmt).scalars().all())


def count_cases(
    session: Session,
    business_id: str,
    status: Optional[str] = None,
    priority_level: Optional[str] = None,
    query: Optional[str] = None,
    needs_human_approval: Optional[bool] = None,
) -> int:
    """Total cases matching the filters (pagination-independent)."""
    stmt = _apply_case_filters(
        select(func.count()).select_from(RecoveryCase).where(
            RecoveryCase.business_id == business_id
        ),
        status,
        priority_level,
        query,
        needs_human_approval,
    )
    return session.execute(stmt).scalar_one()


def _apply_case_filters(
    stmt,
    status: Optional[str],
    priority_level: Optional[str],
    query: Optional[str],
    needs_human_approval: Optional[bool],
):
    """Apply the optional dashboard filters to a SELECT statement."""
    if status is not None:
        stmt = stmt.where(RecoveryCase.status == status)
    if priority_level is not None:
        stmt = stmt.where(RecoveryCase.priority_level == priority_level)
    if needs_human_approval is not None:
        stmt = stmt.where(RecoveryCase.needs_human_approval.is_(needs_human_approval))
    if query is not None and query.strip():
        needle = f"%{query.strip().lower()}%"
        stmt = stmt.where(
            func.or_(
                func.lower(RecoveryCase.lead_name).like(needle),
                func.lower(RecoveryCase.lead_id).like(needle),
                func.lower(RecoveryCase.id).like(needle),
            )
        )
    return stmt


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


def rank_open_cases(
    session: Session,
    business_id: str,
    policy: RecoveryPolicy,
    config: PriorityConfig,
    now: datetime,
    gate_config: Any = None,
) -> Dict[str, Any]:
    """Score and rank the open (``recommended``) cases of one business.

    Only ``recommended`` cases are ranked: a case that advanced into the
    ACT/HITL lifecycle (``in_recovery``/``recovered``/``exhausted``/
    ``archived``) is owned by that stage and is never re-scored or
    re-ranked here — same rule the sweep already applies.

    Eligibility is *re-verified* with Module 4's own gate (reused, never
    re-implemented): a case row only exists for a lead that passed the
    gate at detection time, but the lead may have become ineligible
    since (opted out, marked recovered, contact invalidated). Such a
    case drops out of the ranked set — any previously persisted priority
    fields are cleared — so a stale case can never appear in ranked
    results or the priority summary. A high priority score can never
    override the gate.

    Fresh lead data is read from ``agent_leads`` so a changed deal
    value, contact detail or intent flag is reflected on the next run.
    Cases are ordered deterministically (``RANKING_ORDER``); repeated
    runs with unchanged inputs reproduce identical scores and ranks.
    """
    from app.modules.recovery_agent.policy import eligibility_config as _default_gate

    if gate_config is None:
        gate_config = _default_gate()

    cases = list(
        session.execute(
            select(RecoveryCase).where(
                RecoveryCase.business_id == business_id,
                RecoveryCase.status == RecoveryCaseStatus.RECOMMENDED,
            )
        ).scalars().all()
    )
    leads = _load_lead_data(session, business_id, {c.lead_id for c in cases})

    scored: List[Dict[str, Any]] = []
    deranked = 0
    for case in cases:
        lead = leads.get(case.lead_id) or {}
        # Module 4 gate, re-verified against *current* lead data. The
        # persisted signals are passed in so the evidence rule sees the
        # same verified evidence the sweep admitted the case with.
        decision = check_eligibility(
            lead, now, policy, gate_config, signals=list(case.signals or [])
        )
        if not decision.eligible:
            _clear_priority(case)
            deranked += 1
            continue
        result = score_priority(lead, list(case.signals or []), policy, config, now)
        scored.append((case, result))

    # Deterministic order: priority score desc, recoverability score
    # desc, case id asc (stable tie-break for equal priorities).
    scored.sort(
        key=lambda item: (
            -item[1].score,
            -(float(item[0].score) if item[0].score is not None else 0.0),
            item[0].id,
        )
    )

    levels: Dict[str, int] = {level: 0 for level in PRIORITY_LEVELS}
    for rank, (case, result) in enumerate(scored, start=1):
        case.priority_score = result.score
        case.priority_rank = rank
        case.priority_level = result.level
        case.score_breakdown = result.as_breakdown(config)
        case.scored_at = now
        levels[result.level] += 1

    return {
        "scored": len(scored),
        "deranked_ineligible": deranked,
        "levels": levels,
        "order": RANKING_ORDER,
        "scored_at": now.isoformat(),
    }


def _clear_priority(case: RecoveryCase) -> None:
    """Drop any stale ranking from a case that is no longer eligible."""
    case.priority_score = None
    case.priority_rank = None
    case.priority_level = None
    case.score_breakdown = None
    case.scored_at = None


def _load_lead_data(
    session: Session, business_id: str, lead_ids
) -> Dict[str, Dict[str, Any]]:
    """Current canonical lead fields for the scored cases, one query.

    A case whose lead record has vanished scores with an empty lead —
    every factor degrades to zero points instead of guessing.
    """
    if not lead_ids:
        return {}
    rows = session.execute(
        select(AgentLead).where(
            AgentLead.business_id == business_id,
            AgentLead.lead_id.in_(lead_ids),
        )
    ).scalars().all()
    return {
        row.lead_id: {
            "lead_id": row.lead_id,
            "phone": row.phone,
            "email": row.email,
            "language": row.language,
            "deal_value": row.deal_value,
            "last_contact_at": row.last_contact_at,
            "custom_fields": row.custom_fields or {},
            "created_at": row.created_at,
            "status": row.status,
        }
        for row in rows
    }


def priority_summary(session: Session, business_id: str) -> Dict[str, Any]:
    """Dashboard-ready metadata over ALL ranked cases of one business.

    Counted from the database (not the current page) so pagination never
    distorts the totals; ``scored_at`` is the latest scoring run.
    """
    rows = session.execute(
        select(RecoveryCase.priority_level, func.count()).where(
            RecoveryCase.business_id == business_id,
            RecoveryCase.status == RecoveryCaseStatus.RECOMMENDED,
            RecoveryCase.priority_score.is_not(None),
        ).group_by(RecoveryCase.priority_level)
    ).all()
    levels = {level: 0 for level in PRIORITY_LEVELS}
    total = 0
    for level, count in rows:
        total += count
        if level in levels:
            levels[level] = count
    latest = session.execute(
        select(func.max(RecoveryCase.scored_at)).where(
            RecoveryCase.business_id == business_id,
            RecoveryCase.status == RecoveryCaseStatus.RECOMMENDED,
        )
    ).scalar_one()
    return {
        "total_scored": total,
        "levels": levels,
        "order": RANKING_ORDER,
        "scored_at": latest.isoformat() if latest else None,
    }
