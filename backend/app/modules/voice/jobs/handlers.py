"""Job handlers.

A handler receives the job and does the work. It must be safe to run
twice: the post-call workflow overwrites the outcome of a call instead of
appending, so a retried job produces the same result.
"""

import logging
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.config import Settings
from app.modules.voice import repository as repo
from app.modules.voice import sync
from app.modules.voice.jobs import queue
from app.modules.voice.postcall.graph import PostCallWorkflow
from app.modules.voice.postcall.llm import build_llm_client

logger = logging.getLogger("callora.voice.jobs.handlers")


def handle_postcall(session: Session, job, settings: Settings) -> Dict[str, Any]:
    """Run the post-call LangGraph workflow for one call."""
    call_id = job.payload.get("call_id")
    if not call_id:
        raise ValueError("the post-call job has no call_id")
    call = repo.get_call_any_business(session, str(call_id))
    if call is None:
        # The call was deleted; nothing to do, and retrying would not help.
        logger.info("postcall_job_skipped_missing_call", extra={"call_id": call_id})
        return {"skipped": "call_not_found"}

    llm = build_llm_client(settings)
    workflow = PostCallWorkflow(settings, llm)
    state = workflow.run(session, {"call_id": call.id})
    session.commit()

    outcome = repo.get_outcome(session, call.id)
    logger.info(
        "postcall_processed",
        extra={
            "call_id": call.id,
            "disposition": state.get("disposition"),
            "engine": state.get("engine"),
        },
    )
    return {
        "outcome_id": outcome.id if outcome else None,
        "disposition": state.get("disposition"),
        "engine": state.get("engine"),
    }


def handle_status_sync(session: Session, job, settings: Settings) -> Dict[str, Any]:
    """Reconcile the status of the calls with the voice provider.

    The provider webhook is the fast path; this job is the safety net
    for the webhooks that never arrive. It asks the provider for the
    status of the calls that are due for a check and applies the answer
    with the shared state machine.
    """
    business_id = job.payload.get("business_id")
    report = sync.sweep(session, settings, business_id=business_id)
    session.commit()
    logger.info(
        "status_sync_swept",
        extra={
            "checked": report.checked,
            "applied": report.applied,
            "unchanged": report.unchanged,
            "skipped": report.skipped,
            "failed": report.failed,
        },
    )
    return report.to_dict()


def handle_callback_dispatch(session: Session, job, settings: Settings) -> Dict[str, Any]:
    """Dial the callback the voice agent scheduled.

    The job was queued by ``VoiceToolService._schedule_callback`` for the
    moment the customer asked to be called. The full compliance gate runs
    again here — the request-time evaluation was advisory, the gate is the
    decision — so a consent change, a DND entry or a closed calling window
    between scheduling and dialing is honoured. Outbound calls stay off
    unless ``voice_enable_outbound_calls`` is set: the handoff then simply
    remains for the team.
    """
    from app.modules.voice.constants import AuditAction, HandoffStatus
    from app.modules.voice.errors import ComplianceBlockedError
    from app.modules.voice.models import Handoff
    from app.modules.voice.orchestrator import CallOrchestrator, CallRequest

    handoff_id = job.payload.get("handoff_id")
    lead_id = job.payload.get("lead_id")
    business_id = job.payload.get("business_id")
    if not handoff_id:
        raise ValueError("the callback dispatch job has no handoff_id")

    handoff = session.get(Handoff, str(handoff_id))
    if handoff is None:
        logger.info(
            "callback_dispatch_skipped_missing_handoff",
            extra={"handoff_id": handoff_id},
        )
        return {"skipped": "handoff_not_found"}
    if handoff.status != HandoffStatus.REQUESTED:
        # Already actioned (completed, cancelled, ...); a retry must not
        # dial it again.
        logger.info(
            "callback_dispatch_skipped_handoff_status",
            extra={"handoff_id": handoff.id, "status": handoff.status},
        )
        return {"skipped": "handoff_already_actioned", "status": handoff.status}

    if not settings.voice_enable_outbound_calls:
        logger.info(
            "callback_dispatch_outbound_disabled",
            extra={"handoff_id": handoff.id, "business_id": business_id},
        )
        return {"dispatched": False, "reason": "outbound_disabled"}

    if not handoff.lead_id:
        return {"dispatched": False, "reason": "handoff_has_no_lead"}

    request = CallRequest(
        business_id=str(business_id or handoff.business_id),
        lead_id=str(handoff.lead_id),
    )
    try:
        result = CallOrchestrator(settings, session).initiate(request)
    except ComplianceBlockedError as exc:
        # The gate decided at dial time; the call row and the decision are
        # persisted, the handoff stays open for the team.
        session.commit()
        repo.add_audit_log(
            session,
            action=AuditAction.CALLBACK_DISPATCHED,
            business_id=handoff.business_id,
            actor="callback_dispatch",
            entity_type="handoff",
            entity_id=handoff.id,
            decision="block",
            reason_code="compliance_blocked",
            reason=f"the scheduled callback was blocked at dial time ({exc.summary})",
            metadata={"handoff_id": handoff.id, "blocked": exc.reasons},
        )
        session.commit()
        return {
            "dispatched": False,
            "reason": "compliance_blocked",
            "blocked": [r.get("reason_code") for r in exc.reasons],
        }

    repo.update_handoff(
        session,
        handoff,
        {"call_id": result.call.id, "status": HandoffStatus.INITIATED},
    )
    repo.add_audit_log(
        session,
        action=AuditAction.CALLBACK_DISPATCHED,
        business_id=handoff.business_id,
        actor="callback_dispatch",
        entity_type="handoff",
        entity_id=handoff.id,
        decision="allow",
        reason_code="callback_dialed",
        reason="the scheduled callback was dispatched to the provider",
        metadata={"handoff_id": handoff.id, "call_id": result.call.id},
    )
    session.commit()

    logger.info(
        "callback_dispatch_attempted",
        extra={"handoff_id": handoff.id, "call_id": result.call.id},
    )
    return {
        "dispatched": True,
        "call_id": result.call.id,
        "simulated": result.simulated,
    }


#: type -> handler
DEFAULT_HANDLERS = {
    "postcall.process": handle_postcall,
    "status.sync": handle_status_sync,
    "callback.dispatch": handle_callback_dispatch,
}


def get_handler(job_type: str):
    return DEFAULT_HANDLERS.get(job_type)
