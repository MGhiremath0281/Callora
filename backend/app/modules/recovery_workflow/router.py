"""Recovery workflow API routes.

Everything is business-scoped through ``X-API-Key``. Static paths are
declared before the dynamic ``/{run_id}`` route. No endpoint here
places a real call or sends a real message by itself: voice calls still
go through the voice module's compliance gate and the master outbound
switch, and message steps report ``channel_not_configured`` until a
delivery adapter exists.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import authenticate_business
from app.config import Settings, get_settings
from app.db.session import get_db
from app.errors import InvalidParameterError, LeadNotFoundError
from app.modules.recovery_workflow import analytics as wf_analytics
from app.modules.recovery_workflow import appointments as wf_appointments
from app.modules.recovery_workflow.eligibility import evaluate_eligibility
from app.modules.recovery_workflow.models import (
    Appointment,
    FollowUpTask,
    IntegrationConnection,
    IntegrationSyncState,
    OutcomeRecord,
    Reminder,
    WorkflowAction,
    WorkflowRun,
)
from app.modules.recovery_workflow.rules import RuleEngine, RuleError, evaluate_condition
from app.modules.recovery_workflow.service import (
    RUN_TRANSITIONS,
    WorkflowEngine,
    WorkflowRunError,
    prefixed_id,
)
from app.modules.voice import repository as voice_repo

router = APIRouter(
    prefix="/workflow",
    tags=["Recovery Workflow"],
)

RUN_STATUSES = sorted(RUN_TRANSITIONS)


async def business_credentials(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    business_id: str = Depends(authenticate_business),
) -> str:
    return business_id


def _run_or_404(db: Session, business_id: str, run_id: str) -> WorkflowRun:
    run = db.execute(
        select(WorkflowRun).where(
            WorkflowRun.business_id == business_id, WorkflowRun.id == run_id
        )
    ).scalars().first()
    if run is None:
        raise _NotFound(f"Workflow run '{run_id}' was not found")
    return run


class _NotFound(InvalidParameterError):
    """404-flavoured not found for workflow resources."""

    status_code = 404
    code = "workflow_run_not_found"

    def __init__(self, message: str):
        super().__init__(message)


def _run_dict(run: WorkflowRun, actions: Optional[List[WorkflowAction]] = None) -> Dict[str, Any]:
    payload = {
        "id": run.id,
        "business_id": run.business_id,
        "case_id": run.case_id,
        "lead_id": run.lead_id,
        "workflow_type": run.workflow_type,
        "status": run.status,
        "dry_run": run.dry_run,
        "approval_required": run.approval_required,
        "approved_by": run.approved_by,
        "approved_at": run.approved_at.isoformat() if run.approved_at else None,
        "idempotency_key": run.idempotency_key,
        "input": run.input,
        "result": run.result,
        "current_step": run.current_step,
        "error_message": run.error_message,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "allowed_transitions": sorted(RUN_TRANSITIONS.get(run.status, set())),
    }
    if actions is not None:
        payload["actions"] = [
            {
                "id": a.id,
                "step": a.step,
                "action_type": a.action_type,
                "status": a.status,
                "attempt": a.attempt,
                "request": a.request,
                "response": a.response,
                "error_message": a.error_message,
                "created_at": a.created_at.isoformat(),
            }
            for a in actions
        ]
    return payload


# ---------------------------------------------------------------------------
# Eligibility (static, before /runs dynamic paths)
# ---------------------------------------------------------------------------


@router.post("/eligibility", status_code=status.HTTP_200_OK)
async def check_eligibility(
    payload: Dict[str, Any],
    business_id: str = Depends(business_credentials),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Evaluate the workflow eligibility gate for one lead/channel."""
    lead_id = payload.get("lead_id")
    if not lead_id:
        raise InvalidParameterError("lead_id is required")
    channel = payload.get("channel", "voice")
    if channel not in ("voice", "sms", "whatsapp", "email"):
        raise InvalidParameterError(f"unsupported channel {channel!r}")
    if voice_repo.get_lead(db, business_id, lead_id) is None:
        raise LeadNotFoundError(lead_id)
    persist = bool(payload.get("persist", True))
    result = evaluate_eligibility(
        db,
        settings,
        business_id=business_id,
        lead_id=lead_id,
        channel=channel,
        case_id=payload.get("case_id"),
        persist=persist,
    )
    db.commit()
    return {
        "business_id": business_id,
        "lead_id": lead_id,
        "channel": channel,
        **result.to_dict(),
    }


