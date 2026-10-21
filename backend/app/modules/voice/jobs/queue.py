"""Durable background job queue.

The queue lives in PostgreSQL (``voice_jobs``) so it survives restarts,
supports retries with exponential backoff and can be inspected through the
API. Redis is not involved: losing the cache must never lose a job.

Claiming a job is an atomic conditional update, which makes the queue safe
to run with several workers at once:

    UPDATE voice_jobs SET status='running' ... WHERE id=:id AND status='pending'

Only the transaction that actually changes a row owns the job.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.voice.constants import JobStatus, JobType
from app.modules.voice.models import Job

logger = logging.getLogger("callora.voice.jobs")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(
    session: Session,
    job_type: str,
    payload: Optional[Dict[str, Any]] = None,
    dedupe_key: Optional[str] = None,
    max_attempts: int = 5,
    delay_seconds: float = 0.0,
    job_id: Optional[str] = None,
    business_id: Optional[str] = None,
) -> Tuple[Optional[Job], bool]:
    """Add a job to the queue.

    Returns ``(job, created)``. When a ``dedupe_key`` is supplied and a job
    with that key already exists, the existing job is returned and
    ``created`` is ``False`` — this is what keeps a retried webhook from
    producing a second post-call job.
    """
    if dedupe_key:
        existing = session.execute(
            select(Job).where(Job.dedupe_key == dedupe_key).limit(1)
        ).scalars().first()
        if existing is not None:
            return existing, False

    now = utcnow()
    job = Job(
        id=job_id or None,
        type=job_type,
        business_id=business_id or (payload or {}).get("business_id"),
        payload=payload or {},
        status=JobStatus.PENDING,
        max_attempts=max_attempts,
        dedupe_key=dedupe_key,
        scheduled_at=now + timedelta(seconds=max(0.0, delay_seconds)),
    )
    if job.id is None:
        from app.modules.voice.models import prefixed_id

        job.id = prefixed_id("job")
    session.add(job)
    try:
        with session.begin_nested():
            session.flush()
        return job, True
    except IntegrityError:
        # Another writer inserted the same dedupe key concurrently.
        existing = session.execute(
            select(Job).where(Job.dedupe_key == dedupe_key).limit(1)
        ).scalars().first()
        return existing, False


def claim_next(
    session: Session,
    worker_id: str,
    job_types: Optional[List[str]] = None,
    now: Optional[datetime] = None,
) -> Optional[Job]:
    """Atomically claim the next runnable job.

    Returns the claimed job (already marked ``running``) or ``None`` when
    there is nothing to do.
    """
    now = now or utcnow()
    stmt = select(Job).where(Job.status == JobStatus.PENDING, Job.scheduled_at <= now)
    if job_types:
        stmt = stmt.where(Job.type.in_(job_types))
    stmt = stmt.order_by(Job.scheduled_at.asc(), Job.created_at.asc()).limit(20)

    if session.bind is not None and session.bind.dialect.name == "postgresql":
        # Skip rows another worker is already locking.
        stmt = stmt.with_for_update(skip_locked=True)

    candidates = list(session.execute(stmt).scalars().all())
    for candidate in candidates:
        result = session.execute(
            update(Job)
            .where(Job.id == candidate.id, Job.status == JobStatus.PENDING)
            .values(
                status=JobStatus.RUNNING,
                locked_at=now,
                locked_by=worker_id,
                attempts=Job.attempts + 1,
                updated_at=now,
            )
        )
        if result.rowcount == 1:
            session.commit()
            session.expire_all()
            claimed = session.get(Job, candidate.id)
            return claimed
        # Another worker won the race — try the next candidate.
        session.expire_all()
    session.commit()
    return None


def mark_succeeded(
    session: Session, job: Job, result: Optional[Dict[str, Any]] = None
) -> Job:
    """Mark a job as successfully processed."""
    job.status = JobStatus.SUCCEEDED
    job.result = result
    job.last_error = None
    job.locked_at = None
    job.locked_by = None
    job.updated_at = utcnow()
    session.flush()
    return job


def mark_failed(
    session: Session,
    job: Job,
    error: str,
    retryable: bool = True,
    backoff_seconds: float = 10.0,
    max_backoff_seconds: float = 3600.0,
) -> Job:
    """Record a failure and decide whether the job is retried.

    A job that exhausts its attempts is parked as ``dead`` so an operator
    can inspect and retry it explicitly.
    """
    now = utcnow()
    job.last_error = error[:4000]
    job.updated_at = now
    if not retryable or job.attempts >= job.max_attempts:
        job.status = JobStatus.DEAD if retryable else JobStatus.FAILED
        job.locked_at = None
        job.locked_by = None
        session.flush()
        logger.warning(
            "job_failed_permanently",
            extra={"job_id": job.id, "type": job.type, "attempts": job.attempts},
        )
        return job
    delay = min(max_backoff_seconds, backoff_seconds * (2 ** max(0, job.attempts - 1)))
    job.status = JobStatus.PENDING
    job.scheduled_at = now + timedelta(seconds=delay)
    job.locked_at = None
    job.locked_by = None
    session.flush()
    logger.info(
        "job_retry_scheduled",
        extra={"job_id": job.id, "type": job.type, "delay_seconds": delay},
    )
    return job


def retry_job(session: Session, job: Job, reset_attempts: bool = True) -> Job:
    """Put a failed/dead job back into the queue."""
    job.status = JobStatus.PENDING
    job.last_error = None
    job.scheduled_at = utcnow()
    job.locked_at = None
    job.locked_by = None
    if reset_attempts:
        job.attempts = 0
    job.updated_at = utcnow()
    session.flush()
    return job


def release_stale_jobs(session: Session, older_than_seconds: int = 900) -> int:
    """Return jobs that a crashed worker left in ``running`` to the queue."""
    cutoff = utcnow() - timedelta(seconds=older_than_seconds)
    result = session.execute(
        update(Job)
        .where(Job.status == JobStatus.RUNNING, Job.locked_at < cutoff)
        .values(
            status=JobStatus.PENDING,
            scheduled_at=utcnow(),
            locked_at=None,
            locked_by=None,
            updated_at=utcnow(),
        )
    )
    session.flush()
    return int(result.rowcount or 0)


def list_jobs(
    session: Session,
    business_id: Optional[str] = None,
    status: Optional[str] = None,
    job_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Job]:
    stmt = select(Job)
    if business_id:
        stmt = stmt.where(Job.business_id == business_id)
    if status:
        stmt = stmt.where(Job.status == status)
    if job_type:
        stmt = stmt.where(Job.type == job_type)
    stmt = stmt.order_by(Job.created_at.desc()).limit(limit).offset(offset)
    return list(session.execute(stmt).scalars().all())


def get_job(session: Session, job_id: str) -> Optional[Job]:
    return session.get(Job, job_id)


def postcall_dedupe_key(call_id: str, status: str) -> str:
    """Dedupe key for the post-call job of one call in one terminal state."""
    return f"postcall:{call_id}:{status}"


__all__ = [
    "JobType",
    "enqueue",
    "claim_next",
    "mark_succeeded",
    "mark_failed",
    "retry_job",
    "release_stale_jobs",
    "list_jobs",
    "get_job",
    "postcall_dedupe_key",
]
