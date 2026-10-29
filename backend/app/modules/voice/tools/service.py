"""Business logic behind the Bolna custom functions.

Six live tool calls the voice agent may make during (or right after) a
conversation, all implemented on top of the existing Callora components —
the canonical lead table, the voice repository, the compliance gate, the
handoff store, the background job queue and the CRM synchronization
service. Nothing here opens a second database or a second engine.

Rules that hold for every function:

* **Authorization is derived, never trusted.** The business comes from the
  bearer token (``tools.security``); a ``business_id`` in the body is only
  a cross-check; a provider execution id resolves the persisted call and
  its business is authoritative.
* **The LLM chooses values from a closed vocabulary** (the outcome enum);
  it never names database fields and can never issue an arbitrary update.
* **Lead status moves forward, never backwards** — the priority ranking
  below decides whether a status is applied, and every suppressed
  transition is audited.
* **Every mutation is idempotent.** Repeated tool calls with the same
  event key replay the stored result instead of duplicating work.
* **Failures are honest.** A CRM sync is only reported successful when
  the external system confirmed it; a callback is only reported scheduled
  when automatic dispatch actually exists and is enabled.
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import AppError, InvalidParameterError, LeadNotFoundError
from app.modules.voice import repository as repo
from app.modules.voice import compliance as compliance_mod
from app.modules.voice.constants import AuditAction, HandoffKind, HandoffStatus, JobType
from app.modules.voice.errors import CrossBusinessAccessError
from app.modules.voice.handoff.service import HandoffService
from app.modules.voice.models import Business, Call, VoiceOpportunity, VoiceOpportunityStatus
from app.modules.voice.tools import qualification as qual
from app.modules.voice.tools.schemas import (
    CreateOpportunityRequest,
    CrmSyncToolRequest,
    LeadDetailsRequest,
    QualifyLeadRequest,
    SaveOutcomeRequest,
    ScheduleCallbackRequest,
    ToolOutcome,
    tool_success,
)
from app.modules.voice.tools.security import ToolIdentity, verify_call_context

logger = logging.getLogger("callora.voice.tools")

#: Idempotency scopes used by the tool endpoints (shared idempotency table).
SCOPE_OUTCOME = "voice.tool.outcome"
SCOPE_CALLBACK = "voice.tool.callback"
SCOPE_OPPORTUNITY = "voice.tool.opportunity"

#: How far ahead a callback may be requested.
DEFAULT_MAX_CALLBACK_DAYS = 30


class LeadStatusPriority:
    """Monotonic priority of the statuses the voice tools may set.

    The canonical ``leads`` table keeps no status column; the status lives
    on the ``agent_leads`` projection (Module 2 / CRM synchronization read
    it from there). The ranking orders a lifecycle: a decisive result is
    never overwritten by a transient one, and a conversion is never
    demoted. A status Callora does not know (set by an operator or an
    external system) is never overwritten by the voice agent at all.
    """

    PRIORITY: Dict[str, int] = {
        "new": 0,
        "contacted": 1,
        "no_answer": 1,
        "busy": 1,
        "failed": 1,
        "callback_requested": 2,
        "interested": 2,
        "not_interested": 2,
        "unqualified": 2,
        "qualified": 3,
        "opportunity_created": 3,
        "lost": 3,
        "recovered": 3,
        "converted": 4,
    }

    #: What the tool outcome maps to on the lead.
    OUTCOME_TO_STATUS: Dict[str, str] = {
        ToolOutcome.INTERESTED.value: "interested",
        ToolOutcome.NOT_INTERESTED.value: "not_interested",
        ToolOutcome.CALLBACK_REQUESTED.value: "callback_requested",
        ToolOutcome.QUALIFIED.value: "qualified",
        ToolOutcome.UNQUALIFIED.value: "unqualified",
        ToolOutcome.NO_ANSWER.value: "no_answer",
        ToolOutcome.BUSY.value: "busy",
        ToolOutcome.FAILED.value: "failed",
    }

    #: What the tool outcome maps to in the existing disposition vocabulary
    #: (``constants.Disposition``), where a direct counterpart exists.
    OUTCOME_TO_DISPOSITION: Dict[str, str] = {
        ToolOutcome.INTERESTED.value: "interested",
        ToolOutcome.QUALIFIED.value: "qualified",
        ToolOutcome.CALLBACK_REQUESTED.value: "callback_requested",
        ToolOutcome.NOT_INTERESTED.value: "refused",
        ToolOutcome.UNQUALIFIED.value: "not_qualified",
        ToolOutcome.NO_ANSWER.value: "no_conversation",
        ToolOutcome.BUSY.value: "no_conversation",
        ToolOutcome.FAILED.value: "no_conversation",
    }

    @classmethod
    def rank(cls, status: Optional[str]) -> Optional[int]:
        if not status:
            return None
        return cls.PRIORITY.get(status.strip().lower())

    @classmethod
    def may_apply(cls, current: Optional[str], new: str) -> Tuple[bool, str]:
        """Whether ``new`` may overwrite ``current``.

        Returns ``(allowed, reason)`` — the reason is recorded in the
        audit trail when the transition is suppressed.
        """
        current_rank = cls.rank(current)
        new_rank = cls.rank(new)
        if new_rank is None:
            return False, f"the status '{new}' is not a managed voice status"
        if current is None or not current.strip():
            return True, "the lead had no status yet"
        if current_rank is None:
            return (
                False,
                (
                    f"the current status '{current}' is not managed by the "
                    "voice agent; it was left untouched"
                ),
            )
        if new_rank < current_rank:
            return (
                False,
                (
                    f"the status '{new}' ranks below the current '{current}' "
                    "and was not applied"
                ),
            )
        return True, f"the status changed from '{current}' to '{new}'"


def payload_hash(body: Dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class VoiceToolService:
    """Implements the six Bolna custom functions."""

    def __init__(self, settings: Settings, session: Session) -> None:
        self._settings = settings
        self._session = session

    # -- shared helpers ----------------------------------------------------

    def _lead_or_404(self, business_id: str, lead_id: str):
        lead = repo.get_lead(self._session, business_id, lead_id)
        if lead is None:
            raise LeadNotFoundError(lead_id)
        return lead

    def _business(self, business_id: str) -> Business:
        business = repo.get_business(self._session, business_id)
        if business is None:
            raise CrossBusinessAccessError(
                f"business '{business_id}' has no voice configuration"
            )
        return business

    def _idempotency_replay(
        self,
        business_id: str,
        scope: str,
        key: str,
        hash_value: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Return the replay payload when this exact event was processed.

        A repeated request with the *same* payload replays; the same key
        with a *different* payload is a conflict (the client is trying to
        rewrite an already-processed event).
        """
        record = repo.get_idempotency_record(self._session, business_id, scope, key)
        if record is None:
            return None
        if hash_value is not None and record.payload_hash and record.payload_hash != hash_value:
            from app.modules.voice.errors import IdempotencyConflictError

            raise IdempotencyConflictError(key)
        return {"replayed": True}

    def _store_idempotency(
        self,
        business_id: str,
        scope: str,
        key: str,
        hash_value: Optional[str],
    ) -> None:
        repo.store_idempotency_record(
            self._session, business_id, scope, key, payload_hash=hash_value
        )

    def _apply_status(
        self,
        lead,
        new_status: str,
        audit_metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Apply a lead status under the monotonic rule (audited either way)."""
        current = None
        agent_lead = repo.get_agent_lead(self._session, lead.id)
        if agent_lead is not None:
            current = agent_lead.status
        allowed, reason = LeadStatusPriority.may_apply(current, new_status)
        if allowed:
            applied, previous = repo.apply_lead_status(self._session, lead, new_status)
            repo.add_audit_log(
                self._session,
                action=AuditAction.TOOL_OUTCOME_SAVED,
                business_id=lead.business_id,
                actor="voice_tool",
                entity_type="lead",
                entity_id=lead.id,
                decision="allow",
                reason_code=f"lead_status_{new_status}",
                reason=reason,
                metadata=audit_metadata,
            )
            return {"applied": True, "status": new_status, "previous": previous, "reason": reason}
        repo.add_audit_log(
            self._session,
            action=AuditAction.TOOL_STATUS_PRESERVED,
            business_id=lead.business_id,
            actor="voice_tool",
            entity_type="lead",
            entity_id=lead.id,
            decision="block",
            reason_code="status_not_downgraded",
            reason=reason,
            metadata={**audit_metadata, "requested_status": new_status},
        )
        return {"applied": False, "status": current, "previous": current, "reason": reason}

    def _evaluate_qualification(
        self,
        business: Business,
        answers: Optional[Dict[str, Any]],
    ) -> qual.QualificationResult:
        merged: Dict[str, Any] = dict(answers or {})
        return qual.evaluate_qualification(
            list(business.qualification_questions or []),
            merged,
            min_score=self._settings.voice_qualification_min_score,
        )

    # -- 1. lead details ---------------------------------------------------

    def get_lead_details(self, identity: ToolIdentity, request: LeadDetailsRequest) -> Dict[str, Any]:
        """Return only what the conversation needs about the lead."""
        call = verify_call_context(
            self._session, identity, request.execution_id, request.call_id
        )
        lead = self._lead_or_404(identity.business_id, request.lead_id)
        business = self._business(identity.business_id)
        agent_lead = repo.get_agent_lead(self._session, lead.id)
        profile = repo.get_lead_profile(self._session, lead.business_id, lead.id)

        question_keys = [
            str(q.get("key"))
            for q in (business.qualification_questions or [])
            if isinstance(q, dict) and q.get("key")
        ]
        known_fields = {
            key: lead.custom_fields[key]
            for key in question_keys
            if key in (lead.custom_fields or {})
        }

        latest_outcome = repo.get_latest_outcome_for_lead(
            self._session, lead.business_id, lead.id
        )

        data = {
            "lead_id": lead.id,
            "name": lead.name,
            "language": lead.language or (business.default_language if business else None),
            "preferred_language": profile.preferred_language if profile else None,
            "status": (agent_lead.status if agent_lead else None) or "new",
            "deal_value": float(lead.deal_value) if lead.deal_value is not None else None,
            "source": lead.source,
            "qualification_questions": [
                {
                    "key": q.get("key"),
                    "question": q.get("question"),
                    "type": q.get("type", "text"),
                    "required": bool(q.get("required")),
                }
                for q in (business.qualification_questions or [])
                if isinstance(q, dict) and q.get("key")
            ],
            "known_fields": known_fields,
            "previous_outcome": (
                {
                    "disposition": latest_outcome.disposition,
                    "summary": (latest_outcome.summary or "")[:300],
                    "at": latest_outcome.processed_at.isoformat(),
                }
                if latest_outcome
                else None
            ),
        }
        repo.add_audit_log(
            self._session,
            action=AuditAction.TOOL_LEAD_READ,
            business_id=lead.business_id,
            actor="voice_tool",
            entity_type="lead",
            entity_id=lead.id,
            reason_code="lead_details_read",
            reason="the voice agent read the lead for an authorized conversation",
            metadata={
                "lead_id": lead.id,
                "execution_id": request.execution_id,
                "call_id": (call.id if call is not None else None),
            },
        )
        self._session.commit()
        return tool_success(data, "Lead details retrieved")

    # -- 2. call outcome ---------------------------------------------------

    def save_call_outcome(self, identity: ToolIdentity, request: SaveOutcomeRequest) -> Dict[str, Any]:
        """Persist what the conversation established, transactionally."""
        call = verify_call_context(
            self._session, identity, request.execution_id, request.call_id
        )
        lead = self._lead_or_404(identity.business_id, request.lead_id)
        business = self._business(identity.business_id)

        body = request.model_dump(mode="json")
        hash_value = payload_hash(body)
        event_key = (
            request.execution_id
            or request.call_id
            or f"lead:{lead.id}:{request.outcome.value}"
        )
        replay = self._idempotency_replay(identity.business_id, SCOPE_OUTCOME, event_key, hash_value)
        if replay is not None:
            data = {
                "lead_id": lead.id,
                "outcome": request.outcome.value,
                "lead_status": (
                    (repo.get_agent_lead(self._session, lead.id).status)
                    if repo.get_agent_lead(self._session, lead.id)
                    else None
                ),
                **replay,
            }
            return tool_success(data, "the call outcome was already recorded")

        # Validate the callback time *before* anything is saved, so an
        # invalid request never produces a half-written outcome.
        callback_time = request.callback_time
        if callback_requested_now(request) and callback_time is not None:
            self._validate_callback_time(callback_time)

        qualification = None
        if request.qualification_answers is not None:
            qualification = self._evaluate_qualification(business, request.qualification_answers)

        audit_metadata = {
            "lead_id": lead.id,
            "call_id": (call.id if call is not None else None),
            "execution_id": request.execution_id,
            "bolna_call_id": request.execution_id,
            "outcome": request.outcome.value,
            "customer_interest": (
                request.customer_interest.value if request.customer_interest else None
            ),
        }

        lead_fields: Dict[str, Any] = {
            "voice_last_outcome": request.outcome.value,
            "voice_last_outcome_at": datetime.now(timezone.utc).isoformat(),
        }
        if request.execution_id:
            lead_fields["voice_bolna_call_id"] = request.execution_id
        if request.summary:
            lead_fields["voice_call_summary"] = request.summary
        if qualification is not None:
            lead_fields["voice_qualification_status"] = qualification.status
            lead_fields["voice_qualification_answers"] = qualification.answers
        repo.append_lead_custom_fields(self._session, lead, lead_fields)

        # Lead status: "qualified" is only accepted when the rule engine
        # agrees; the LLM can never promote itself past the rules.
        target_status = LeadStatusPriority.OUTCOME_TO_STATUS[request.outcome.value]
        if request.outcome == ToolOutcome.QUALIFIED and (
            qualification is None or qualification.status != qual.STATUS_QUALIFIED
        ):
            target_status = "interested"
            if qualification is not None:
                audit_metadata["qualification_status"] = qualification.status
        status_result = self._apply_status(lead, target_status, audit_metadata)

        callback_result: Optional[Dict[str, Any]] = None
        if callback_requested_now(request):
            callback_result = self._create_callback_for_outcome(
                identity, lead, request, call
            )

        if call is not None:
            self._record_outcome_on_call(call, lead, request, qualification, callback_time)

        repo.add_audit_log(
            self._session,
            action=AuditAction.TOOL_OUTCOME_SAVED,
            business_id=lead.business_id,
            actor="voice_tool",
            entity_type="lead",
            entity_id=lead.id,
            decision="allow",
            reason_code=f"outcome_{request.outcome.value}",
            reason="the voice agent saved the call outcome",
            metadata={**audit_metadata, "lead_status": status_result},
        )

        self._store_idempotency(identity.business_id, SCOPE_OUTCOME, event_key, hash_value)
        self._session.commit()

        data = {
            "lead_id": lead.id,
            "outcome": request.outcome.value,
            "lead_status": status_result["status"],
            "lead_status_applied": status_result["applied"],
        }
        if qualification is not None:
            data["qualification"] = qualification.to_dict()
        if callback_result is not None:
            data["callback"] = callback_result
        return tool_success(data, "Lead outcome saved successfully")

    def _record_outcome_on_call(
        self,
        call: Call,
        lead,
        request: SaveOutcomeRequest,
        qualification: Optional[qual.QualificationResult],
        callback_time: Optional[datetime],
    ) -> None:
        """Attach the live outcome to the persisted call and its memory."""
        disposition = LeadStatusPriority.OUTCOME_TO_DISPOSITION[request.outcome.value]
        outcome = repo.get_outcome(self._session, call.id)
        if outcome is None:
            from app.modules.voice.models import CallOutcome

            outcome = CallOutcome(
                business_id=call.business_id,
                call_id=call.id,
                lead_id=lead.id,
                engine="voice-tool",
            )
        outcome.disposition = disposition
        if request.summary:
            outcome.summary = request.summary
        if qualification is not None:
            outcome.qualified = qualification.qualified
            outcome.qualification_score = qualification.score
            outcome.qualification_answers = qualification.answers
            outcome.field_confidence = {
                "source": "live_conversation",
                "unknown_keys": qualification.unknown_keys,
            }
        repo.upsert_outcome(self._session, outcome)

        metadata = dict(call.call_metadata or {})
        metadata["live_outcome"] = request.outcome.value
        metadata["live_outcome_at"] = datetime.now(timezone.utc).isoformat()
        if callback_time is not None:
            metadata["callback_requested_at"] = callback_time.isoformat()
        repo.update_call(self._session, call, {"call_metadata": metadata})

        memory = repo.get_or_create_call_memory(
            self._session,
            call.business_id,
            call.id,
            lead_id=lead.id,
            language=call.language or "en",
        )
        if qualification is not None:
            fields = dict(memory.qualification_fields or {})
            for key, value in qualification.answers.items():
                if value is not None:
                    fields[key] = value
            pending = [
                key
                for key in (memory.pending_qualification_keys or [])
                if fields.get(key) is None
            ]
            repo.update_call_memory(
                self._session,
                memory,
                {"qualification_fields": fields, "pending_qualification_keys": pending},
            )

    # -- 3. qualification --------------------------------------------------

    def qualify_lead(self, identity: ToolIdentity, request: QualifyLeadRequest) -> Dict[str, Any]:
        call = verify_call_context(
            self._session, identity, request.execution_id, request.call_id
        )
        lead = self._lead_or_404(identity.business_id, request.lead_id)
        business = self._business(identity.business_id)

        merged: Dict[str, Any] = {}
        if call is not None:
            memory = repo.get_call_memory(self._session, call.id)
            if memory is not None:
                merged.update(memory.qualification_fields or {})
        for key, value in (request.answers or {}).items():
            if not qual_is_unknown(value):
                merged[key] = value

        result = self._evaluate_qualification(business, merged)

        status_result: Optional[Dict[str, Any]] = None
        if result.status == qual.STATUS_QUALIFIED:
            status_result = self._apply_status(
                lead,
                "qualified",
                {
                    "lead_id": lead.id,
                    "execution_id": request.execution_id,
                    "qualification_status": result.status,
                    "score": result.score,
                },
            )

        if call is not None:
            memory = repo.get_or_create_call_memory(
                self._session, call.business_id, call.id, lead_id=lead.id,
                language=call.language or "en",
            )
            fields = dict(memory.qualification_fields or {})
            for key, value in merged.items():
                fields[key] = value
            pending = [
                key
                for key in (memory.pending_qualification_keys or [])
                if fields.get(key) is None
            ]
            repo.update_call_memory(
                self._session,
                memory,
                {"qualification_fields": fields, "pending_qualification_keys": pending},
            )

        repo.add_audit_log(
            self._session,
            action=AuditAction.TOOL_QUALIFICATION,
            business_id=lead.business_id,
            actor="voice_tool",
            entity_type="lead",
            entity_id=lead.id,
            decision="allow" if result.status == qual.STATUS_QUALIFIED else result.status,
            reason_code=f"qualification_{result.status}",
            reason="; ".join(result.reasons)[:500],
            metadata={
                "lead_id": lead.id,
                "score": result.score,
                "unknown_keys": result.unknown_keys,
                "lead_status": status_result,
            },
        )
        self._session.commit()

        data: Dict[str, Any] = {
            "lead_id": lead.id,
            "qualification": result.to_dict(),
        }
        if status_result is not None:
            data["lead_status"] = status_result["status"]
        return tool_success(data, f"Qualification evaluated: {result.status}")

    # -- 4. opportunities --------------------------------------------------

    def create_opportunity(self, identity: ToolIdentity, request: CreateOpportunityRequest) -> Dict[str, Any]:
        if request.force and identity.method != "api_key":
            raise CrossBusinessAccessError(
                "creating an opportunity without passing the qualification "
                "rules requires a business API key"
            )
        call = verify_call_context(
            self._session, identity, request.execution_id, request.call_id
        )
        lead = self._lead_or_404(identity.business_id, request.lead_id)
        business = self._business(identity.business_id)

        event_key = request.execution_id or request.call_id or f"lead:{lead.id}"

        existing = repo.get_opportunity_by_event(
            self._session, identity.business_id, lead.id, event_key
        )
        if existing is not None:
            return tool_success(
                {
                    "lead_id": lead.id,
                    "opportunity_id": existing.id,
                    "status": existing.status,
                    "created": False,
                },
                "an opportunity for this lead and event already exists",
            )

        if request.force:
            result = None
        else:
            merged: Dict[str, Any] = {}
            if call is not None:
                memory = repo.get_call_memory(self._session, call.id)
                if memory is not None:
                    merged.update(memory.qualification_fields or {})
            for key, value in (request.answers or {}).items():
                if not qual_is_unknown(value):
                    merged[key] = value
            if not merged:
                latest = repo.get_latest_outcome_for_lead(
                    self._session, lead.business_id, lead.id
                )
                if latest is not None:
                    merged = dict(latest.qualification_answers or {})
            result = self._evaluate_qualification(business, merged)
            if result.status != qual.STATUS_QUALIFIED:
                return {
                    "success": False,
                    "error": {
                        "code": "QUALIFICATION_NOT_MET",
                        "message": (
                            "the lead did not satisfy the business's "
                            "qualification criteria, so no opportunity was "
                            f"created ({result.status})"
                        ),
                        "details": {"qualification": result.to_dict()},
                    },
                }

        opportunity = VoiceOpportunity(
            business_id=lead.business_id,
            lead_id=lead.id,
            call_id=(call.id if call is not None else None),
            provider_call_id=request.execution_id,
            source_event_key=event_key[:128],
            source="voice",
            status=VoiceOpportunityStatus.OPEN,
            qualification_result=(result.to_dict() if result else {"forced": True}),
            qualification_score=(result.score if result else None),
        )
        try:
            repo.create_opportunity(self._session, opportunity)
        except IntegrityError:
            # A concurrent request created the same opportunity first.
            self._session.rollback()
            existing = repo.get_opportunity_by_event(
                self._session, identity.business_id, lead.id, event_key
            )
            if existing is None:
                raise
            return tool_success(
                {
                    "lead_id": lead.id,
                    "opportunity_id": existing.id,
                    "status": existing.status,
                    "created": False,
                },
                "an opportunity for this lead and event already exists",
            )

        status_result = self._apply_status(
            lead,
            "opportunity_created",
            {
                "lead_id": lead.id,
                "opportunity_id": opportunity.id,
                "execution_id": request.execution_id,
            },
        )
        repo.add_audit_log(
            self._session,
            action=AuditAction.TOOL_OPPORTUNITY,
            business_id=lead.business_id,
            actor="voice_tool",
            entity_type="voice_opportunity",
            entity_id=opportunity.id,
            decision="allow",
            reason_code="opportunity_created",
            reason=(
                "an opportunity was created from a qualified voice conversation"
                if result is not None
                else "an opportunity was created by explicit operator request"
            ),
            metadata={
                "lead_id": lead.id,
                "call_id": (call.id if call is not None else None),
                "qualification": (result.to_dict() if result else {"forced": True}),
                "lead_status": status_result,
            },
        )
        self._session.commit()

        return tool_success(
            {
                "lead_id": lead.id,
                "opportunity_id": opportunity.id,
                "status": opportunity.status,
                "created": True,
                "lead_status": status_result["status"],
            },
            "Opportunity created successfully",
        )

    # -- 5. callbacks ------------------------------------------------------

    def _validate_callback_time(self, callback_time: datetime) -> None:
        now = datetime.now(timezone.utc)
        if callback_time <= now - timedelta(minutes=1):
            raise InvalidParameterError(
                "the requested callback time is in the past",
                details=[{"requested_at": callback_time.isoformat()}],
            )
        max_days = self._settings.voice_callback_max_days_ahead
        if callback_time > now + timedelta(days=max_days):
            raise InvalidParameterError(
                f"the requested callback time is more than {max_days} days ahead",
                details=[{"requested_at": callback_time.isoformat(), "max_days": max_days}],
            )

    def _create_callback_for_outcome(
        self,
        identity: ToolIdentity,
        lead,
        request: SaveOutcomeRequest,
        call,
    ) -> Dict[str, Any]:
        """The callback part of a save-outcome request.

        Input validation failures abort the whole request (nothing was
        written yet); compliance failures only mean the callback is not
        created while the outcome itself is still saved.
        """
        if request.callback_time is None:
            # A callback request without a time is recorded for the team.
            handoff = self._record_callback_handoff(
                identity.business_id, lead, requested_utc=None, timezone_name=None,
                reason=request.callback_reason or "callback requested during the call",
                execution_id=request.execution_id,
                call_id=(call.id if call is not None else None),
            )
            self._apply_status(
                lead, "callback_requested",
                {"lead_id": lead.id, "callback_id": handoff.id},
            )
            return {
                "scheduled": False,
                "callback_id": handoff.id,
                "status": handoff.status,
                "detail": "no callback time was given; the request is queued for the team",
            }

        try:
            return self._schedule_callback(
                identity,
                lead_id=lead.id,
                requested_at=request.callback_time,
                timezone_name=request.callback_timezone
                or (call.timezone if call is not None and getattr(call, "timezone", None) else None)
                or self._business(identity.business_id).timezone,
                reason=request.callback_reason or "callback requested during the call",
                execution_id=request.execution_id,
                call_id=(call.id if call is not None else None),
            )
        except AppError as exc:
            # Compliance blocks: the outcome stands, the callback does not.
            return {
                "scheduled": False,
                "error": {"code": exc.code.upper(), "message": exc.message},
            }

    def schedule_callback(self, identity: ToolIdentity, request: ScheduleCallbackRequest) -> Dict[str, Any]:
        call = verify_call_context(
            self._session, identity, request.execution_id, request.call_id
        )
        lead = self._lead_or_404(identity.business_id, request.lead_id)
        data = self._schedule_callback(
            identity,
            lead_id=lead.id,
            requested_at=request.requested_at,
            timezone_name=request.timezone,
            reason=request.reason or "the customer requested a callback",
            execution_id=request.execution_id,
            call_id=(call.id if call is not None else None),
        )
        return tool_success(data, "Callback scheduled successfully")

    def _schedule_callback(
        self,
        identity: ToolIdentity,
        lead_id: str,
        requested_at: datetime,
        timezone_name: Optional[str],
        reason: str,
        execution_id: Optional[str],
        call_id: Optional[str],
    ) -> Dict[str, Any]:
        """Resolve, validate, compliance-check and persist one callback."""
        business = self._business(identity.business_id)
        lead = self._lead_or_404(identity.business_id, lead_id)
        tz_name = timezone_name or business.timezone

        # Timezone resolution — an unresolvable name is a hard error.
        tz = repo.resolve_timezone(tz_name)
        if tz_name and tz_name.strip().lower() not in (
            "utc",
            "etc/utc",
            "z",
            "gmt",
        ):
            # resolve_timezone falls back to UTC silently; reject a named
            # zone it could not resolve so the agent can correct the input.
            try:
                from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

                ZoneInfo(tz_name)
            except (ZoneInfoNotFoundError, ValueError, KeyError):
                raise InvalidParameterError(
                    f"the timezone '{tz_name}' could not be resolved",
                    details=[{"timezone": tz_name}],
                )

        if requested_at.tzinfo is None:
            # Naive time — interpret it *in* the requested timezone.
            localized = requested_at.replace(tzinfo=tz)
            requested_utc = localized.astimezone(timezone.utc)
        else:
            requested_utc = requested_at.astimezone(timezone.utc)

        self._validate_callback_time(requested_utc)

        evaluation = compliance_mod.evaluate_callback_request(
            self._settings, self._session, business, lead, requested_utc
        )
        if not evaluation.allowed:
            blocked = evaluation.blocked_reasons
            codes = [r.get("reason_code") or "unknown" for r in blocked]
            if any(code.startswith("consent") for code in codes):
                raise CallbackBlockedError(
                    "consent", "the business does not have voice consent for this lead"
                )
            if any(code.startswith("dnd") for code in codes):
                raise CallbackBlockedError(
                    "dnd", "the lead's number is under a do-not-disturb restriction"
                )
            raise CallbackWindowError(
                "the requested callback time is outside the business's "
                "permitted calling window",
                details=blocked,
            )

        requested_utc_iso = requested_utc.isoformat()
        key = f"{lead.id}:{requested_utc_iso}"
        replay = self._idempotency_replay(identity.business_id, SCOPE_CALLBACK, key, None)
        if replay is not None:
            handoff = self._find_callback_handoff(identity.business_id, key)
            if handoff is not None:
                return {
                    "scheduled": True,
                    "callback_id": handoff.id,
                    "status": handoff.status,
                    "requested_at": requested_utc_iso,
                    "timezone": tz_name,
                    "replayed": True,
                }

        handoff = self._record_callback_handoff(
            identity.business_id,
            lead,
            requested_utc=requested_utc,
            timezone_name=tz_name,
            reason=reason,
            execution_id=execution_id,
            call_id=call_id,
        )

        dispatch: Dict[str, Any] = {"mode": "pending_manual"}
        if self._settings.voice_auto_callback_dispatch:
            delay = max(0.0, (requested_utc - datetime.now(timezone.utc)).total_seconds())
            job, created = self._enqueue_callback_job(handoff, delay)
            dispatch = {
                "mode": "queued" if created else "already_queued",
                "job_id": (job.id if job else None),
            }

        self._apply_status(
            lead,
            "callback_requested",
            {
                "lead_id": lead.id,
                "callback_id": handoff.id,
                "requested_at": requested_utc_iso,
            },
        )
        repo.add_audit_log(
            self._session,
            action=AuditAction.TOOL_CALLBACK,
            business_id=lead.business_id,
            actor="voice_tool",
            entity_type="handoff",
            entity_id=handoff.id,
            decision="allow",
            reason_code="callback_scheduled",
            reason=reason,
            metadata={
                "lead_id": lead.id,
                "requested_at_utc": requested_utc_iso,
                "timezone": tz_name,
                "dispatch": dispatch,
                "execution_id": execution_id,
            },
        )
        self._store_callback_idempotency(identity.business_id, key, handoff.id)
        self._session.commit()

        return {
            "scheduled": True,
            "callback_id": handoff.id,
            "status": handoff.status,
            "requested_at": requested_utc_iso,
            "timezone": tz_name,
            "dispatch": dispatch,
        }

    def _record_callback_handoff(
        self,
        business_id: str,
        lead,
        requested_utc: Optional[datetime],
        timezone_name: Optional[str],
        reason: str,
        execution_id: Optional[str],
        call_id: Optional[str],
    ):
        context: Dict[str, Any] = {
            "source": "voice_tool",
            "requested_at_utc": requested_utc.isoformat() if requested_utc else None,
            "timezone": timezone_name,
            "execution_id": execution_id,
        }
        handoff = HandoffService(self._settings, self._session).request_callback(
            business_id=business_id,
            lead_id=lead.id,
            call_id=call_id,
            reason=reason,
            lead_name=lead.name,
            requested_by="voice_tool",
            reason_code="callback_requested",
            context=context,
        )
        return handoff

    def _find_callback_handoff(self, business_id: str, key: str):
        from sqlalchemy import select

        from app.modules.voice.models import Handoff

        record = repo.get_idempotency_record(self._session, business_id, SCOPE_CALLBACK, key)
        if record is None or not record.response_ref:
            return None
        return (
            self._session.execute(
                select(Handoff).where(Handoff.id == record.response_ref)
            )
            .scalars()
            .first()
        )

    def _store_callback_idempotency(
        self, business_id: str, key: str, handoff_id: str
    ) -> None:
        """Persist the callback idempotency record with a pointer to the
        handoff, so a replayed request returns the original callback."""
        repo.store_idempotency_record(
            self._session,
            business_id,
            SCOPE_CALLBACK,
            key,
            response_ref=handoff_id,
        )

    def _enqueue_callback_job(self, handoff, delay_seconds: float):
        return _enqueue_callback_job(self._session, self._settings, handoff, delay_seconds)

    # -- 6. CRM sync -------------------------------------------------------

    async def sync_lead_to_crm(self, identity: ToolIdentity, request: CrmSyncToolRequest) -> Dict[str, Any]:
        """Synchronize the lead with the external CRM.

        Every identifier is validated here: the lead is loaded from the
        database after the ownership check, so the CRM request always
        carries a real ``id`` *and* ``business_id`` — the requirement the
        CRM module enforces (a lead without both is rejected). Nothing the
        model said is forwarded to the CRM.
        """
        from app.errors import (
            CRMConnectionNotFoundError,
            CRMConnectionInactiveError,
        )

        call = verify_call_context(
            self._session, identity, request.execution_id, request.call_id
        )
        lead = self._lead_or_404(identity.business_id, request.lead_id)
        self._business(identity.business_id)

        from app.modules.crm_sync.schemas import CRMSyncRequest, LeadSyncInput
        from app.modules.crm_sync.service import CRMSyncService

        service = CRMSyncService(self._session)
        try:
            connection = service._resolve_connection(
                identity.business_id, request.connection_id
            )
        except (CRMConnectionNotFoundError, CRMConnectionInactiveError):
            raise CrmNotConfiguredError(
                f"no active CRM connection '{request.connection_id}' is "
                "configured for this business"
            )

        lead_input = LeadSyncInput(
            id=lead.id,
            business_id=lead.business_id,
            name=lead.name,
            phone=lead.phone,
            email=lead.email,
            source=lead.source,
            language=lead.language,
            deal_value=lead.deal_value,
            last_contact_at=lead.last_contact_at,
            custom_fields=lead.custom_fields or {},
            external_id=lead.external_id,
            created_at=lead.created_at,
        )
        event_key = request.execution_id or request.call_id or "latest"
        idempotency_key = f"voice:{lead.id}:{event_key}"[:128]
        sync_request = CRMSyncRequest(
            connection_id=request.connection_id,
            lead=lead_input,
            idempotency_key=idempotency_key,
        )
        try:
            response = await service.sync_lead(sync_request)
        except AppError as exc:
            # The CRM module's own validation and error handling are
            # preserved; the failure is reported honestly to the agent.
            repo.add_audit_log(
                self._session,
                action=AuditAction.TOOL_CRM_SYNC,
                business_id=lead.business_id,
                actor="voice_tool",
                entity_type="lead",
                entity_id=lead.id,
                decision="block",
                reason_code=exc.code,
                reason=f"the CRM synchronization failed: {exc.message}",
                metadata={"lead_id": lead.id, "connection_id": request.connection_id},
            )
            self._session.commit()
            raise

        repo.add_audit_log(
            self._session,
            action=AuditAction.TOOL_CRM_SYNC,
            business_id=lead.business_id,
            actor="voice_tool",
            entity_type="lead",
            entity_id=lead.id,
            decision="allow",
            reason_code=f"crm_{response.status}",
            reason=f"the lead was synchronized with the CRM ({response.status})",
            metadata={
                "lead_id": lead.id,
                "sync_id": response.sync_id,
                "external_crm_id": response.external_crm_id,
                "connection_id": request.connection_id,
                "call_id": (call.id if call is not None else None),
            },
        )
        self._session.commit()

        return tool_success(
            {
                "lead_id": lead.id,
                "sync_id": response.sync_id,
                "status": response.status,
                "external_crm_id": response.external_crm_id,
                "connection_id": request.connection_id,
            },
            response.message,
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def callback_requested_now(request: SaveOutcomeRequest) -> bool:
    return bool(request.callback_requested) or request.outcome == ToolOutcome.CALLBACK_REQUESTED


def qual_is_unknown(value: Any) -> bool:
    from app.modules.voice.tools.qualification import _is_unknown

    return _is_unknown(value)


def _enqueue_callback_job(session: Session, settings: Settings, handoff, delay_seconds: float):
    from app.modules.voice.jobs import queue

    job, created = queue.enqueue(
        session,
        JobType.CALLBACK_DISPATCH,
        payload={
            "handoff_id": handoff.id,
            "lead_id": handoff.lead_id,
            "business_id": handoff.business_id,
        },
        dedupe_key=f"callback:{handoff.id}",
        max_attempts=settings.voice_job_max_attempts,
        delay_seconds=delay_seconds,
        business_id=handoff.business_id,
    )
    return job, created


class CallbackBlockedError(AppError):
    """A callback the customer asked for collides with consent/DND."""

    status_code = 403

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CallbackWindowError(AppError):
    """The requested callback time is outside the permitted calling window."""

    status_code = 422
    code = "callback_outside_calling_hours"

    def __init__(self, message: str, details: Optional[list] = None):
        super().__init__(message, details=details)


class CrmNotConfiguredError(AppError):
    """No active CRM connection exists for the business."""

    status_code = 409
    code = "crm_not_configured"


__all__ = [
    "LeadStatusPriority",
    "SCOPE_CALLBACK",
    "SCOPE_OUTCOME",
    "SCOPE_OPPORTUNITY",
    "VoiceToolService",
    "payload_hash",
]
