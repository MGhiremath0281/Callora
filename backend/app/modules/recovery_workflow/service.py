"""Workflow run execution engine.

A workflow run is the auditable execution of one recovery action for one
lead. The engine enforces, in this order:

1. **Idempotency** — the same ``idempotency_key`` returns the same run.
2. **Approval policy** — real outbound actions require an explicit
   approval unless the business switched on the (development-oriented)
   ``workflow_allow_unapproved_internal`` rule *and* the step is internal.
   A ``dry_run`` run never contacts anything, so it may execute without
   approval.
3. **Eligibility** — every execution re-evaluates the persisted
   eligibility gate (consent, DND, calling window, duplicate call,
   quotas, attempt cap, concurrency). A block is recorded, never worked
   around.
4. **Append-only actions** — every step appends exactly one
   :class:`WorkflowAction` row describing what was attempted and what
   happened. A message step with no configured adapter is recorded as
   ``channel_not_configured`` — never as a fabricated success.

Voice calls are delegated to the voice module's orchestrator, so the
full compliance gate (including provider readiness and the real
``CALLORA_VOICE_ENABLE_OUTBOUND_CALLS`` switch) applies unchanged.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import InvalidParameterError
from app.modules.recovery_workflow.eligibility import evaluate_eligibility
from app.modules.recovery_workflow.models import (
    FollowUpTask,
    WorkflowAction,
    WorkflowRun,
)
from app.modules.recovery_workflow.rules import RuleEngine
from app.modules.voice import repository as voice_repo
from app.modules.voice.orchestrator import CallOrchestrator, CallRequest

#: Run lifecycle. ``draft`` is only used before the run leaves the
#: creation path; API-visible runs are at least ``awaiting_approval``.
RUN_STATUS_DRAFT = "draft"
RUN_STATUS_AWAITING_APPROVAL = "awaiting_approval"
RUN_STATUS_APPROVED = "approved"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_CANCELLED = "cancelled"
RUN_STATUS_PAUSED = "paused"

TERMINAL_STATUSES = {RUN_STATUS_COMPLETED, RUN_STATUS_FAILED, RUN_STATUS_CANCELLED}
ACTIVE_STATUSES = {RUN_STATUS_APPROVED, RUN_STATUS_RUNNING, RUN_STATUS_PAUSED}
NON_TERMINAL_STATUSES = (
    TERMINAL_STATUSES
    | ACTIVE_STATUSES
    | {RUN_STATUS_DRAFT, RUN_STATUS_AWAITING_APPROVAL}
)

#: Allowed run transitions (same philosophy as the recommendation
#: lifecycle: terminal states are never left).
RUN_TRANSITIONS: Dict[str, set] = {
    RUN_STATUS_DRAFT: {RUN_STATUS_AWAITING_APPROVAL, RUN_STATUS_APPROVED, RUN_STATUS_CANCELLED},
    RUN_STATUS_AWAITING_APPROVAL: {RUN_STATUS_APPROVED, RUN_STATUS_CANCELLED},
    RUN_STATUS_APPROVED: {RUN_STATUS_RUNNING, RUN_STATUS_CANCELLED, RUN_STATUS_PAUSED},
    RUN_STATUS_RUNNING: {RUN_STATUS_COMPLETED, RUN_STATUS_FAILED, RUN_STATUS_PAUSED, RUN_STATUS_CANCELLED},
    RUN_STATUS_PAUSED: {RUN_STATUS_APPROVED, RUN_STATUS_CANCELLED},
    RUN_STATUS_COMPLETED: set(),
    RUN_STATUS_FAILED: set(),
    RUN_STATUS_CANCELLED: set(),
}

#: The step vocabulary. Anything else is rejected at creation time.
STEP_TYPES = {"eligibility_check", "voice_call", "message", "followup_task", "book_appointment"}

#: Default step sequences per workflow type.
DEFAULT_STEPS: Dict[str, List[Dict[str, Any]]] = {
    "recovery_call": [
        {"type": "eligibility_check", "channel": "voice"},
        {"type": "voice_call"},
        {"type": "followup_task", "offset_hours": 48},
    ],
    "recovery_message": [
        {"type": "eligibility_check", "channel": "sms"},
        {"type": "message", "channel": "sms"},
        {"type": "followup_task", "offset_hours": 72},
    ],
    "recovery_booking": [
        {"type": "eligibility_check", "channel": "voice"},
        {"type": "book_appointment"},
        {"type": "followup_task", "offset_hours": 24},
    ],
}

ACTION_STATUS_COMPLETED = "completed"
ACTION_STATUS_BLOCKED = "blocked"
ACTION_STATUS_FAILED = "failed"
ACTION_STATUS_SKIPPED = "skipped"


def prefixed_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowRunError(InvalidParameterError):
    """A run transition or step definition is invalid."""


class WorkflowEngine:
    """Creates, approves and executes workflow runs."""

    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings
        self.rules = RuleEngine(session, business_id="")  # per-call set below

    # -- creation ------------------------------------------------------------

    def create_run(
        self,
        *,
        business_id: str,
        lead_id: str,
        workflow_type: str = "recovery_call",
        channel: Optional[str] = None,
        dry_run: bool = False,
        idempotency_key: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        case_id: Optional[str] = None,
        steps: Optional[List[Dict[str, Any]]] = None,
    ) -> WorkflowRun:
        """Create (or return) one workflow run.

        An ``idempotency_key`` replay returns the existing run untouched.
        Without a key a duplicate in-flight run for the same lead and
        workflow type is also returned instead of duplicated, so a
        double-click can never open two contact attempts.
        """
        self.rules = RuleEngine(self.session, business_id)

        if idempotency_key:
            existing = (
                self.session.execute(
                    select(WorkflowRun).where(
                        WorkflowRun.business_id == business_id,
                        WorkflowRun.idempotency_key == idempotency_key,
                    )
                ).scalars().first()
            )
            if existing is not None:
                return existing

        lead = voice_repo.get_lead(self.session, business_id, lead_id)
        if lead is None:
            from app.errors import LeadNotFoundError

            raise LeadNotFoundError(lead_id)

        step_defs = steps if steps is not None else DEFAULT_STEPS.get(workflow_type)
        if not step_defs:
            raise WorkflowRunError(
                f"unknown workflow_type {workflow_type!r}; provide explicit steps"
            )
        for step in step_defs:
            if step.get("type") not in STEP_TYPES:
                raise WorkflowRunError(
                    f"unsupported step type {step.get('type')!r}; "
                    f"allowed: {sorted(STEP_TYPES)}"
                )

        # Deduplicate in-flight runs for the same (lead, type) unless the
        # caller explicitly asked for a dry run.
        if not dry_run:
            inflight = (
                self.session.execute(
                    select(WorkflowRun).where(
                        WorkflowRun.business_id == business_id,
                        WorkflowRun.lead_id == lead_id,
                        WorkflowRun.workflow_type == workflow_type,
                        WorkflowRun.status.in_(sorted(NON_TERMINAL_STATUSES - TERMINAL_STATUSES)),
                    )
                ).scalars().first()
            )
            if inflight is not None:
                return inflight

        approval_required = bool(
            self.rules.get_value("workflow.require_approval", {"enabled": True}).get(
                "enabled", True
            )
        )
        # A dry run never places a call, sends a message or books an
        # external event, so it does not need operator approval.
        if dry_run:
            approval_required = False

        run = WorkflowRun(
            id=prefixed_id("wfr"),
            business_id=business_id,
            case_id=case_id,
            lead_id=lead_id,
            workflow_type=workflow_type,
            status=(
                RUN_STATUS_AWAITING_APPROVAL if approval_required else RUN_STATUS_APPROVED
            ),
            dry_run=dry_run,
            approval_required=approval_required,
            idempotency_key=idempotency_key,
            input={
                "channel": channel or ("voice" if workflow_type == "recovery_call" else "sms"),
                "params": params or {},
                "steps": step_defs,
            },
        )
        self.session.add(run)
        self.session.flush()
        self._audit(
            business_id,
            action="workflow.run_created",
            entity_id=run.id,
            reason=f"workflow {workflow_type} created",
            metadata={"dry_run": dry_run, "approval_required": approval_required},
        )
        self.session.flush()
        return run

    # -- lifecycle -----------------------------------------------------------

    def approve_run(self, run: WorkflowRun, actor: str) -> WorkflowRun:
        self._transition(run, RUN_STATUS_APPROVED)
        run.approved_by = actor
        run.approved_at = utcnow()
        self._audit(
            run.business_id,
            action="workflow.run_approved",
            entity_id=run.id,
            actor=actor,
            reason="workflow run approved",
        )
        self.session.flush()
        return run

    def cancel_run(self, run: WorkflowRun, actor: str = "api", reason: str = "cancelled") -> WorkflowRun:
        self._transition(run, RUN_STATUS_CANCELLED)
        run.completed_at = utcnow()
        self._audit(
            run.business_id,
            action="workflow.run_cancelled",
            entity_id=run.id,
            actor=actor,
            reason=reason,
        )
        self.session.flush()
        return run

    def pause_run(self, run: WorkflowRun, actor: str = "api") -> WorkflowRun:
        self._transition(run, RUN_STATUS_PAUSED)
        self._audit(
            run.business_id,
            action="workflow.run_paused",
            entity_id=run.id,
            actor=actor,
            reason="workflow run paused",
        )
        self.session.flush()
        return run

    # -- execution -----------------------------------------------------------

    def execute_run(self, run: WorkflowRun, actor: str = "api") -> WorkflowRun:
        """Execute every step of an approved run, append-only.

        The run may only execute from ``approved``; re-executing a
        completed run returns it unchanged. Each step appends exactly one
        :class:`WorkflowAction` row.
        """
        if run.status == RUN_STATUS_COMPLETED:
            return run
        if run.status not in {RUN_STATUS_APPROVED, RUN_STATUS_RUNNING}:
            raise WorkflowRunError(
                f"run is {run.status!r}; only an approved run can execute "
                f"(transition: {sorted(RUN_TRANSITIONS.get(run.status, set())) or 'none'})"
            )
        if run.status == RUN_STATUS_APPROVED:
            self._transition(run, RUN_STATUS_RUNNING)
            run.started_at = run.started_at or utcnow()
            self.session.flush()

        steps: List[Dict[str, Any]] = list((run.input or {}).get("steps") or [])
        results: List[Dict[str, Any]] = []
        blocked = False

        try:
            for index, step in enumerate(steps):
                run.current_step = step.get("type")
                outcome = self._execute_step(run, step, index=index)
                results.append(outcome)
                if outcome["status"] in (ACTION_STATUS_BLOCKED, ACTION_STATUS_FAILED):
                    blocked = True
                    break
        except Exception as exc:  # noqa: BLE001 — the run records, never leaks
            run.status = RUN_STATUS_FAILED
            run.error_message = str(exc)
            run.completed_at = utcnow()
            run.current_step = None
            run.result = {"steps": results, "executed": False, "error": str(exc)}
            self._audit(
                run.business_id,
                action="workflow.run_failed",
                entity_id=run.id,
                actor=actor,
                reason=str(exc),
                decision="failed",
            )
            self.session.flush()
            return run

        run.status = RUN_STATUS_COMPLETED
        run.completed_at = utcnow()
        run.current_step = None
        run.result = {
            "steps": results,
            "executed": True,
            "blocked": blocked,
            "dry_run": run.dry_run,
        }
        self._audit(
            run.business_id,
            action="workflow.run_completed",
            entity_id=run.id,
            actor=actor,
            reason=(
                "workflow run completed with a blocked step"
                if blocked
                else "workflow run completed"
            ),
            decision="blocked" if blocked else "completed",
            metadata={"steps": [s["step"] for s in results]},
        )
        self.session.flush()
        return run

    # -- steps ---------------------------------------------------------------

    def _execute_step(
        self, run: WorkflowRun, step: Dict[str, Any], *, index: int
    ) -> Dict[str, Any]:
        step_type = step.get("type")
        if step_type == "eligibility_check":
            status, response = self._step_eligibility(run, step)
        elif step_type == "voice_call":
            status, response = self._step_voice_call(run, step)
        elif step_type == "message":
            status, response = self._step_message(run, step)
        elif step_type == "followup_task":
            status, response = self._step_followup(run, step)
        elif step_type == "book_appointment":
            status, response = self._step_booking(run, step)
        else:  # pragma: no cover — creation validates the vocabulary
            status, response = ACTION_STATUS_FAILED, {"error": "unknown step"}

        action = WorkflowAction(
            id=prefixed_id("wfa"),
            run_id=run.id,
            business_id=run.business_id,
            step=f"{index}:{step_type}",
            action_type=step_type,
            status=status,
            request={k: v for k, v in step.items() if k != "type"},
            response=response,
            error_message=response.get("error") if status != ACTION_STATUS_COMPLETED else None,
        )
        self.session.add(action)
        self.session.flush()
        return {"step": step_type, "status": status, "response": response}

    def _step_eligibility(
        self, run: WorkflowRun, step: Dict[str, Any]
        ) -> tuple:
        channel = step.get("channel") or (run.input or {}).get("channel") or "voice"
        # A dry run still evaluates and records the real decision —
        # planning must reflect the true compliance picture.
        result = evaluate_eligibility(
            self.session,
            self.settings,
            business_id=run.business_id,
            lead_id=run.lead_id,
            channel=channel,
            case_id=run.case_id,
            persist=True,
        )
        if not result.eligible:
            return ACTION_STATUS_BLOCKED, {
                "reasons": result.reasons,
                "decision_id": result.decision_id,
                "channel": channel,
            }
        return ACTION_STATUS_COMPLETED, {
            "decision_id": result.decision_id,
            "channel": channel,
        }

    def _step_voice_call(self, run: WorkflowRun, step: Dict[str, Any]) -> tuple:
        params = (run.input or {}).get("params") or {}
        orchestrator = CallOrchestrator(self.settings, self.session)
        request = CallRequest(
            business_id=run.business_id,
            lead_id=run.lead_id,
            language=params.get("language") or step.get("language"),
            scheduled_at=None,
            from_number=params.get("from_number"),
            agent_id=params.get("agent_id"),
            extra_context={
                "workflow_run_id": run.id,
                "case_id": run.case_id,
                **(params.get("extra_context") or {}),
            },
            dry_run=bool(run.dry_run),
        )
        result = orchestrator.initiate(request)
        call = result.call
        if not result.evaluation.allowed:
            return ACTION_STATUS_BLOCKED, {
                "call_id": call.id,
                "status": call.status,
                "blocked_reasons": result.evaluation.blocked_reasons,
            }
        return ACTION_STATUS_COMPLETED, {
            "call_id": call.id,
            "status": call.status,
            "simulated": result.simulated,
        }

    def _step_message(self, run: WorkflowRun, step: Dict[str, Any]) -> tuple:
        channel = step.get("channel") or (run.input or {}).get("channel") or "sms"
        # No messaging adapter is implemented. The honest answer is
        # channel_not_configured — the workflow never claims a message
        # was sent when nothing was contacted.
        return ACTION_STATUS_SKIPPED, {
            "channel": channel,
            "reason_code": "channel_not_configured",
            "detail": (
                "no delivery adapter is configured for this channel; "
                "the message was not sent"
            ),
        }

    def _step_followup(self, run: WorkflowRun, step: Dict[str, Any]) -> tuple:
        from datetime import timedelta

        offset_hours = int(step.get("offset_hours") or 48)
        task = FollowUpTask(
            id=prefixed_id("fup"),
            business_id=run.business_id,
            case_id=run.case_id,
            lead_id=run.lead_id,
            workflow_run_id=run.id,
            channel=step.get("channel") or "call",
            reason=step.get("reason") or f"follow-up after {run.workflow_type}",
            due_at=utcnow() + timedelta(hours=offset_hours),
            status="pending",
            priority=step.get("priority"),
        )
        self.session.add(task)
        self.session.flush()
        return ACTION_STATUS_COMPLETED, {"task_id": task.id, "due_at": task.due_at.isoformat()}

    def _step_booking(self, run: WorkflowRun, step: Dict[str, Any]) -> tuple:
        from app.modules.recovery_workflow.appointments import book_appointment

        params = (run.input or {}).get("params") or {}
        start = params.get("scheduled_start")
        if not start:
            return ACTION_STATUS_FAILED, {
                "error": "scheduled_start is required to book an appointment",
                "reason_code": "invalid_request",
            }
        appointment = book_appointment(
            self.session,
            self.settings,
            business_id=run.business_id,
            lead_id=run.lead_id,
            scheduled_start=start,
            case_id=run.case_id,
            workflow_run_id=run.id,
            title=step.get("title") or params.get("title") or "Recovery consultation",
            attendee_name=params.get("attendee_name") or step.get("attendee_name"),
            attendee_phone=params.get("attendee_phone") or step.get("attendee_phone"),
            notes=params.get("booking_notes"),
        )
        if appointment.status == "failed":
            return ACTION_STATUS_FAILED, {
                "appointment_id": appointment.id,
                "status": appointment.status,
                "error": appointment.error_message,
            }
        return ACTION_STATUS_COMPLETED, {
            "appointment_id": appointment.id,
            "status": appointment.status,
            "provider": appointment.provider,
            "provider_event_id": appointment.provider_event_id,
        }

    # -- helpers ---------------------------------------------------------------

    def _transition(self, run: WorkflowRun, target: str) -> None:
        allowed = RUN_TRANSITIONS.get(run.status, set())
        if target not in allowed:
            raise WorkflowRunError(
                f"invalid run transition {run.status!r} -> {target!r}; "
                f"allowed from {run.status!r}: {sorted(allowed) or 'none'}"
            )
        run.status = target
        run.updated_at = utcnow()

    def _audit(
        self,
        business_id: str,
        *,
        action: str,
        entity_id: Optional[str] = None,
        actor: str = "api",
        reason: Optional[str] = None,
        decision: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        voice_repo.add_audit_log(
            self.session,
            action=action,
            business_id=business_id,
            actor=actor,
            entity_type="workflow_run",
            entity_id=entity_id,
            decision=decision,
            reason=reason,
            metadata=metadata or {},
        )


__all__ = [
    "ACTION_STATUS_BLOCKED",
    "ACTION_STATUS_COMPLETED",
    "ACTION_STATUS_FAILED",
    "ACTION_STATUS_SKIPPED",
    "ACTIVE_STATUSES",
    "DEFAULT_STEPS",
    "RUN_STATUS_APPROVED",
    "RUN_STATUS_AWAITING_APPROVAL",
    "RUN_STATUS_CANCELLED",
    "RUN_STATUS_COMPLETED",
    "RUN_STATUS_DRAFT",
    "RUN_STATUS_FAILED",
    "RUN_STATUS_PAUSED",
    "RUN_STATUS_RUNNING",
    "RUN_TRANSITIONS",
    "STEP_TYPES",
    "TERMINAL_STATUSES",
    "WorkflowEngine",
    "WorkflowRunError",
]
