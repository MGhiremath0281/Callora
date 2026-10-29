"""Deterministic eligibility for recovery workflow execution.

The recovery sweep decides *which leads are opportunities*. This module
decides whether a specific action (call / message / email / booking) may be
attempted now. It reuses the voice module's compliance gate for voice so the
same consent, DND, calling-window, duplicate-call, quota and concurrency
rules apply, and adds workflow-specific rules (max attempts, channel
contactability, active run limits).

Every decision is persisted and auditable. A decision is never fabricated:
an unknown consent record blocks when the business requires consent.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Lead
from app.modules.recovery_workflow.models import (
    EligibilityDecisionRecord,
    WorkflowRun,
)
from app.modules.voice import repository as voice_repo
from app.modules.voice.compliance import ComplianceContext, ComplianceGate
from app.modules.voice.models import Business
from app.modules.voice.phone import is_valid_e164, normalize_phone
from app.modules.voice.providers.registry import build_provider

CONTACT_CHANNELS = ("voice", "sms", "whatsapp", "email")


@dataclass
class EligibilityResult:
    eligible: bool
    reasons: List[Dict[str, Any]] = field(default_factory=list)
    checks: List[Dict[str, Any]] = field(default_factory=list)
    decision_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reasons": self.reasons,
            "checks": self.checks,
            "decision_id": self.decision_id,
        }


def _blocked(check: str, code: str, reason: str) -> Dict[str, Any]:
    return {"check": check, "reason_code": code, "reason": reason}


def evaluate_eligibility(
    session: Session,
    settings: Settings,
    *,
    business_id: str,
    lead_id: str,
    channel: str = "voice",
    case_id: Optional[str] = None,
    now: Optional[datetime] = None,
    persist: bool = True,
) -> EligibilityResult:
    """Evaluate every workflow eligibility rule for one lead/channel."""
    now = now or datetime.now(timezone.utc)
    channel = channel.strip().lower()
    checks: List[Dict[str, Any]] = []
    reasons: List[Dict[str, Any]] = []

    def fail(check: str, code: str, reason: str) -> None:
        checks.append(
            {"name": check, "outcome": "failed", "reason_code": code, "reason": reason}
        )
        reasons.append(_blocked(check, code, reason))

    def pass_check(check: str, reason: str, **detail: Any) -> None:
        checks.append(
            {"name": check, "outcome": "passed", "reason": reason, "detail": detail}
        )

    business = voice_repo.get_business(session, business_id)
    lead = voice_repo.get_lead(session, business_id, lead_id)

    if business is None:
        fail("business_active", "business_not_found", "business does not exist")
        return _finish(
            session,
            business_id,
            lead_id,
            case_id,
            channel,
            checks,
            reasons,
            persist,
        )
    if not business.is_active:
        fail("business_active", "business_inactive", "business is deactivated")
    else:
        pass_check("business_active", "business is active")

    if lead is None:
        fail("lead_exists", "lead_not_found", "lead does not exist for this business")
        return _finish(
            session,
            business_id,
            lead_id,
            case_id,
            channel,
            checks,
            reasons,
            persist,
        )
    pass_check("lead_exists", "lead exists", lead_id=lead.id)

    if channel not in CONTACT_CHANNELS:
        fail("channel", "unsupported_channel", f"unsupported channel {channel!r}")
        return _finish(
            session,
            business_id,
            lead_id,
            case_id,
            channel,
            checks,
            reasons,
            persist,
        )

    # Channel contactability. Missing/blank data is unknown, never usable.
    phone = normalize_phone(lead.phone)
    if channel in ("voice", "sms", "whatsapp"):
        if not phone or not is_valid_e164(phone):
            fail("contactable", "no_contact_channel", "lead has no usable phone")
        else:
            pass_check("contactable", "usable phone", phone=phone)
    else:
        if not (lead.email and lead.email.strip()):
            fail("contactable", "no_contact_channel", "lead has no usable email")
        else:
            pass_check("contactable", "usable email")

    # Consent: a missing record blocks when the business requires consent.
    if business.require_consent:
        consent = voice_repo.get_consent(session, business_id, lead.id, channel)
        if consent is None:
            fail(
                "consent",
                "consent_missing",
                f"no {channel} consent record exists for this lead",
            )
        elif consent.status == "revoked":
            fail("consent", "consent_revoked", f"{channel} consent was revoked")
        elif consent.status == "expired" or (
            consent.expires_at is not None and consent.expires_at <= now
        ):
            fail("consent", "consent_expired", f"{channel} consent has expired")
        elif consent.status != "granted":
            fail(
                "consent",
                "consent_missing",
                f"{channel} consent is in state {consent.status!r}",
            )
        else:
            pass_check("consent", f"{channel} consent is on record")
    else:
        checks.append(
            {
                "name": "consent",
                "outcome": "skipped",
                "reason": "business does not require recorded consent",
            }
        )

    # DND: the local registry is authoritative for phone channels.
    if channel in ("voice", "sms", "whatsapp") and business.dnd_enabled:
        profile = voice_repo.get_lead_profile(session, business_id, lead.id)
        if profile is not None and profile.do_not_call:
            fail("dnd", "dnd_lead_flag", "lead is flagged do-not-call")
        else:
            entry = voice_repo.find_dnd_entry(session, business_id, phone)
            if entry is not None:
                fail("dnd", "dnd_active", "number is on a do-not-disturb list")
            else:
                pass_check("dnd", "number is not on a do-not-disturb list")
    elif channel in ("voice", "sms", "whatsapp"):
        checks.append(
            {
                "name": "dnd",
                "outcome": "skipped",
                "reason": "DND screening is disabled for this business",
            }
        )

    # Voice uses the full compliance gate (calling window, duplicate call,
    # quota, concurrency, provider readiness). It is evaluated as a
    # *simulated* call: this is a planning decision, so missing provider
    # credentials must not block planning. The orchestrator runs the gate
    # again with the real value before any call is placed.
    if channel == "voice":
        provider = build_provider(settings=settings)
        gate = ComplianceGate(settings, session)
        ctx = ComplianceContext(
            business=business,
            lead=lead,
            provider=provider,
            now=now,
            profile=voice_repo.get_lead_profile(session, business_id, lead.id),
            phone=phone,
            simulated=True,
        )
        evaluation = gate.evaluate(ctx)
        for check in evaluation.checks:
            if check.name in {
                "business_active",
                "lead_exists",
                "consent",
                "dnd",
                "provider_ready",
            }:
                continue
            entry = check.to_dict()
            checks.append(entry)
            if not check.passed and check.mandatory:
                reasons.append(check.to_blocked_reason())

    # Workflow-specific: attempt cap.
    attempt_count = (
        session.execute(
            select(voice_repo.Call.id).where(
                voice_repo.Call.business_id == business_id,
                voice_repo.Call.lead_id == lead.id,
            )
        ).all()
    )
    if len(attempt_count) >= settings.recovery_max_attempts:
        fail(
            "attempt_limit",
            "attempt_limit_reached",
            f"lead already has {len(attempt_count)} call attempts "
            f"(limit {settings.recovery_max_attempts})",
        )
    else:
        pass_check(
            "attempt_limit",
            "attempt limit not reached",
            attempts=len(attempt_count),
            limit=settings.recovery_max_attempts,
        )

    # Workflow-specific: active-run cap (per business).
    active_runs = (
        session.execute(
            select(WorkflowRun.id).where(
                WorkflowRun.business_id == business_id,
                WorkflowRun.status.in_(["approved", "running", "paused"]),
            )
        ).all()
    )
    if len(active_runs) >= settings.workflow_max_active_runs:
        fail(
            "workflow_concurrency",
            "workflow_concurrency_reached",
            f"{len(active_runs)} active workflow runs "
            f"(limit {settings.workflow_max_active_runs})",
        )
    else:
        pass_check(
            "workflow_concurrency",
            "workflow concurrency limit not reached",
            active=len(active_runs),
            limit=settings.workflow_max_active_runs,
        )

    # Cooldown for non-voice channels (reuses the configured anti-duplicate
    # window; 0 disables it).
    if channel in ("sms", "whatsapp", "email"):
        cooldown = settings.workflow_message_cooldown_seconds
        if cooldown > 0:
            since = now - timedelta(seconds=cooldown)
            recent = (
                session.execute(
                    select(WorkflowRun.id).where(
                        WorkflowRun.business_id == business_id,
                        WorkflowRun.lead_id == lead.id,
                        WorkflowRun.status == "completed",
                        WorkflowRun.completed_at >= since,
                    )
                ).all()
            )
            if recent:
                fail(
                    "message_cooldown",
                    "message_cooldown_active",
                    f"another workflow completed for this lead within {cooldown}s",
                )
            else:
                pass_check("message_cooldown", "no recent workflow contact")

    return _finish(
        session,
        business_id,
        lead_id,
        case_id,
        channel,
        checks,
        reasons,
        persist,
    )


def _finish(
    session: Session,
    business_id: str,
    lead_id: str,
    case_id: Optional[str],
    channel: str,
    checks: List[Dict[str, Any]],
    reasons: List[Dict[str, Any]],
    persist: bool,
) -> EligibilityResult:
    eligible = not reasons
    decision_id = None
    if persist:
        row = EligibilityDecisionRecord(
            business_id=business_id,
            lead_id=lead_id,
            case_id=case_id,
            channel=channel,
            eligible=eligible,
            reasons=reasons,
            checks=checks,
            policy_version="v1",
        )
        session.add(row)
        session.flush()
        decision_id = row.id
    return EligibilityResult(
        eligible=eligible, reasons=reasons, checks=checks, decision_id=decision_id
    )
