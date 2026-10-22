"""Call orchestration.

The orchestrator is the only component allowed to contact a voice provider.
Its contract is simple and deliberate:

1. evaluate the compliance gate — no exceptions, no bypasses;
2. persist the compliance decision (and the audit trail) *before* the
   provider is contacted;
3. create the call record;
4. place the call through the provider adapter;
5. record the provider identifiers and the resulting status.

It never contains provider-specific logic: everything Bolna-shaped lives in
``providers/bolna.py``.
"""

import time as time_module
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.config import Settings
from app.logging_config import get_logger
from app.modules.voice import repository as repo
from app.modules.voice.compliance import (
    POLICY_VERSION,
    CheckResult,
    ComplianceContext,
    ComplianceEvaluation,
    ComplianceGate,
    DndLookup,
)
from app.modules.voice.constants import (
    AuditAction,
    CallStatus,
    ComplianceDecision,
    ReasonCode,
    map_provider_status,
)
from app.modules.voice.errors import (
    ComplianceBlockedError,
    ProviderConnectionError,
    ProviderError,
)
from app.modules.voice.lifecycle import SOURCE_REFRESH, apply_status, queue_postcall
from app.modules.voice.models import Business, Call, ComplianceDecision as ComplianceDecisionRow
from app.modules.voice.phone import normalize_phone
from app.modules.voice.providers.base import OutboundCallRequest, VoiceProvider
from app.modules.voice.providers.registry import build_provider

logger = get_logger("callora.voice.orchestrator")


@dataclass
class CallRequest:
    """Everything the API needs to place one call."""

    business_id: str
    lead_id: str
    language: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    from_number: Optional[str] = None
    agent_id: Optional[str] = None
    # Extra context merged into the agent prompt variables.
    extra_context: Dict[str, Any] = field(default_factory=dict)
    # Force a simulation even when outbound calling is enabled.
    dry_run: bool = False


@dataclass
class CallInitiationResult:
    """The result of an initiation attempt."""

    call: Call
    evaluation: ComplianceEvaluation
    decision_id: str
    simulated: bool = False


