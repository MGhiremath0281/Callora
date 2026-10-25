"""Repository for recovery dashboard data (cases, leads, outreach content/history).

All queries are business-scoped and never fabricate missing lead/case
data. Outcoms are only derived from persisted ``recovery_outreach_events``
(``content_generated`` plus any future execution events).
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AgentLead,
    EXECUTION_EVENT_TYPES,
    RecoveryCase,
    RecoveryCaseStatus,
    RecoveryOutreachContent,
    RecoveryOutreachEvent,
    prefixed_id,
)


def get_dashboard_case(session: Session, business_id: str, case_id: str) -> Optional[RecoveryCase]:
    return session.execute(
        select(RecoveryCase).where(
            RecoveryCase.business_id == business_id,
            RecoveryCase.id == case_id,
        )
    ).scalar_one_or_none()


def get_lead(session: Session, business_id: str, lead_id: str) -> Optional[AgentLead]:
    return session.execute(
        select(AgentLead).where(
            AgentLead.business_id == business_id,
            AgentLead.lead_id == lead_id,
        )
    ).scalar_one_or_none()


def get_outreach_content(
    session: Session, business_id: str, case_id: str
) -> Optional[RecoveryOutreachContent]:
    return session.execute(
        select(RecoveryOutreachContent).where(
            RecoveryOutreachContent.business_id == business_id,
            RecoveryOutreachContent.case_id == case_id,
        )
    ).scalar_one_or_none()


def upsert_outreach_content(
    session: Session,
    business_id: str,
    case_id: str,
    lead_id: str,
    content: Dict[str, Any],
    language: Optional[str],
    engine: str,
    model: Optional[str],
) -> RecoveryOutreachContent:
    existing = get_outreach_content(session, business_id, case_id)
    now = datetime.now(timezone.utc)
    if existing is not None:
        existing.content = content
        existing.language = language
        existing.engine = engine
        existing.model = model
        existing.generation_count = (existing.generation_count or 0) + 1
        existing.regenerated_at = now
        existing.updated_at = now
        session.add(existing)
        session.flush()
        return existing

    row = RecoveryOutreachContent(
        business_id=business_id,
        case_id=case_id,
        lead_id=lead_id,
        content=content,
        language=language,
        engine=engine,
        model=model,
        generation_count=1,
        generated_at=now,
        regenerated_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def record_outreach_event(
    session: Session,
    business_id: str,
    case_id: str,
    lead_id: Optional[str],
    event_type: str,
    channel: Optional[str] = None,
    engine: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> RecoveryOutreachEvent:
    ev = RecoveryOutreachEvent(
        business_id=business_id,
        case_id=case_id,
        lead_id=lead_id,
        event_type=event_type,
        channel=channel,
        engine=engine,
        detail=detail or {},
    )
    session.add(ev)
    session.flush()
    return ev


def list_outreach_events(
    session: Session, business_id: str, case_id: str, limit: int = 100
) -> List[RecoveryOutreachEvent]:
    return list(
        session.execute(
            select(RecoveryOutreachEvent)
            .where(
                RecoveryOutreachEvent.business_id == business_id,
                RecoveryOutreachEvent.case_id == case_id,
            )
            .order_by(RecoveryOutreachEvent.created_at.asc())
            .limit(limit)
        ).scalars().all()
    )


def execution_outcomes(
    session: Session, business_id: str, case_id: str
) -> Dict[str, Any]:
    """Derive outcomes ONLY from persisted execution events.

    If no execution events exist, returns an empty/unknown state with
    ``has_execution_data=False`` — we never fabricate completion.
    """
    rows = session.execute(
        select(RecoveryOutreachEvent.event_type, func.count())
        .where(
            RecoveryOutreachEvent.business_id == business_id,
            RecoveryOutreachEvent.case_id == case_id,
            RecoveryOutreachEvent.event_type.in_(EXECUTION_EVENT_TYPES),
        )
        .group_by(RecoveryOutreachEvent.event_type)
    ).all()
    counts = {et: int(c) for et, c in rows}
    latest = session.execute(
        select(func.max(RecoveryOutreachEvent.created_at))
        .where(
            RecoveryOutreachEvent.business_id == business_id,
            RecoveryOutreachEvent.case_id == case_id,
            RecoveryOutreachEvent.event_type.in_(EXECUTION_EVENT_TYPES),
        )
    ).scalar_one()
    has_data = sum(counts.values()) > 0
    return {
        "has_execution_data": has_data,
        "counts": counts,
        "last_execution_at": latest.isoformat() if latest else None,
        "note": (
            "Outcomes are reported only from persisted execution records. "
            "Calls/messages are not placed by this system; content generation is tracked separately."
            if not has_data
            else None
        ),
    }


def dashboard_summary(session: Session, business_id: str) -> Dict[str, Any]:
    """Aggregate dashboard metrics from real data only."""
    total_cases = session.execute(
        select(func.count()).select_from(RecoveryCase).where(
            RecoveryCase.business_id == business_id
        )
    ).scalar_one()
    eligible_leads = total_cases

    level_rows = session.execute(
        select(RecoveryCase.priority_level, func.count())
        .where(RecoveryCase.business_id == business_id)
        .group_by(RecoveryCase.priority_level)
    ).all()
    priority_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for level, count in level_rows:
        if level in priority_counts:
            priority_counts[level] = int(count)

    awaiting = session.execute(
        select(func.count())
        .select_from(RecoveryCase)
        .where(
            RecoveryCase.business_id == business_id,
            RecoveryCase.status == RecoveryCaseStatus.RECOMMENDED,
        )
    ).scalar_one()

    # Outcomes are only counted when real execution records exist.
    completed_calls = session.execute(
        select(func.count())
        .select_from(RecoveryOutreachEvent)
        .where(
            RecoveryOutreachEvent.business_id == business_id,
            RecoveryOutreachEvent.event_type == "call_completed",
        )
    ).scalar_one()
    callbacks = session.execute(
        select(func.count())
        .select_from(RecoveryOutreachEvent)
        .where(
            RecoveryOutreachEvent.business_id == business_id,
            RecoveryOutreachEvent.event_type == "callback_requested",
        )
    ).scalar_one()
    recovered_exec = session.execute(
        select(func.count())
        .select_from(RecoveryOutreachEvent)
        .where(
            RecoveryOutreachEvent.business_id == business_id,
            RecoveryOutreachEvent.event_type == "call_completed",
            RecoveryOutreachEvent.detail.op("->>")("outcome") == "recovered",
        )
    ).scalar_one()

    total_scored = session.execute(
        select(func.count())
        .select_from(RecoveryCase)
        .where(
            RecoveryCase.business_id == business_id,
            RecoveryCase.priority_score.is_not(None),
        )
    ).scalar_one()

    return {
        "totals": {
            "recovery_cases": int(total_cases),
            "eligible_leads": int(eligible_leads),
        },
        "priority_counts": priority_counts,
        "awaiting_outreach": int(awaiting),
        "outcomes": {
            "completed_calls": int(completed_calls),
            "callback_requests": int(callbacks),
            "successful_recoveries": int(recovered_exec),
            "note": "Only counted from persisted execution records; zero means no execution data recorded.",
        },
        "scoring": {
            "total_scored": int(total_scored),
            "ranking_order": "priority_score desc, score desc, case_id asc",
        },
        "analytics": {
            "recovery_rate_pct": round((recovered_exec / total_cases) * 100.0, 1) if total_cases > 0 else 0.0,
            "leakage_mitigated": int(recovered_exec),
            "analytics_dashboard_url": "/analytics/dashboard",
        },
    }


def safe_case_dict(case: RecoveryCase) -> Dict[str, Any]:
    return {
        "case_id": case.id,
        "lead_id": case.lead_id,
        "lead_name": case.lead_name,
        "business_id": case.business_id,
        "score": float(case.score) if case.score is not None else None,
        "priority_score": float(case.priority_score) if case.priority_score is not None else None,
        "priority_rank": case.priority_rank,
        "priority_level": case.priority_level,
        "score_breakdown": case.score_breakdown,
        "scored_at": case.scored_at.isoformat() if case.scored_at else None,
        "signals": case.signals,
        "route": case.route,
        "action": case.action,
        "channel": case.channel,
        "message": case.message,
        "rationale": case.rationale,
        "engine": case.engine,
        "needs_human_approval": case.needs_human_approval,
        "status": case.status,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "updated_at": case.updated_at.isoformat() if case.updated_at else None,
    }
