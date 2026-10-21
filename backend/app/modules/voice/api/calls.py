"""Call endpoints.

    POST /api/v1/voice/calls                 request an outbound call
    GET  /api/v1/voice/calls                 list the calls of the business
    GET  /api/v1/voice/calls/{call_id}       read one call
    GET  /api/v1/voice/calls/{call_id}/events    read the provider events
    GET  /api/v1/voice/calls/{call_id}/outcome   read the post-call outcome
    POST /api/v1/voice/calls/{call_id}/refresh   re-read the status from the provider
    POST /api/v1/voice/calls/{call_id}/cancel    cancel a queued/scheduled call
    POST /api/v1/voice/calls/{call_id}/postcall  queue the post-call workflow

``POST /voice/calls`` is the only way to place a call. It always runs the
compliance gate first: a blocked call never reaches the provider and the
decision is persisted and audited either way.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.errors import LeadNotFoundError
from app.modules.voice import repository as repo
from app.modules.voice.api.deps import (
    current_business,
    enforce_call_rate_limit,
    optional_idempotency_key,
)
from app.modules.voice.api.serializers import (
    call_event_payload,
    call_outcome_payload,
    call_payload,
    list_payload,
)
from app.modules.voice.constants import (
    TERMINAL_STATUSES,
    AuditAction,
    CallStatus,
    JobType,
)
from app.modules.voice.errors import (
    CallNotFoundError,
    IdempotencyConflictError,
    ProviderError,
)
from app.modules.voice.jobs import queue
from app.modules.voice.orchestrator import CallOrchestrator, CallRequest
from app.modules.voice.providers.registry import build_provider
from app.modules.voice.schemas import (
    CallEventResponse,
    CallInitiationRequest,
    CallInitiationResponse,
    CallListResponse,
    CallOutcomeResponse,
    CallResponse,
    CallStatusResponse,
)

router = APIRouter(
    prefix="/voice/calls",
    tags=["Voice — Calls"],
)

#: Scope used for the shared idempotency table.
IDEMPOTENCY_SCOPE = "voice.call"


def _payload_hash(body: Dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _call_or_404(session: Session, business_id: str, call_id: str):
    call = repo.get_call(session, business_id, call_id)
    if call is None:
        raise CallNotFoundError(call_id)
    return call


@router.post(
    "",
    response_model=CallInitiationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        403: {"description": "The compliance gate blocked the call."},
        409: {"description": "The idempotency key was reused with another payload."},
        429: {"description": "The per-business call rate limit was exceeded."},
        503: {"description": "The voice provider could not be reached."},
    },
)
async def initiate_call(
    payload: CallInitiationRequest,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    idempotency_key: Optional[str] = Depends(optional_idempotency_key),
    _rate_limit: None = Depends(enforce_call_rate_limit),
) -> Dict[str, Any]:
    """Request an outbound call to a lead.

    The request is idempotent when an ``Idempotency-Key`` header is
    supplied: a repeated request with the same key returns the original
    call instead of placing a second one. Reusing the key with a
    different payload is rejected.

    The compliance gate is evaluated *before* the provider is contacted
    and its decision is persisted either way. While
    ``CALLORA_VOICE_ENABLE_OUTBOUND_CALLS`` is false the call is recorded
    as ``simulated`` and no provider is contacted.
    """
    body = payload.model_dump(mode="json")
    hash_value = _payload_hash(body)

    if idempotency_key:
        existing = repo.get_idempotency_record(
            db, business_id, IDEMPOTENCY_SCOPE, idempotency_key
        )
        if existing is not None:
            if existing.payload_hash and existing.payload_hash != hash_value:
                raise IdempotencyConflictError(idempotency_key)
            call = repo.get_call(db, business_id, existing.response_ref or "")
            if call is not None:
                decision = _decision_for_call(db, call)
                return {
                    "call": call_payload(call),
                    "compliance_decision_id": call.compliance_decision_id or "",
                    "compliance_decision": decision,
                    "compliance_checks": [],
                    "simulated": bool((call.call_metadata or {}).get("simulated")),
                    "message": "the request was already processed (idempotent replay)",
                }

    lead = repo.get_lead(db, business_id, payload.lead_id)
    if lead is None:
        raise LeadNotFoundError(payload.lead_id)

    orchestrator = CallOrchestrator(settings, db)
    request = CallRequest(
        business_id=business_id,
        lead_id=payload.lead_id,
        language=payload.language,
        scheduled_at=payload.scheduled_at,
        from_number=payload.from_number,
        agent_id=payload.agent_id,
        extra_context=payload.extra_context or {},
        dry_run=payload.dry_run,
    )
    result = orchestrator.initiate(request)

    if idempotency_key:
        repo.store_idempotency_record(
            db,
            business_id,
            IDEMPOTENCY_SCOPE,
            idempotency_key,
            response_ref=result.call.id,
            payload_hash=hash_value,
        )
        db.commit()

    return {
        "call": call_payload(result.call),
        "compliance_decision_id": result.decision_id,
        "compliance_decision": result.evaluation.decision,
        "compliance_checks": [c.to_dict() for c in result.evaluation.checks],
        "simulated": result.simulated,
        "message": (
            "the call was recorded as simulated; no provider was contacted"
            if result.simulated
            else "the call was placed"
        ),
    }


def _decision_for_call(session: Session, call) -> str:
    decision_id = call.compliance_decision_id
    if not decision_id:
        return "unknown"
    from app.modules.voice.models import ComplianceDecision

    decision = session.get(ComplianceDecision, decision_id)
    return decision.decision if decision is not None else "unknown"


@router.get("", response_model=CallListResponse, status_code=status.HTTP_200_OK)
async def list_calls(
    business_id: str = Depends(current_business),
    call_status: Optional[str] = Query(default=None, alias="status"),
    lead_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List the calls of the business."""
    calls = repo.list_calls(
        db, business_id, status=call_status, lead_id=lead_id, limit=limit, offset=offset
    )
    items = [call_payload(call) for call in calls]
    return list_payload(items, business_id, limit, offset)


