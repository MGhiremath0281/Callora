"""Serialization helpers for the voice API.

The API never returns ORM objects directly: every response body is built
here so the wire format is stable, free of internal columns and
explicitly documented in the OpenAPI schema.
"""

from typing import Any, Dict, List, Optional

from app.modules.voice.models import (
    AuditLog,
    Business,
    BusinessKnowledgeItem,
    Call,
    CallEvent,
    CallOutcome,
    ConsentRecord,
    DndEntry,
    Handoff,
    Job,
)
from app.modules.voice.models import ComplianceDecision as ComplianceDecisionRow


def business_payload(business: Business, settings: Any = None) -> Dict[str, Any]:
    """Serialize a business voice configuration."""
    provider_configured = False
    outbound_enabled = False
    quotas_disabled = False
    if settings is not None:
        provider_configured = bool(getattr(settings, "bolna_api_key", None))
        outbound_enabled = bool(getattr(settings, "voice_enable_outbound_calls", False))
        quotas_disabled = bool(getattr(settings, "call_quotas_disabled", False))
    return {
        "business_id": business.id,
        "name": business.name,
        "timezone": business.timezone,
        "default_language": business.default_language,
        "supported_languages": list(business.supported_languages or []),
        "calling_window_start": business.calling_window_start,
        "calling_window_end": business.calling_window_end,
        "calling_days": list(business.calling_days or []),
        "max_calls_per_lead_per_day": business.max_calls_per_lead_per_day,
        "max_calls_per_business_per_day": business.max_calls_per_business_per_day,
        "max_concurrent_calls": business.max_concurrent_calls,
        "require_consent": business.require_consent,
        "dnd_enabled": business.dnd_enabled,
        "bolna_agent_id": business.bolna_agent_id,
        "agent_instructions": business.agent_instructions,
        "qualification_questions": list(business.qualification_questions or []),
        "warm_transfer_enabled": business.warm_transfer_enabled,
        "warm_transfer_number": business.warm_transfer_number,
        "handoff_target_number": business.handoff_target_number,
        "is_active": business.is_active,
        "outbound_calls_enabled": outbound_enabled,
        "provider_configured": provider_configured,
        "call_quotas_disabled": quotas_disabled,
        "created_at": business.created_at,
        "updated_at": business.updated_at,
    }


def call_payload(call: Call) -> Dict[str, Any]:
    """Serialize one call."""
    metadata = dict(call.call_metadata or {})
    return {
        "id": call.id,
        "business_id": call.business_id,
        "lead_id": call.lead_id,
        "provider": call.provider,
        "provider_call_id": call.provider_call_id,
        "status": call.status,
        "blocked_reason": call.blocked_reason,
        "error_message": call.error_message,
        "to_number": call.to_number,
        "from_number": call.from_number,
        "language": call.language,
        "direction": call.direction,
        "scheduled_at": call.scheduled_at,
        "initiated_at": call.initiated_at,
        "connected_at": call.connected_at,
        "ended_at": call.ended_at,
        "duration_seconds": call.duration_seconds,
        "cost": float(call.cost) if call.cost is not None else None,
        "currency": call.currency,
        "recording_url": call.recording_url,
        "transcript": call.transcript,
        "summary": call.summary,
        "compliance_decision_id": call.compliance_decision_id,
        "simulated": bool(metadata.get("simulated")),
        "metadata": metadata,
        "created_at": call.created_at,
        "updated_at": call.updated_at,
    }


def call_event_payload(event: CallEvent) -> Dict[str, Any]:
    """Serialize one provider event."""
    return {
        "id": event.id,
        "call_id": event.call_id,
        "provider": event.provider,
        "event_key": event.event_key,
        "event_type": event.event_type,
        "provider_status": event.provider_status,
        "provider_event_id": event.provider_event_id,
        "payload": dict(event.payload or {}),
        "signature_valid": event.signature_valid,
        "processed": event.processed,
        "processing_error": event.processing_error,
        "received_at": event.received_at,
    }


def call_outcome_payload(outcome: CallOutcome) -> Dict[str, Any]:
    """Serialize one post-call outcome."""
    return {
        "id": outcome.id,
        "business_id": outcome.business_id,
        "call_id": outcome.call_id,
        "lead_id": outcome.lead_id,
        "summary": outcome.summary,
        "disposition": outcome.disposition,
        "qualified": outcome.qualified,
        "qualification_score": (
            float(outcome.qualification_score)
            if outcome.qualification_score is not None
            else None
        ),
        "qualification_answers": dict(outcome.qualification_answers or {}),
        "refusal_category": outcome.refusal_category,
        "refusal_reason": outcome.refusal_reason,
        "follow_up_actions": list(outcome.follow_up_actions or []),
        "handoff_required": outcome.handoff_required,
        "handoff_briefing": outcome.handoff_briefing,
        "language_detected": outcome.language_detected,
        "model": outcome.model,
        "engine": outcome.engine,
        "processed_at": outcome.processed_at,
        "created_at": outcome.created_at,
        "updated_at": outcome.updated_at,
    }


