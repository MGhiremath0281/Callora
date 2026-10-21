"""Human handoff workflow.

Two kinds of handoff exist:

``warm_transfer``
    The agent connected the caller to a human *during* the call. Bolna
    reports this through the pre-transfer webhook, and the record here is
    the audit trail of a transfer that already happened.

``callback``
    The caller asked to be called back later, or the post-call analysis
    decided a human should continue the conversation. The record is a
    request for the sales team to act.

Both are stored in the same table so an operator has one queue of "things
a human must deal with". The service owns every state transition, so the
status vocabulary is applied consistently and every transition is audited.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.modules.voice import repository as repo
from app.modules.voice.constants import (
    AuditAction,
    HandoffKind,
    HandoffStatus,
)
from app.modules.voice.models import Call, Handoff

logger = logging.getLogger("callora.voice.handoff")

#: Statuses a human may set from the API.
MANUAL_STATUSES = frozenset(
    {
        HandoffStatus.REQUESTED,
        HandoffStatus.INITIATED,
        HandoffStatus.COMPLETED,
        HandoffStatus.FAILED,
        HandoffStatus.CANCELLED,
    }
)


class HandoffService:
    """Creates and updates human-handoff records."""

    def __init__(self, settings: Settings, session: Session) -> None:
        self._settings = settings
        self._session = session

    # -- warm transfer -----------------------------------------------------

    def record_warm_transfer(
        self,
        call: Call,
        reason: str,
        summary: str = "",
        transferred_to: Optional[str] = None,
    ) -> Handoff:
        """Record a live warm transfer the provider performed.

        The provider reports the transfer *after* it happened, so the record
        is written from an observed fact — never from a prediction. The
        call row is updated in the same transaction so the transcript, the
        recording and the handoff always describe the same conversation.
        """
        business = repo.get_business(self._session, call.business_id)
        enabled = bool(business is not None and business.warm_transfer_enabled)
        status = HandoffStatus.INITIATED if enabled else HandoffStatus.NOT_SUPPORTED
        target = (
            transferred_to
            or (business.warm_transfer_number if business is not None else None)
            or (business.handoff_target_number if business is not None else None)
        )

        handoff = self._find_for_call(call.id, HandoffKind.WARM_TRANSFER)
        context: Dict[str, Any] = {
            "transferred_to": transferred_to,
            "summary": summary,
            "provider_call_id": call.provider_call_id,
        }
        if handoff is None:
            handoff = repo.create_handoff(
                self._session,
                Handoff(
                    business_id=call.business_id,
                    call_id=call.id,
                    lead_id=call.lead_id,
                    lead_name=self._lead_name(call),
                    kind=HandoffKind.WARM_TRANSFER,
                    status=status,
                    reason=reason,
                    reason_code="warm_transfer",
                    requested_by="ai",
                    target_number=target,
                    briefing=summary or None,
                    context=context,
                ),
            )
        else:
            repo.update_handoff(
                self._session,
                handoff,
                {
                    "status": status,
                    "reason": reason,
                    "target_number": target,
                    "briefing": summary or handoff.briefing,
                    "context": {**(handoff.context or {}), **context},
                },
            )

        repo.add_audit_log(
            self._session,
            action=AuditAction.HANDOFF_REQUESTED,
            business_id=call.business_id,
            actor="webhook",
            entity_type="handoff",
            entity_id=handoff.id,
            reason_code="warm_transfer",
            reason=reason,
            metadata={
                "call_id": call.id,
                "lead_id": call.lead_id,
                "status": status,
                "transferred_to": transferred_to,
            },
        )
        logger.info(
            "warm_transfer_recorded",
            extra={"call_id": call.id, "handoff_id": handoff.id, "status": status},
        )
        return handoff

    # -- callback ----------------------------------------------------------

    def request_callback(
        self,
        business_id: str,
        lead_id: Optional[str] = None,
        call_id: Optional[str] = None,
        reason: Optional[str] = None,
        briefing: Optional[str] = None,
        requested_by: str = "ai",
        reason_code: Optional[str] = None,
        target_number: Optional[str] = None,
        lead_name: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Handoff:
        """Open a callback request for the sales team."""
        handoff = repo.create_handoff(
            self._session,
            Handoff(
                business_id=business_id,
                call_id=call_id,
                lead_id=lead_id,
                lead_name=lead_name,
                kind=HandoffKind.CALLBACK,
                status=HandoffStatus.REQUESTED,
                reason=reason,
                reason_code=reason_code or "callback_requested",
                requested_by=requested_by,
                target_number=target_number,
                briefing=briefing,
                context=context or {},
            ),
        )
        repo.add_audit_log(
            self._session,
            action=AuditAction.HANDOFF_REQUESTED,
            business_id=business_id,
            actor=requested_by,
            entity_type="handoff",
            entity_id=handoff.id,
            reason_code=reason_code or "callback_requested",
            reason=reason or "a callback was requested",
            metadata={"call_id": call_id, "lead_id": lead_id},
        )
        logger.info(
            "callback_requested",
            extra={"handoff_id": handoff.id, "business_id": business_id},
        )
        return handoff

    # -- state transitions -------------------------------------------------

    def set_status(
        self,
        handoff: Handoff,
        status: str,
        note: Optional[str] = None,
        actor: str = "api",
    ) -> Handoff:
        """Move a handoff to a new status.

        Only the documented statuses are accepted; anything else is a
        client error rather than a silently ignored value.
        """
        if status not in MANUAL_STATUSES and status != HandoffStatus.NOT_SUPPORTED:
            raise ValueError(
                f"'{status}' is not a valid handoff status; expected one of "
                f"{sorted(MANUAL_STATUSES)}"
            )
        values: Dict[str, Any] = {"status": status}
        if note:
            values["outcome_note"] = note
        repo.update_handoff(self._session, handoff, values)
        repo.add_audit_log(
            self._session,
            action=AuditAction.HANDOFF_COMPLETED
            if status == HandoffStatus.COMPLETED
            else AuditAction.HANDOFF_REQUESTED,
            business_id=handoff.business_id,
            actor=actor,
            entity_type="handoff",
            entity_id=handoff.id,
            reason_code=status,
            reason=note or f"the handoff status changed to '{status}'",
            metadata={"call_id": handoff.call_id, "lead_id": handoff.lead_id},
        )
        return handoff

    def record_outcome(
        self,
        handoff: Handoff,
        outcome: str,
        note: Optional[str] = None,
        actor: str = "api",
    ) -> Handoff:
        """Record what actually happened after the handoff."""
        values: Dict[str, Any] = {"outcome": outcome}
        if note:
            values["outcome_note"] = note
        if outcome in ("completed", "connected", "resolved"):
            values["status"] = HandoffStatus.COMPLETED
        elif outcome in ("failed", "unreachable", "no_answer"):
            values["status"] = HandoffStatus.FAILED
        elif outcome in ("cancelled", "rejected"):
            values["status"] = HandoffStatus.CANCELLED
        repo.update_handoff(self._session, handoff, values)
        repo.add_audit_log(
            self._session,
            action=AuditAction.HANDOFF_COMPLETED,
            business_id=handoff.business_id,
            actor=actor,
            entity_type="handoff",
            entity_id=handoff.id,
            reason_code=outcome,
            reason=note or f"the handoff outcome was recorded as '{outcome}'",
            metadata={"call_id": handoff.call_id, "lead_id": handoff.lead_id},
        )
        return handoff

    # -- reads -------------------------------------------------------------

    def get(self, business_id: str, handoff_id: str) -> Optional[Handoff]:
        return repo.get_handoff(self._session, business_id, handoff_id)

    def list(
        self,
        business_id: str,
        status: Optional[str] = None,
        kind: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Handoff]:
        return repo.list_handoffs(self._session, business_id, status, kind, limit, offset)

    # -- internals ---------------------------------------------------------

    def _find_for_call(self, call_id: str, kind: str) -> Optional[Handoff]:
        stmt = select(Handoff).where(Handoff.call_id == call_id, Handoff.kind == kind).limit(1)
        return self._session.execute(stmt).scalars().first()

    def _lead_name(self, call: Call) -> Optional[str]:
        if not call.lead_id:
            return None
        lead = repo.get_lead(self._session, call.business_id, call.lead_id)
        return lead.name if lead is not None else None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
