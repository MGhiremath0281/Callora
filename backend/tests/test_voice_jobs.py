"""Tests for the durable background job queue.

The queue lives in PostgreSQL so it survives restarts, and a job may be
claimed by exactly one worker. These tests cover the dedupe key, the
atomic claim, the retry/backoff policy, the recovery of jobs a crashed
worker left behind and the worker's own failure handling.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.db.session import get_session_factory
from app.modules.voice.constants import JobStatus, JobType
from app.modules.voice.jobs import queue
from app.modules.voice.jobs.worker import Worker
from app.modules.voice.models import Job
from tests.conftest import VOICE_BIZ


def _now():
    return datetime.now(timezone.utc)


def _fresh(job_id):
    """Read a job in a new session (the worker uses its own connection)."""
    session = get_session_factory()()
    try:
        return session.get(Job, job_id)
    finally:
        session.close()


def _enqueue(db, **kwargs):
    params = dict(
        job_type=JobType.POSTCALL_PROCESS,
        payload={"call_id": "call_1", "business_id": VOICE_BIZ},
        business_id=VOICE_BIZ,
    )
    params.update(kwargs)
    job, created = queue.enqueue(db, **params)
    db.commit()
    return job, created


# ---------------------------------------------------------------------------
# Enqueueing
# ---------------------------------------------------------------------------


class TestEnqueue:
    def test_a_job_is_created(self, db):
        job, created = _enqueue(db)
        assert created is True
        assert job.id
        assert job.status == JobStatus.PENDING
        assert job.attempts == 0
        assert job.business_id == VOICE_BIZ

    def test_a_dedupe_key_prevents_a_second_job(self, db):
        first, created_first = _enqueue(db, dedupe_key="postcall:call_1:completed")
        second, created_second = _enqueue(db, dedupe_key="postcall:call_1:completed")
        assert created_first is True
        assert created_second is False
        assert second.id == first.id
        assert db.query(Job).count() == 1

    def test_different_dedupe_keys_create_different_jobs(self, db):
        _enqueue(db, dedupe_key="postcall:call_1:completed")
        _enqueue(db, dedupe_key="postcall:call_1:no_answer")
        assert db.query(Job).count() == 2

    def test_a_delayed_job_is_not_runnable_yet(self, db):
        job, _created = _enqueue(db, delay_seconds=600)
        assert job.scheduled_at > _now()
        assert queue.claim_next(db, "worker-1") is None

    def test_the_dedupe_key_of_a_post_call_job(self):
        assert (
            queue.postcall_dedupe_key("call_1", "completed")
            == "postcall:call_1:completed"
        )


# ---------------------------------------------------------------------------
# Claiming
# ---------------------------------------------------------------------------


class TestClaim:
    def test_a_pending_job_is_claimed(self, db):
        job, _created = _enqueue(db)
        claimed = queue.claim_next(db, "worker-1")
        assert claimed is not None
        assert claimed.id == job.id
        assert claimed.status == JobStatus.RUNNING
        assert claimed.attempts == 1
        assert claimed.locked_by == "worker-1"

    def test_nothing_is_claimed_twice(self, db):
        _enqueue(db)
        first = queue.claim_next(db, "worker-1")
        second = queue.claim_next(db, "worker-2")
        assert first is not None
        assert second is None

    def test_an_empty_queue_yields_nothing(self, db):
        assert queue.claim_next(db, "worker-1") is None

    def test_the_job_type_can_be_filtered(self, db):
        _enqueue(db, job_type="postcall.process")
        assert queue.claim_next(db, "worker-1", job_types=["other.type"]) is None
        assert queue.claim_next(db, "worker-1", job_types=["postcall.process"]) is not None

    def test_a_scheduled_job_is_claimed_once_it_is_due(self, db):
        job, _created = _enqueue(db, delay_seconds=600)
        assert queue.claim_next(db, "worker-1") is None
        job.scheduled_at = _now() - timedelta(seconds=1)
        db.commit()
        assert queue.claim_next(db, "worker-1") is not None


# ---------------------------------------------------------------------------
# Failures and retries
# ---------------------------------------------------------------------------


class TestFailureHandling:
    def test_a_retryable_failure_is_rescheduled(self, db):
        job, _created = _enqueue(db)
        queue.claim_next(db, "worker-1")
        queue.mark_failed(db, job, "the model timed out", retryable=True, backoff_seconds=10)
        db.commit()
        assert job.status == JobStatus.PENDING
        assert job.last_error == "the model timed out"
        assert job.scheduled_at > _now()
        assert job.locked_by is None

    def test_the_backoff_grows_with_every_attempt(self, db):
        job, _created = _enqueue(db)
        job.attempts = 1
        queue.mark_failed(db, job, "boom", retryable=True, backoff_seconds=10)
        first_delay = (job.scheduled_at - _now()).total_seconds()
        job.attempts = 2
        queue.mark_failed(db, job, "boom", retryable=True, backoff_seconds=10)
        second_delay = (job.scheduled_at - _now()).total_seconds()
        assert second_delay > first_delay

    def test_a_job_that_exhausts_its_attempts_is_parked_as_dead(self, db):
        job, _created = _enqueue(db, max_attempts=2)
        job.attempts = 2
        queue.mark_failed(db, job, "boom", retryable=True)
        db.commit()
        assert job.status == JobStatus.DEAD
        assert job.locked_by is None

    def test_a_non_retryable_failure_fails_immediately(self, db):
        job, _created = _enqueue(db)
        job.attempts = 1
        queue.mark_failed(db, job, "bad payload", retryable=False)
        db.commit()
        assert job.status == JobStatus.FAILED

    def test_a_successful_job_is_marked_succeeded(self, db):
        job, _created = _enqueue(db)
        queue.claim_next(db, "worker-1")
        queue.mark_succeeded(db, job, {"disposition": "qualified"})
        db.commit()
        assert job.status == JobStatus.SUCCEEDED
        assert job.result == {"disposition": "qualified"}
        assert job.last_error is None

    def test_a_dead_job_can_be_retried_by_an_operator(self, db):
        job, _created = _enqueue(db, max_attempts=1)
        job.attempts = 1
        queue.mark_failed(db, job, "boom", retryable=True)
        db.commit()
        assert job.status == JobStatus.DEAD
        queue.retry_job(db, job)
        db.commit()
        assert job.status == JobStatus.PENDING
        assert job.attempts == 0
        assert job.last_error is None

    def test_a_stale_running_job_is_released(self, db):
        job, _created = _enqueue(db)
        queue.claim_next(db, "worker-1")
        job.locked_at = _now() - timedelta(minutes=30)
        db.commit()
        released = queue.release_stale_jobs(db, older_than_seconds=900)
        db.commit()
        assert released == 1
        assert job.status == JobStatus.PENDING
        assert job.locked_by is None


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------


class TestWorker:
    @pytest.fixture()
    def worker(self, voice_settings):
        return Worker(
            voice_settings,
            session_factory=get_session_factory(),
            handlers={},
            worker_id="worker-test",
        )

    def test_a_successful_job_is_processed(self, worker, db):
        job, _created = _enqueue(db)
        db.rollback()
        worker._handlers = {
            JobType.POSTCALL_PROCESS: lambda session, job, settings: {"ok": True}
        }
        assert worker.run_once() == 1
        stored = _fresh(job.id)
        assert stored.status == JobStatus.SUCCEEDED
        assert stored.result == {"ok": True}

    def test_a_failing_job_is_retried(self, worker, db):
        job, _created = _enqueue(db)
        db.rollback()

        def _boom(session, job, settings):
            raise RuntimeError("the model is unavailable")

        worker._handlers = {JobType.POSTCALL_PROCESS: _boom}
        assert worker.run_once() == 1
        stored = _fresh(job.id)
        assert stored.status == JobStatus.PENDING
        assert stored.attempts == 1
        assert "the model is unavailable" in stored.last_error

    def test_a_non_retryable_failure_is_not_rescheduled(self, worker, db):
        job, _created = _enqueue(db)
        db.rollback()

        def _boom(session, job, settings):
            raise ValueError("the job payload is invalid")

        worker._handlers = {JobType.POSTCALL_PROCESS: _boom}
        worker.run_once()
        stored = _fresh(job.id)
        assert stored.status == JobStatus.FAILED
        assert stored.attempts == 1

    def test_an_unknown_job_type_fails_permanently(self, worker, db):
        job, _created = _enqueue(db, job_type="unknown.type")
        db.rollback()
        worker.run_once()
        stored = _fresh(job.id)
        assert stored.status == JobStatus.FAILED
        assert "no handler" in stored.last_error

    def test_an_empty_queue_processes_nothing(self, worker, db):
        assert worker.run_once() == 0

    def test_the_worker_only_runs_the_jobs_it_owns(self, voice_settings, db):
        _enqueue(db, job_type="postcall.process")
        _enqueue(db, job_type="other.type")
        db.rollback()
        worker = Worker(
            voice_settings,
            session_factory=get_session_factory(),
            handlers={},
            worker_id="worker-test",
            job_types=["other.type"],
        )
        worker._handlers = {"other.type": lambda session, job, settings: {}}
        assert worker.run_once() == 1
        remaining = queue.list_jobs(db, status=JobStatus.PENDING)
        assert [job.type for job in remaining] == ["postcall.process"]