def consent_payload(record: ConsentRecord) -> Dict[str, Any]:
    """Serialize one consent record."""
    return {
        "id": record.id,
        "business_id": record.business_id,
        "lead_id": record.lead_id,
        "phone": record.phone,
        "channel": record.channel,
        "status": record.status,
        "source": record.source,
        "granted_at": record.granted_at,
        "expires_at": record.expires_at,
        "revoked_at": record.revoked_at,
        "evidence": dict(record.evidence or {}),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def dnd_payload(entry: DndEntry) -> Dict[str, Any]:
    """Serialize one DND registry entry."""
    return {
        "id": entry.id,
        "scope": entry.scope,
        "business_id": entry.business_id,
        "phone": entry.phone,
        "reason": entry.reason,
        "source": entry.source,
        "active": entry.active,
        "expires_at": entry.expires_at,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


def handoff_payload(handoff: Handoff) -> Dict[str, Any]:
    """Serialize one handoff."""
    return {
        "id": handoff.id,
        "business_id": handoff.business_id,
        "call_id": handoff.call_id,
        "lead_id": handoff.lead_id,
        "lead_name": handoff.lead_name,
        "kind": handoff.kind,
        "status": handoff.status,
        "reason": handoff.reason,
        "reason_code": handoff.reason_code,
        "requested_by": handoff.requested_by,
        "target_number": handoff.target_number,
        "briefing": handoff.briefing,
        "context": dict(handoff.context or {}),
        "outcome": handoff.outcome,
        "outcome_note": handoff.outcome_note,
        "created_at": handoff.created_at,
        "updated_at": handoff.updated_at,
    }


def job_payload(job: Job) -> Dict[str, Any]:
    """Serialize one background job."""
    return {
        "id": job.id,
        "type": job.type,
        "business_id": job.business_id,
        "payload": dict(job.payload or {}),
        "status": job.status,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "last_error": job.last_error,
        "result": dict(job.result) if job.result else None,
        "dedupe_key": job.dedupe_key,
        "scheduled_at": job.scheduled_at,
        "locked_at": job.locked_at,
        "locked_by": job.locked_by,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def audit_log_payload(entry: AuditLog) -> Dict[str, Any]:
    """Serialize one audit-trail entry."""
    return {
        "id": entry.id,
        "business_id": entry.business_id,
        "actor": entry.actor,
        "action": entry.action,
        "entity_type": entry.entity_type,
        "entity_id": entry.entity_id,
        "decision": entry.decision,
        "reason_code": entry.reason_code,
        "reason": entry.reason,
        "metadata": dict(entry.audit_metadata or {}),
        "created_at": entry.created_at,
    }


def compliance_decision_payload(decision: ComplianceDecisionRow) -> Dict[str, Any]:
    """Serialize one persisted compliance decision."""
    return {
        "id": decision.id,
        "business_id": decision.business_id,
        "lead_id": decision.lead_id,
        "call_id": decision.call_id,
        "decision": decision.decision,
        "checks": list(decision.checks or []),
        "blocked_reasons": list(decision.blocked_reasons or []),
        "policy_version": decision.policy_version,
        "created_at": decision.created_at,
    }


def list_payload(items: List[Dict[str, Any]], business_id: str, limit: int, offset: int) -> Dict[str, Any]:
    """Wrap a page of items in the standard list envelope."""
    return {
        "items": items,
        "business_id": business_id,
        "limit": limit,
        "offset": offset,
    }


def knowledge_item_payload(item: BusinessKnowledgeItem) -> Dict[str, Any]:
    """Serialize one business knowledge item."""
    return {
        "id": item.id,
        "business_id": item.business_id,
        "category": item.category,
        "title": item.title,
        "content": item.content,
        "tags": list(item.tags or []),
        "source_reference": item.source_reference,
        "is_active": item.is_active,
        "priority": item.priority,
        "extra_metadata": dict(item.extra_metadata or {}),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


__all__ = [
    "audit_log_payload",
    "business_payload",
    "call_event_payload",
    "call_outcome_payload",
    "call_payload",
    "compliance_decision_payload",
    "consent_payload",
    "dnd_payload",
    "handoff_payload",
    "job_payload",
    "knowledge_item_payload",
    "list_payload",
]