@router.get("/{call_id}", response_model=CallResponse, status_code=status.HTTP_200_OK)
async def read_call(
    call_id: str,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
):
    """Read one call."""
    call = _call_or_404(db, business_id, call_id)
    return call_payload(call)


@router.get(
    "/{call_id}/events",
    response_model=list[CallEventResponse],
    status_code=status.HTTP_200_OK,
)
async def read_call_events(
    call_id: str,
    business_id: str = Depends(current_business),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Read the provider events that were applied to one call."""
    _call_or_404(db, business_id, call_id)
    events = repo.list_call_events(db, business_id, call_id, limit=limit, offset=offset)
    return [call_event_payload(event) for event in events]


@router.get(
    "/{call_id}/outcome",
    response_model=CallOutcomeResponse,
    status_code=status.HTTP_200_OK,
)
async def read_call_outcome(
    call_id: str,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
):
    """Read the structured post-call outcome of one call.

    The outcome is produced by the background post-call workflow; it may
    not exist yet for a call that has not reached a terminal status.
    """
    _call_or_404(db, business_id, call_id)
    outcome = repo.get_outcome(db, call_id)
    if outcome is None:
        from app.modules.voice.errors import CallOutcomeNotFoundError

        raise CallOutcomeNotFoundError(call_id)
    return call_outcome_payload(outcome)


@router.post(
    "/{call_id}/refresh",
    response_model=CallStatusResponse,
    status_code=status.HTTP_200_OK,
)
async def refresh_call(
    call_id: str,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Re-read the state of a call from the voice provider.

    This is the recovery path for a missed webhook: the provider is
    polled once and the call is updated. It never creates a call.
    """
    call = _call_or_404(db, business_id, call_id)
    if not call.provider_call_id:
        return {
            "call": call_payload(call),
            "refreshed": False,
            "message": "the call was never placed with the provider",
        }
    provider = build_provider(settings=settings)
    orchestrator = CallOrchestrator(settings, db, provider=provider)
    try:
        orchestrator.refresh_status(call)
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()
    db.commit()
    return {
        "call": call_payload(call),
        "refreshed": True,
        "message": "the call status was refreshed from the provider",
    }


@router.post(
    "/{call_id}/cancel",
    response_model=CallStatusResponse,
    status_code=status.HTTP_200_OK,
)
async def cancel_call(
    call_id: str,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Cancel a call that has not started yet.

    Only a call that is still queued or scheduled can be cancelled; a
    call that already connected has to be ended by the provider.
    """
    call = _call_or_404(db, business_id, call_id)
    if call.status not in (CallStatus.QUEUED, CallStatus.SCHEDULED, CallStatus.PENDING):
        from app.errors import InvalidParameterError

        raise InvalidParameterError(
            f"a call in status '{call.status}' cannot be cancelled"
        )
    if not call.provider_call_id:
        repo.update_call(
            db,
            call,
            {
                "status": CallStatus.CANCELED,
                "ended_at": datetime.now(timezone.utc),
            },
        )
    else:
        provider = build_provider(settings=settings)
        try:
            provider.stop_call(call.provider_call_id)
        except ProviderError:
            raise
        finally:
            close = getattr(provider, "close", None)
            if callable(close):
                close()
        repo.update_call(
            db,
            call,
            {
                "status": CallStatus.CANCELED,
                "ended_at": datetime.now(timezone.utc),
            },
        )
    repo.add_audit_log(
        db,
        action=AuditAction.CALL_CANCELED,
        business_id=business_id,
        actor="api",
        entity_type="call",
        entity_id=call.id,
        reason_code="cancelled_by_api",
        reason="the call was cancelled before it started",
    )
    db.commit()
    return {
        "call": call_payload(call),
        "refreshed": False,
        "message": "the call was cancelled",
    }


@router.post(
    "/{call_id}/postcall",
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_postcall(
    call_id: str,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """Queue the post-call workflow for one call.

    The workflow normally runs automatically when the provider reports a
    terminal status. This endpoint re-queues it (for example after the
    transcript was extended) without ever creating a second call.
    """
    call = _call_or_404(db, business_id, call_id)
    status_key = call.status if call.status in TERMINAL_STATUSES else "manual"
    job, created = queue.enqueue(
        db,
        JobType.POSTCALL_PROCESS,
        payload={
            "call_id": call.id,
            "business_id": call.business_id,
            "status": call.status,
        },
        dedupe_key=queue.postcall_dedupe_key(call.id, status_key),
        max_attempts=settings.voice_job_max_attempts,
        business_id=call.business_id,
    )
    db.commit()
    return {
        "job_id": job.id if job else None,
        "created": created,
        "call_id": call.id,
        "status": job.status if job else None,
        "message": (
            "the post-call workflow was queued"
            if created
            else "a post-call job for this call already exists"
        ),
    }
