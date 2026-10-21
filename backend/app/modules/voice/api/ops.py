"""Operational endpoints: jobs, audit trail, compliance decisions, stats.

    GET  /api/v1/voice/ops/stats                a summary of the voice activity
    GET  /api/v1/voice/ops/jobs                 list the background jobs
    GET  /api/v1/voice/ops/jobs/{job_id}        read one background job
    POST /api/v1/voice/ops/jobs/{job_id}/retry  re-queue a failed job
    GET  /api/v1/voice/ops/audit                read the audit trail
    GET  /api/v1/voice/ops/compliance-decisions read the compliance decisions

These endpoints exist so an operator can answer "what happened, what was
allowed, and what is stuck" without a database client.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.voice import repository as repo
from app.modules.voice.api.deps import current_business
from app.modules.voice.api.serializers import (
    audit_log_payload,
    compliance_decision_payload,
    job_payload,
    list_payload,
)
from app.modules.voice.constants import (
    AuditAction,
    HandoffStatus,
    JobStatus,
)
from app.modules.voice.errors import JobNotFoundError
from app.modules.voice.jobs import queue
from app.modules.voice.schemas import (
    AuditLogResponse,
    ComplianceDecisionResponse,
    JobResponse,
    VoiceStatsResponse,
)

router = APIRouter(
    prefix="/voice/ops",
    tags=["Voice — Operations"],
)

#: Handoff statuses that still need a human.
OPEN_HANDOFF_STATUSES = (
    HandoffStatus.REQUESTED,
    HandoffStatus.INITIATED,
)


@router.get("/stats", response_model=VoiceStatsResponse, status_code=status.HTTP_200_OK)
async def read_stats(
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
):
    """A summary of the voice activity of the business."""
    calls_by_status = repo.count_calls_by_status(db, business_id)
    consent_by_status = repo.count_consent_by_status(db, business_id)
    business = repo.get_business(db, business_id)
    tz_name = business.timezone if business is not None else "Asia/Kolkata"
    start, end = repo.day_bounds(repo.now_utc(), tz_name)
    calls_today = repo.count_calls(db, business_id, start=start, end=end)
    blocked_today = repo.count_calls(
        db, business_id, statuses=["blocked"], start=start, end=end
    )
    pending_jobs = len(
        queue.list_jobs(db, business_id=business_id, status=JobStatus.PENDING)
    )
    failed_jobs = len(
        queue.list_jobs(db, business_id=business_id, status=JobStatus.DEAD)
    ) + len(queue.list_jobs(db, business_id=business_id, status=JobStatus.FAILED))
    return {
        "business_id": business_id,
        "calls_total": sum(calls_by_status.values()),
        "calls_by_status": calls_by_status,
        "calls_today": calls_today,
        "blocked_today": blocked_today,
        "handoffs_open": repo.count_handoffs(
            db, business_id, statuses=list(OPEN_HANDOFF_STATUSES)
        ),
        "consent_granted": consent_by_status.get("granted", 0),
        "consent_missing": consent_by_status.get("missing", 0),
        "dnd_entries": repo.count_dnd_entries(db, business_id),
        "jobs_pending": pending_jobs,
        "jobs_failed": failed_jobs,
        "total_cost": repo.sum_call_cost(db, business_id),
        "currency": "INR",
    }


@router.get("/jobs", status_code=status.HTTP_200_OK)
async def list_jobs(
    business_id: str = Depends(current_business),
    job_status: Optional[str] = Query(default=None, alias="status"),
    job_type: Optional[str] = Query(default=None, alias="type"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List the background jobs of the business."""
    jobs = queue.list_jobs(
        db,
        business_id=business_id,
        status=job_status,
        job_type=job_type,
        limit=limit,
        offset=offset,
    )
    items = [job_payload(job) for job in jobs]
    return list_payload(items, business_id, limit, offset)


@router.get("/jobs/{job_id}", response_model=JobResponse, status_code=status.HTTP_200_OK)
async def read_job(
    job_id: str,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
):
    """Read one background job."""
    job = queue.get_job(db, job_id)
    if job is None or (job.business_id and job.business_id != business_id):
        raise JobNotFoundError(job_id)
    return job_payload(job)


@router.post(
    "/jobs/{job_id}/retry",
    response_model=JobResponse,
    status_code=status.HTTP_200_OK,
)
async def retry_job(
    job_id: str,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
):
    """Re-queue a failed background job.

    Retrying is explicit and audited: a job that exhausted its attempts
    is parked as ``dead`` and only ever runs again when an operator asks
    for it.
    """
    job = queue.get_job(db, job_id)
    if job is None or (job.business_id and job.business_id != business_id):
        raise JobNotFoundError(job_id)
    queue.retry_job(db, job)
    repo.add_audit_log(
        db,
        action=AuditAction.JOB_RETRIED,
        business_id=business_id,
        actor="api",
        entity_type="job",
        entity_id=job.id,
        reason_code="manual_retry",
        reason="the background job was re-queued by an operator",
        metadata={"type": job.type, "previous_status": job.status},
    )
    db.commit()
    return job_payload(job)


@router.get("/audit", status_code=status.HTTP_200_OK)
async def list_audit_logs(
    business_id: str = Depends(current_business),
    action: Optional[str] = Query(default=None),
    entity_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Read the audit trail of the business."""
    entries = repo.list_audit_logs(
        db, business_id, action=action, entity_id=entity_id, limit=limit, offset=offset
    )
    items = [audit_log_payload(entry) for entry in entries]
    return list_payload(items, business_id, limit, offset)


@router.get(
    "/compliance-decisions",
    status_code=status.HTTP_200_OK,
)
async def list_compliance_decisions(
    business_id: str = Depends(current_business),
    lead_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Read the persisted compliance decisions of the business."""
    decisions = repo.list_compliance_decisions(
        db, business_id, lead_id=lead_id, limit=limit, offset=offset
    )
    items = [compliance_decision_payload(decision) for decision in decisions]
    return list_payload(items, business_id, limit, offset)


__all__ = [
    "router",
    "AuditLogResponse",
    "ComplianceDecisionResponse",
    "JobResponse",
]