class CallOrchestrator:
    """Places outbound calls, subject to the compliance gate."""

    def __init__(
        self,
        settings: Settings,
        session: Session,
        provider: Optional[VoiceProvider] = None,
        dnd_lookup: Optional[DndLookup] = None,
    ) -> None:
        self._settings = settings
        self._session = session
        self._provider = provider
        self._dnd_lookup = dnd_lookup

    # -- public API --------------------------------------------------------

    def initiate(
        self, request: CallRequest, now: Optional[datetime] = None
    ) -> CallInitiationResult:
        """Run the gate and, when it allows, place the call."""
        now = now or datetime.now(timezone.utc)
        business = repo.get_business(self._session, request.business_id)
        lead = (
            repo.get_lead(self._session, request.business_id, request.lead_id)
            if business is not None
            else None
        )
        provider = self._provider or build_provider(settings=self._settings)

        # Whether the provider will actually be contacted. The gate needs to
        # know this: for a simulated call the provider is never reached, so
        # ``provider_ready`` is skipped instead of blocking.
        simulated = bool(request.dry_run) or not self._settings.voice_enable_outbound_calls

        evaluation = self._evaluate(business, lead, provider, now, simulated=simulated)
        decision = self._persist_decision(evaluation, request, business, lead)
        call = self._create_call(request, business, lead, evaluation, decision.id)

        if not evaluation.allowed:
            blocked = evaluation.blocked_reasons
            reason_codes = [r.get("reason_code") for r in blocked]
            repo.update_call(
                self._session,
                call,
                {
                    "status": CallStatus.BLOCKED,
                    "blocked_reason": ",".join(
                        code for code in reason_codes if code
                    ) or "compliance_blocked",
                },
            )
            repo.add_audit_log(
                self._session,
                action=AuditAction.CALL_BLOCKED,
                business_id=request.business_id,
                actor="compliance_gate",
                entity_type="call",
                entity_id=call.id,
                decision=ComplianceDecision.BLOCKED,
                reason_code=call.blocked_reason,
                reason="the call was blocked before it could be placed",
                metadata={"checks": [c.to_dict() for c in evaluation.checks]},
            )
            self._session.commit()
            raise ComplianceBlockedError(blocked, decision_id=decision.id)

        # Allowed: place the call.
        if simulated:
            reason = (
                "the request was a dry run"
                if request.dry_run
                else "outbound calling is disabled for this deployment"
            )
            return self._simulate(call, request, evaluation, decision.id, reason=reason)

        try:
            provider_request = self._build_provider_request(
                request, business, lead, now, call_id=call.id
            )
            result = self._start_call_with_retry(provider, provider_request)
        except ProviderConnectionError as exc:
            self._fail_call(call, request, str(exc))
            raise
        except ProviderError as exc:
            self._fail_call(call, request, str(exc))
            raise

        # The provider accepted the call. A status Callora does not know
        # says nothing about the call, so the safe assumption is that it
        # is waiting in the dial queue.
        status = result.status or CallStatus.QUEUED
        if status == CallStatus.UNKNOWN:
            status = CallStatus.QUEUED

        repo.update_call(
            self._session,
            call,
            {
                "provider_call_id": result.execution_id,
                "provider_agent_id": provider_request.agent_id,
                "status": status,
                "initiated_at": now,
                "provider_payload": result.raw,
                "error_message": None,
            },
        )
        repo.add_audit_log(
            self._session,
            action=AuditAction.CALL_INITIATED,
            business_id=request.business_id,
            entity_type="call",
            entity_id=call.id,
            decision="allow",
            reason_code="compliance_passed",
            reason="the call was placed with the voice provider",
            metadata={
                "provider": provider.name,
                "provider_call_id": result.execution_id,
                "status": result.status,
            },
        )
        self._session.commit()
        logger.info(
            "call_initiated",
            extra={
                "call_id": call.id,
                "provider_call_id": result.execution_id,
                "execution_id": result.execution_id,
                "call_status": call.status,
                "provider": provider.name,
            },
        )
        return CallInitiationResult(
            call=call, evaluation=evaluation, decision_id=decision.id
        )

    def refresh_status(
        self, call: Call, provider: Optional[VoiceProvider] = None
    ) -> Call:
        """Pull the current state of a call from the provider.

        This is the recovery path for a webhook that never arrived. The
        provider is asked for the authoritative status and the answer is
        applied with the shared state machine, so a refresh can never move
        a call backwards and a call that turns out to be over is queued
        for the post-call analysis exactly like a webhook would have done.
        """
        if not call.provider_call_id:
            return call
        provider = provider or self._provider or build_provider(settings=self._settings)
        result = provider.get_call(call.provider_call_id)
        raw_status = (result.raw or {}).get("status")
        internal_status = map_provider_status(result.status or raw_status or "")
        update = apply_status(
            self._session,
            call,
            internal_status,
            result.raw,
            source=SOURCE_REFRESH,
            raw_status=raw_status,
        )
        if update.applied and update.changed:
            queue_postcall(self._session, self._settings, call)
        return call

    # -- internals ---------------------------------------------------------

    def _evaluate(
        self,
        business: Optional[Business],
        lead,
        provider: VoiceProvider,
        now: datetime,
        simulated: bool = False,
    ) -> ComplianceEvaluation:
        if business is None:
            return ComplianceEvaluation(
                decision=ComplianceDecision.BLOCKED,
                checks=[
                    CheckResult(
                        name="business_active",
                        outcome="failed",
                        mandatory=True,
                        reason_code=ReasonCode.BUSINESS_NOT_FOUND,
                        reason="the business configuration does not exist",
                    )
                ],
            )
        gate = ComplianceGate(
            self._settings, self._session, dnd_lookup=self._dnd_lookup
        )
        profile = repo.get_lead_profile(self._session, business.id, lead.id) if lead else None
        ctx = ComplianceContext(
            business=business,
            lead=lead,
            provider=provider,
            now=now,
            profile=profile,
            phone=normalize_phone(lead.phone) if lead is not None else None,
            simulated=simulated,
        )
        return gate.evaluate(ctx)

    def _persist_decision(
        self,
        evaluation: ComplianceEvaluation,
        request: CallRequest,
        business: Optional[Business],
        lead,
    ) -> ComplianceDecisionRow:
        decision = repo.create_compliance_decision(
            self._session,
            ComplianceDecisionRow(
                business_id=request.business_id,
                lead_id=request.lead_id,
                decision=evaluation.decision,
                checks=[c.to_dict() for c in evaluation.checks],
                blocked_reasons=evaluation.blocked_reasons,
                policy_version=POLICY_VERSION,
            ),
        )
        repo.add_audit_log(
            self._session,
            action=AuditAction.CALL_REQUESTED,
            business_id=request.business_id,
            actor="api",
            entity_type="compliance_decision",
            entity_id=decision.id,
            decision=evaluation.decision,
            reason_code=(
                None
                if evaluation.allowed
                else ",".join(
                    r.get("reason_code") or "unknown"
                    for r in evaluation.blocked_reasons
                )
            ),
            reason="the compliance gate evaluated an outbound call request",
            metadata={
                "lead_id": request.lead_id,
                "checks": [c.to_dict() for c in evaluation.checks],
            },
        )
        self._session.flush()
        return decision

    def _create_call(
        self,
        request: CallRequest,
        business: Optional[Business],
        lead,
        evaluation: ComplianceEvaluation,
        decision_id: str,
    ) -> Call:
        call = repo.create_call(
            self._session,
            Call(
                business_id=request.business_id,
                lead_id=request.lead_id,
                provider=(self._provider.name if self._provider else "bolna"),
                status=CallStatus.PENDING,
                to_number=normalize_phone(lead.phone) if lead is not None else None,
                from_number=self._resolve_from_number(request),
                language=self._resolve_language(request, business, lead),
                scheduled_at=request.scheduled_at,
                compliance_decision_id=decision_id,
                call_metadata={
                    "requested_at": datetime.now(timezone.utc).isoformat(),
                    "extra_context": request.extra_context or {},
                },
            ),
        )
        self._session.flush()
        return call

    def _simulate(
        self,
        call: Call,
        request: CallRequest,
        evaluation: ComplianceEvaluation,
        decision_id: str,
        reason: str = "outbound calling is disabled for this deployment",
    ) -> CallInitiationResult:
        """Record an allowed call without contacting the provider."""
        metadata = dict(call.call_metadata or {})
        metadata.update({"simulated": True, "reason": reason})
        repo.update_call(
            self._session,
            call,
            {
                "status": CallStatus.SIMULATED,
                "call_metadata": metadata,
                "compliance_decision_id": decision_id,
            },
        )
        repo.add_audit_log(
            self._session,
            action=AuditAction.CALL_INITIATED,
            business_id=request.business_id,
            entity_type="call",
            entity_id=call.id,
            decision="allow",
            reason_code="simulated",
            reason="the call was allowed but no provider was contacted",
            metadata={"simulated": True},
        )
        self._session.commit()
        logger.info(
            "call_simulated",
            extra={"call_id": call.id},
        )
        return CallInitiationResult(
            call=call, evaluation=evaluation, decision_id=decision_id, simulated=True
        )

    def _fail_call(self, call: Call, request: CallRequest, message: str) -> None:
        repo.update_call(
            self._session,
            call,
            {
                "status": CallStatus.ERROR,
                "error_message": message,
                "ended_at": datetime.now(timezone.utc),
            },
        )
        repo.add_audit_log(
            self._session,
            action=AuditAction.CALL_PROVIDER_ERROR,
            business_id=request.business_id,
            entity_type="call",
            entity_id=call.id,
            decision="allow",
            reason_code="provider_error",
            reason=message,
        )
        self._session.commit()

    def _resolve_from_number(self, request: CallRequest) -> Optional[str]:
        """Resolve the caller number of one call.

        The caller number is optional and is **never** defaulted. Only the
        number the request itself carries is used; it is normalised to
        E.164 — the format Bolna requires.

        When the request carries no number the result is ``None``: the
        provider request then has no caller-number field at all and Bolna
        places the call through the telephony provider of the agent.

        There is deliberately no deployment-wide or business-wide fallback.
        A number that the provider account does not know (``Calling
        from_number ... doesn't exist for <provider>``) would fail every
        call, so it must never be injected behind the caller's back.
        """
        return normalize_phone(request.from_number)

    def _resolve_language(
        self, request: CallRequest, business: Optional[Business], lead
    ) -> Optional[str]:
        if request.language:
            return request.language.strip().lower()
        if business and business.default_language:
            return business.default_language.strip().lower()
        if lead is not None and lead.language:
            return lead.language.strip().lower()
        return None

    def _build_provider_request(
        self,
        request: CallRequest,
        business: Optional[Business],
        lead,
        now: datetime,
        call_id: Optional[str] = None,
    ) -> OutboundCallRequest:
        agent_id = (
            request.agent_id
            or (business.bolna_agent_id if business else None)
            or self._settings.bolna_default_agent_id
            or ""
        )
        language = self._resolve_language(request, business, lead)
        user_data = self._build_user_data(request, business, lead, language, call_id=call_id)
        metadata: Dict[str, Any] = {
            "provider_requested_at": now.isoformat(),
        }
        return OutboundCallRequest(
            to_number=normalize_phone(lead.phone) if lead is not None else "",
            agent_id=agent_id,
            from_number=self._resolve_from_number(request),
            language=language,
            user_data=user_data,
            scheduled_at=request.scheduled_at,
            metadata=metadata,
        )

    def _build_user_data(
        self,
        request: CallRequest,
        business: Optional[Business],
        lead,
        language: Optional[str],
        call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Variables injected into the Bolna agent prompt.

        The names are stable and documented so a Bolna agent can reference
        them (``{{lead_name}}``, ``{{qualification_questions}}``, ...).
        """
        questions: List[Dict[str, Any]] = []
        if business is not None and business.qualification_questions:
            questions = list(business.qualification_questions)

        question_lines = []
        for item in questions:
            if isinstance(item, dict):
                text = item.get("question") or item.get("text") or ""
                key = item.get("key") or item.get("id") or ""
                if text:
                    question_lines.append(f"{key}: {text}" if key else text)
            elif isinstance(item, str):
                question_lines.append(item)

        user_data: Dict[str, Any] = {
            "call_id": call_id or "",
            "business_id": (business.id if business is not None else request.business_id) or "",
            "lead_name": (lead.name if lead is not None else "") or "",
            "lead_phone": (lead.phone if lead is not None else "") or "",
            "business_name": (business.name if business is not None else "") or "",
            "call_language": language or "",
            "qualification_questions": "\n".join(question_lines),
            "business_instructions": (business.agent_instructions if business else "") or "",
        }
        if lead is not None:
            if lead.custom_fields:
                user_data["lead_context"] = ", ".join(
                    f"{k}: {v}" for k, v in lead.custom_fields.items()
                )
            if lead.deal_value is not None:
                user_data["deal_value"] = str(lead.deal_value)
        if request.extra_context:
            for key, value in request.extra_context.items():
                user_data[key] = value
        return {k: v for k, v in user_data.items() if v != ""}

    def _start_call_with_retry(
        self, provider: VoiceProvider, provider_request: OutboundCallRequest
    ):
        """Place the call, retrying only when the provider was unreachable.

        A timeout or a provider-side error is *not* retried automatically:
        the request may have been received, and retrying could place a
        second billable call. Those failures are recorded and the operator
        can retry through the API (which re-runs the compliance gate).
        """
        attempts = max(1, self._settings.voice_provider_max_attempts)
        last_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                return provider.start_call(provider_request)
            except ProviderConnectionError as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                delay = self._settings.voice_provider_retry_backoff_seconds * attempt
                logger.warning(
                    "provider_unreachable_retrying",
                    extra={"attempt": attempt, "delay_seconds": delay},
                )
                if delay:
                    time_module.sleep(delay)
                continue
        assert last_error is not None
        raise last_error