# ---------------------------------------------------------------------------
# Business rules
# ---------------------------------------------------------------------------


@router.get("/rules", status_code=status.HTTP_200_OK)
async def list_rules(
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    engine = RuleEngine(db, business_id)
    return [
        {
            "key": rule.key,
            "name": rule.name,
            "description": rule.description,
            "category": rule.category,
            "value": rule.value,
            "is_active": rule.is_active,
            "current_version": rule.current_version,
        }
        for rule in engine.list_rules()
    ]


@router.put("/rules", status_code=status.HTTP_200_OK)
async def upsert_rule(
    payload: Dict[str, Any],
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    """Create or version a business rule. Every change is audited."""
    required = {"key", "name", "value"}
    missing = required - set(payload)
    if missing:
        raise InvalidParameterError(f"missing fields: {sorted(missing)}")
    engine = RuleEngine(db, business_id)
    try:
        rule = engine.upsert_rule(
            key=payload["key"],
            name=payload["name"],
            value=payload["value"],
            description=payload.get("description"),
            category=payload.get("category", "workflow"),
            actor=payload.get("actor", "api"),
            change_summary=payload.get("change_summary"),
        )
    except RuleError as exc:
        raise InvalidParameterError(str(exc)) from exc
    db.commit()
    return {
        "key": rule.key,
        "value": rule.value,
        "current_version": rule.current_version,
        "is_active": rule.is_active,
    }


@router.get("/rules/{rule_key}/versions", status_code=status.HTTP_200_OK)
async def rule_versions(
    rule_key: str,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    engine = RuleEngine(db, business_id)
    return [
        {
            "version": v.version,
            "value": v.value,
            "change_summary": v.change_summary,
            "created_by": v.created_by,
            "created_at": v.created_at.isoformat(),
        }
        for v in engine.versions(rule_key)
    ]


@router.post("/rules/evaluate", status_code=status.HTTP_200_OK)
async def evaluate_rule_condition(
    payload: Dict[str, Any],
    business_id: str = Depends(business_credentials),
):
    """Evaluate one condition against a supplied context (no side effects)."""
    condition = payload.get("condition")
    context = payload.get("context") or {}
    if not isinstance(condition, dict):
        raise InvalidParameterError("condition must be an object")
    try:
        matched = evaluate_condition(condition, context)
    except RuleError as exc:
        raise InvalidParameterError(str(exc)) from exc
    return {"matched": matched, "condition": condition, "context": context}


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


@router.post("/runs", status_code=status.HTTP_201_CREATED)
async def create_run(
    payload: Dict[str, Any],
    business_id: str = Depends(business_credentials),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Create a workflow run (idempotent when a key is supplied)."""
    lead_id = payload.get("lead_id")
    if not lead_id:
        raise InvalidParameterError("lead_id is required")
    engine = WorkflowEngine(db, settings)
    run = engine.create_run(
        business_id=business_id,
        lead_id=lead_id,
        workflow_type=payload.get("workflow_type", "recovery_call"),
        channel=payload.get("channel"),
        dry_run=bool(payload.get("dry_run", False)),
        idempotency_key=payload.get("idempotency_key"),
        params=payload.get("params") or {},
        case_id=payload.get("case_id"),
    )
    db.commit()
    return _run_dict(run)


@router.get("/runs", status_code=status.HTTP_200_OK)
async def list_runs(
    business_id: str = Depends(business_credentials),
    run_status: Optional[str] = Query(default=None, alias="status"),
    lead_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    if run_status is not None and run_status not in RUN_STATUSES:
        raise InvalidParameterError(f"unknown status {run_status!r}")
    stmt = select(WorkflowRun).where(WorkflowRun.business_id == business_id)
    if run_status:
        stmt = stmt.where(WorkflowRun.status == run_status)
    if lead_id:
        stmt = stmt.where(WorkflowRun.lead_id == lead_id)
    runs = list(
        db.execute(
            stmt.order_by(WorkflowRun.created_at.desc()).limit(limit).offset(offset)
        ).scalars().all()
    )
    return {
        "items": [_run_dict(r) for r in runs],
        "business_id": business_id,
        "limit": limit,
        "offset": offset,
    }


@router.get("/runs/{run_id}", status_code=status.HTTP_200_OK)
async def get_run(
    run_id: str,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    run = _run_or_404(db, business_id, run_id)
    actions = list(
        db.execute(
            select(WorkflowAction)
            .where(WorkflowAction.run_id == run.id)
            .order_by(WorkflowAction.created_at.asc())
        ).scalars().all()
    )
    return _run_dict(run, actions)


@router.post("/runs/{run_id}/approve", status_code=status.HTTP_200_OK)
async def approve_run(
    run_id: str,
    payload: Optional[Dict[str, Any]] = None,
    business_id: str = Depends(business_credentials),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    run = _run_or_404(db, business_id, run_id)
    actor = (payload or {}).get("actor", "api")
    engine = WorkflowEngine(db, settings)
    engine.approve_run(run, actor=actor)
    db.commit()
    return _run_dict(run)


@router.post("/runs/{run_id}/execute", status_code=status.HTTP_200_OK)
async def execute_run(
    run_id: str,
    payload: Optional[Dict[str, Any]] = None,
    business_id: str = Depends(business_credentials),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Execute every step of an approved run, append-only."""
    run = _run_or_404(db, business_id, run_id)
    actor = (payload or {}).get("actor", "api")
    engine = WorkflowEngine(db, settings)
    try:
        run = engine.execute_run(run, actor=actor)
    except WorkflowRunError as exc:
        db.rollback()
        raise InvalidParameterError(str(exc)) from exc
    db.commit()
    actions = list(
        db.execute(
            select(WorkflowAction)
            .where(WorkflowAction.run_id == run.id)
            .order_by(WorkflowAction.created_at.asc())
        ).scalars().all()
    )
    return _run_dict(run, actions)


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_run(
    run_id: str,
    payload: Optional[Dict[str, Any]] = None,
    business_id: str = Depends(business_credentials),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    run = _run_or_404(db, business_id, run_id)
    data = payload or {}
    engine = WorkflowEngine(db, settings)
    try:
        engine.cancel_run(run, actor=data.get("actor", "api"), reason=data.get("reason", "cancelled"))
    except WorkflowRunError as exc:
        raise InvalidParameterError(str(exc)) from exc
    db.commit()
    return _run_dict(run)


@router.post("/runs/{run_id}/pause", status_code=status.HTTP_200_OK)
async def pause_run(
    run_id: str,
    payload: Optional[Dict[str, Any]] = None,
    business_id: str = Depends(business_credentials),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    run = _run_or_404(db, business_id, run_id)
    engine = WorkflowEngine(db, settings)
    try:
        engine.pause_run(run, actor=(payload or {}).get("actor", "api"))
    except WorkflowRunError as exc:
        raise InvalidParameterError(str(exc)) from exc
    db.commit()
    return _run_dict(run)


# ---------------------------------------------------------------------------
# Follow-up tasks
# ---------------------------------------------------------------------------


@router.get("/followups", status_code=status.HTTP_200_OK)
async def list_followups(
    business_id: str = Depends(business_credentials),
    task_status: Optional[str] = Query(default=None, alias="status"),
    lead_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(FollowUpTask).where(FollowUpTask.business_id == business_id)
    if task_status:
        stmt = stmt.where(FollowUpTask.status == task_status)
    if lead_id:
        stmt = stmt.where(FollowUpTask.lead_id == lead_id)
    tasks = list(
        db.execute(
            stmt.order_by(FollowUpTask.due_at.asc()).limit(limit).offset(offset)
        ).scalars().all()
    )
    return {
        "items": [
            {
                "id": t.id,
                "lead_id": t.lead_id,
                "case_id": t.case_id,
                "workflow_run_id": t.workflow_run_id,
                "channel": t.channel,
                "reason": t.reason,
                "due_at": t.due_at.isoformat() if t.due_at else None,
                "status": t.status,
                "priority": t.priority,
                "assigned_to": t.assigned_to,
                "outcome": t.outcome,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            }
            for t in tasks
        ],
        "business_id": business_id,
        "limit": limit,
        "offset": offset,
    }


@router.post("/followups/{task_id}/complete", status_code=status.HTTP_200_OK)
async def complete_followup(
    task_id: str,
    payload: Optional[Dict[str, Any]] = None,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    from datetime import datetime, timezone

    task = db.execute(
        select(FollowUpTask).where(
            FollowUpTask.business_id == business_id, FollowUpTask.id == task_id
        )
    ).scalars().first()
    if task is None:
        raise _NotFound(f"Follow-up task '{task_id}' was not found")
    data = payload or {}
    task.status = "completed"
    task.outcome = data.get("outcome")
    task.notes = data.get("notes", task.notes)
    task.completed_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": task.id, "status": task.status, "outcome": task.outcome}


# ---------------------------------------------------------------------------
# Appointments & reminders
# ---------------------------------------------------------------------------


@router.post("/appointments", status_code=status.HTTP_201_CREATED)
async def create_appointment(
    payload: Dict[str, Any],
    business_id: str = Depends(business_credentials),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    lead_id = payload.get("lead_id")
    if not lead_id:
        raise InvalidParameterError("lead_id is required")
    appointment = wf_appointments.book_appointment(
        db,
        settings,
        business_id=business_id,
        lead_id=lead_id,
        scheduled_start=payload.get("scheduled_start"),
        scheduled_end=payload.get("scheduled_end"),
        title=payload.get("title", "Recovery consultation"),
        attendee_name=payload.get("attendee_name"),
        attendee_phone=payload.get("attendee_phone"),
        location=payload.get("location"),
        meeting_url=payload.get("meeting_url"),
        notes=payload.get("notes"),
        timezone_name=payload.get("timezone", "UTC"),
        case_id=payload.get("case_id"),
        workflow_run_id=payload.get("workflow_run_id"),
        provider=payload.get("provider"),
    )
    if payload.get("schedule_reminders", True):
        wf_appointments.schedule_reminders(db, settings, appointment)
    db.commit()
    return _appointment_dict(appointment)


@router.get("/appointments", status_code=status.HTTP_200_OK)
async def list_appointments(
    business_id: str = Depends(business_credentials),
    lead_id: Optional[str] = Query(default=None),
    appt_status: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = wf_appointments.list_appointments(
        db, business_id, lead_id=lead_id, status=appt_status, limit=limit, offset=offset
    )
    return {
        "items": [_appointment_dict(a) for a in items],
        "business_id": business_id,
        "limit": limit,
        "offset": offset,
    }


def _appointment_dict(a: Appointment) -> Dict[str, Any]:
    return {
        "id": a.id,
        "business_id": a.business_id,
        "case_id": a.case_id,
        "lead_id": a.lead_id,
        "workflow_run_id": a.workflow_run_id,
        "provider": a.provider,
        "provider_event_id": a.provider_event_id,
        "status": a.status,
        "scheduled_start": a.scheduled_start.isoformat(),
        "scheduled_end": a.scheduled_end.isoformat() if a.scheduled_end else None,
        "timezone": a.timezone,
        "title": a.title,
        "attendee_name": a.attendee_name,
        "attendee_phone": a.attendee_phone,
        "location": a.location,
        "meeting_url": a.meeting_url,
        "confirmation": a.confirmation,
        "error_message": a.error_message,
    }


@router.post("/appointments/{appointment_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_appointment(
    appointment_id: str,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    appointment = wf_appointments.get_appointment(db, business_id, appointment_id)
    if appointment is None:
        raise _NotFound(f"Appointment '{appointment_id}' was not found")
    wf_appointments.cancel_appointment(db, business_id, appointment)
    db.commit()
    return _appointment_dict(appointment)


@router.get("/reminders", status_code=status.HTTP_200_OK)
async def list_reminders(
    business_id: str = Depends(business_credentials),
    reminder_status: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = wf_appointments.list_reminders(
        db, business_id, status=reminder_status, limit=limit, offset=offset
    )
    return {
        "items": [
            {
                "id": r.id,
                "appointment_id": r.appointment_id,
                "channel": r.channel,
                "remind_at": r.remind_at.isoformat(),
                "status": r.status,
                "attempts": r.attempts,
                "sent_at": r.sent_at.isoformat() if r.sent_at else None,
                "error_message": r.error_message,
                "detail": r.detail,
            }
            for r in items
        ],
        "business_id": business_id,
    }


@router.post("/reminders/dispatch", status_code=status.HTTP_200_OK)
async def dispatch_reminders(
    business_id: str = Depends(business_credentials),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    """Attempt the due reminders; never claim a send without an adapter."""
    result = wf_appointments.dispatch_due_reminders(db, settings)
    db.commit()
    return result


# ---------------------------------------------------------------------------
# Conversation analysis & analytics (static, before dynamic routes)
# ---------------------------------------------------------------------------


@router.post("/analysis", status_code=status.HTTP_200_OK)
async def analyse_call(
    payload: Dict[str, Any],
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    """Analyse one owned call's transcript with the deterministic rules."""
    from app.modules.recovery_workflow.analysis import analyse_transcript

    call_id = payload.get("call_id")
    if not call_id:
        raise InvalidParameterError("call_id is required")
    result = analyse_transcript(db, business_id=business_id, call_id=call_id)
    db.commit()
    return _analysis_dict(result)


@router.post("/analysis/bulk", status_code=status.HTTP_200_OK)
async def analyse_bulk(
    payload: Optional[Dict[str, Any]] = None,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    from app.modules.recovery_workflow.analysis import analyse_business_calls

    data = payload or {}
    result = analyse_business_calls(
        db, business_id, limit=int(data.get("limit", 100))
    )
    db.commit()
    return result


def _analysis_dict(a) -> Dict[str, Any]:
    return {
        "id": a.id,
        "business_id": a.business_id,
        "call_id": a.call_id,
        "lead_id": a.lead_id,
        "transcript_hash": a.transcript_hash,
        "analysis": a.analysis,
        "objections": a.objections,
        "buying_signals": a.buying_signals,
        "leakage_flags": a.leakage_flags,
        "sentiment": a.sentiment,
        "intent": a.intent,
        "engine": a.engine,
        "model": a.model,
        "analyzed_at": a.analyzed_at.isoformat(),
    }


@router.get("/analytics/funnel", status_code=status.HTTP_200_OK)
async def funnel(
    days: int = Query(default=30, ge=1, le=365),
    business_id: str = Depends(business_credentials),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    return wf_analytics.funnel_summary(db, settings, business_id, days=days)


@router.get("/analytics/leakage", status_code=status.HTTP_200_OK)
async def leakage(
    days: int = Query(default=30, ge=1, le=365),
    business_id: str = Depends(business_credentials),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
):
    return wf_analytics.leakage_report(db, settings, business_id, days=days)


@router.get("/analytics/roi", status_code=status.HTTP_200_OK)
async def roi(
    days: int = Query(default=90, ge=1, le=365),
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    return wf_analytics.roi_summary(db, business_id, days=days)


@router.post("/analytics/record-call-costs", status_code=status.HTTP_200_OK)
async def record_call_costs(
    days: int = Query(default=30, ge=1, le=365),
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    """Persist provider-reported call costs that have no cost row."""
    created = wf_analytics.record_call_costs_from_calls(db, business_id, days=days)
    db.commit()
    return {"created": created}


# ---------------------------------------------------------------------------
# Outcomes, costs, revenue
# ---------------------------------------------------------------------------


@router.post("/outcomes", status_code=status.HTTP_201_CREATED)
async def create_outcome(
    payload: Dict[str, Any],
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    required = {"lead_id", "outcome_type"}
    missing = required - set(payload)
    if missing:
        raise InvalidParameterError(f"missing fields: {sorted(missing)}")
    outcome = wf_analytics.record_outcome(
        db,
        business_id=business_id,
        lead_id=payload["lead_id"],
        outcome_type=payload["outcome_type"],
        verified=bool(payload.get("verified", False)),
        verified_by=payload.get("verified_by"),
        verification_source=payload.get("verification_source"),
        revenue_amount=payload.get("revenue_amount"),
        currency=payload.get("currency", "INR"),
        case_id=payload.get("case_id"),
        call_id=payload.get("call_id"),
        workflow_run_id=payload.get("workflow_run_id"),
        notes=payload.get("notes"),
        evidence=payload.get("evidence"),
        recorded_by=payload.get("recorded_by", "api"),
    )
    db.commit()
    return _outcome_dict(outcome)


@router.get("/outcomes", status_code=status.HTTP_200_OK)
async def list_outcomes(
    business_id: str = Depends(business_credentials),
    lead_id: Optional[str] = Query(default=None),
    outcome_type: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(OutcomeRecord).where(OutcomeRecord.business_id == business_id)
    if lead_id:
        stmt = stmt.where(OutcomeRecord.lead_id == lead_id)
    if outcome_type:
        stmt = stmt.where(OutcomeRecord.outcome_type == outcome_type)
    rows = list(
        db.execute(
            stmt.order_by(OutcomeRecord.created_at.desc()).limit(limit).offset(offset)
        ).scalars().all()
    )
    return {
        "items": [_outcome_dict(o) for o in rows],
        "business_id": business_id,
        "limit": limit,
        "offset": offset,
    }


def _outcome_dict(o: OutcomeRecord) -> Dict[str, Any]:
    return {
        "id": o.id,
        "business_id": o.business_id,
        "case_id": o.case_id,
        "lead_id": o.lead_id,
        "call_id": o.call_id,
        "workflow_run_id": o.workflow_run_id,
        "outcome_type": o.outcome_type,
        "verified": o.verified,
        "verified_by": o.verified_by,
        "verification_source": o.verification_source,
        "verified_at": o.verified_at.isoformat() if o.verified_at else None,
        "revenue_amount": float(o.revenue_amount) if o.revenue_amount is not None else None,
        "currency": o.currency,
        "notes": o.notes,
        "created_at": o.created_at.isoformat(),
    }


@router.post("/costs", status_code=status.HTTP_201_CREATED)
async def create_cost(
    payload: Dict[str, Any],
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    required = {"cost_type", "amount"}
    missing = required - set(payload)
    if missing:
        raise InvalidParameterError(f"missing fields: {sorted(missing)}")
    record = wf_analytics.record_cost(
        db,
        business_id=business_id,
        cost_type=payload["cost_type"],
        amount=float(payload["amount"]),
        currency=payload.get("currency", "INR"),
        source=payload.get("source", "manual"),
        lead_id=payload.get("lead_id"),
        call_id=payload.get("call_id"),
        case_id=payload.get("case_id"),
        detail=payload.get("detail"),
    )
    db.commit()
    return {"id": record.id, "cost_type": record.cost_type, "amount": float(record.amount)}


@router.post("/revenue", status_code=status.HTTP_201_CREATED)
async def create_revenue(
    payload: Dict[str, Any],
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    if "amount" not in payload:
        raise InvalidParameterError("amount is required")
    record = wf_analytics.record_revenue(
        db,
        business_id=business_id,
        amount=float(payload["amount"]),
        verified=bool(payload.get("verified", False)),
        verified_by=payload.get("verified_by"),
        currency=payload.get("currency", "INR"),
        case_id=payload.get("case_id"),
        lead_id=payload.get("lead_id"),
        outcome_id=payload.get("outcome_id"),
        source=payload.get("source", "manual"),
        detail=payload.get("detail"),
    )
    db.commit()
    return {
        "id": record.id,
        "amount": float(record.amount),
        "verified": record.verified,
        "verified_at": record.verified_at.isoformat() if record.verified_at else None,
    }


# ---------------------------------------------------------------------------
# Integration connections (register + honest sync state)
# ---------------------------------------------------------------------------


@router.get("/integrations", status_code=status.HTTP_200_OK)
async def list_integrations(
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    rows = list(
        db.execute(
            select(IntegrationConnection).where(
                IntegrationConnection.business_id == business_id
            )
        ).scalars().all()
    )
    return {
        "items": [
            {
                "id": c.id,
                "provider": c.provider,
                "name": c.name,
                "status": c.status,
                "config": c.config,
                "capabilities": c.capabilities,
                "last_checked_at": c.last_checked_at.isoformat() if c.last_checked_at else None,
                "last_error": c.last_error,
            }
            for c in rows
        ],
        "business_id": business_id,
    }


@router.post("/integrations", status_code=status.HTTP_201_CREATED)
async def create_integration(
    payload: Dict[str, Any],
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    """Register an integration.

    ``config`` must be non-secret; real secrets are resolved by the
    deployment and never accepted here.
    """
    from datetime import datetime, timezone

    required = {"provider", "name"}
    missing = required - set(payload)
    if missing:
        raise InvalidParameterError(f"missing fields: {sorted(missing)}")
    config = payload.get("config") or {}
    secret_keys = [k for k in config if "secret" in k.lower() or "token" in k.lower() or "password" in k.lower() or "key" == k.lower()]
    if secret_keys:
        raise InvalidParameterError(
            f"config must not contain secrets; rejected keys: {sorted(secret_keys)}"
        )
    from app.modules.recovery_workflow.models import IntegrationConnection

    connection = IntegrationConnection(
        id=prefixed_id("intg"),
        business_id=business_id,
        provider=payload["provider"],
        name=payload["name"],
        status="configured",
        config=config,
        capabilities=payload.get("capabilities") or [],
    )
    db.add(connection)
    db.flush()
    db.commit()
    return {"id": connection.id, "provider": connection.provider, "name": connection.name, "status": connection.status}


@router.post("/integrations/{connection_id}/sync", status_code=status.HTTP_200_OK)
async def sync_integration(
    connection_id: str,
    payload: Optional[Dict[str, Any]] = None,
    business_id: str = Depends(business_credentials),
    db: Session = Depends(get_db),
):
    """Record one synchronization attempt.

    No external adapter is implemented, so the sync honestly records
    ``provider_not_configured`` instead of pretending anything moved.
    """
    from datetime import datetime, timezone

    connection = db.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.business_id == business_id,
            IntegrationConnection.id == connection_id,
        )
    ).scalars().first()
    if connection is None:
        raise _NotFound(f"Integration '{connection_id}' was not found")
    direction = (payload or {}).get("direction", "push")
    if direction not in ("push", "pull"):
        raise InvalidParameterError("direction must be 'push' or 'pull'")
    state = db.execute(
        select(IntegrationSyncState).where(
            IntegrationSyncState.business_id == business_id,
            IntegrationSyncState.connection_id == connection_id,
            IntegrationSyncState.direction == direction,
        )
    ).scalars().first()
    if state is None:
        state = IntegrationSyncState(
            id=prefixed_id("isync"),
            business_id=business_id,
            connection_id=connection_id,
            direction=direction,
            status="idle",
        )
        db.add(state)
    state.status = "failed"
    state.last_error = (
        f"provider {connection.provider!r} is not configured; nothing was synchronized"
    )
    state.detail = {"reason_code": "provider_not_configured", "direction": direction}
    connection.last_checked_at = datetime.now(timezone.utc)
    connection.last_error = state.last_error
    db.commit()
    return {
        "connection_id": connection_id,
        "direction": direction,
        "status": state.status,
        "last_error": state.last_error,
        "total_synced": state.total_synced,
        "total_failed": state.total_failed,
    }
