"""Tests for the status reconciliation between the provider and Callora.

The provider webhook is the fast path, but it was the *only* path: a
lost webhook used to freeze a call in ``scheduled`` for good. The
reconciliation sweep is the safety net, and it has to be careful:

* the provider is asked **before** anything is changed;
* a call is only failed when the provider confirms the failure, or when
  the documented timeout policy applies to a call that never got past
  the dial queue;
* a live conversation is never closed because time passed;
* an unknown provider status never terminates a call;
* a terminal status releases the duplicate-call guard, an active call
  keeps it.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.modules.voice import repository as repo
from app.modules.voice import sync
from app.modules.voice.constants import (
    IN_FLIGHT_STATUSES,
    TERMINAL_STATUSES,
    AuditAction,
    CallStatus,
    JobType,
    map_provider_status,
)
from app.modules.voice.errors import (
    ProviderConnectionError,
    ProviderError,
    ProviderExecutionNotFoundError,
)
from app.modules.voice.jobs import queue
from app.modules.voice.lifecycle import release_in_flight_guard
from app.modules.voice.models import Call
from tests.conftest import VOICE_BIZ, VOICE_LEAD_ID, VOICE_PHONE, auth_headers
from tests.fakes import FakeProvider


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture()
def sync_settings(voice_settings, monkeypatch):
    """Settings for the reconciliation tests.

    The poll interval is zero so that every call is due immediately; the
    timeout is the production default (15 minutes).
    """
    monkeypatch.setenv("CALLORA_VOICE_STATUS_POLL_INTERVAL_SECONDS", "0")
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture()
def call_factory(db, voice_lead):
    """Create a call the provider already knows about."""

    def _make(
        status: str,
        *,
        execution_id: str = "exec_sync_1",
        scheduled_at=None,
        created_at=None,
        business_id: str = VOICE_BIZ,
        lead_id: str = VOICE_LEAD_ID,
    ) -> Call:
        row = Call(
            business_id=business_id,
            lead_id=lead_id,
            provider="fake",
            provider_call_id=execution_id,
            status=status,
            to_number=VOICE_PHONE,
            scheduled_at=scheduled_at,
            created_at=created_at or (_now() - timedelta(hours=2)),
        )
        db.add(row)
        db.commit()
        return row

    return _make


@pytest.fixture()
def provider():
    return FakeProvider()


# ---------------------------------------------------------------------------
# The provider status vocabulary
# ---------------------------------------------------------------------------


class TestStatusMapping:
    """Every status Bolna can report has to map onto the internal one."""

    @pytest.mark.parametrize(
        "provider_status,expected",
        [
            ("scheduled", CallStatus.SCHEDULED),
            ("prepared", CallStatus.QUEUED),
            ("queued", CallStatus.QUEUED),
            ("rescheduled", CallStatus.SCHEDULED),
            ("initiated", CallStatus.INITIATED),
            ("ringing", CallStatus.RINGING),
            ("in-progress", CallStatus.IN_PROGRESS),
            ("connected", CallStatus.IN_PROGRESS),
            ("active", CallStatus.IN_PROGRESS),
            ("call-disconnected", CallStatus.DISCONNECTED),
            ("completed", CallStatus.COMPLETED),
            ("no-answer", CallStatus.NO_ANSWER),
            ("busy", CallStatus.BUSY),
            ("failed", CallStatus.FAILED),
            ("canceled", CallStatus.CANCELED),
            ("stopped", CallStatus.STOPPED),
            ("error", CallStatus.ERROR),
            ("balance-low", CallStatus.BALANCE_LOW),
        ],
    )
    def test_a_known_status_is_mapped(self, provider_status, expected):
        assert map_provider_status(provider_status) == expected

    def test_an_unknown_status_is_not_terminal(self):
        assert map_provider_status("wobbled") == CallStatus.UNKNOWN
        assert CallStatus.UNKNOWN not in TERMINAL_STATUSES

    def test_an_empty_status_is_not_terminal(self):
        assert map_provider_status("") == CallStatus.UNKNOWN

    def test_the_mapping_is_idempotent(self):
        for status in TERMINAL_STATUSES | {
            CallStatus.SCHEDULED,
            CallStatus.QUEUED,
            CallStatus.INITIATED,
            CallStatus.RINGING,
            CallStatus.IN_PROGRESS,
            CallStatus.DISCONNECTED,
        }:
            assert map_provider_status(status) == status

    def test_every_terminal_status_is_out_of_the_in_flight_set(self):
        assert not (TERMINAL_STATUSES & IN_FLIGHT_STATUSES)


# ---------------------------------------------------------------------------
# The happy paths
# ---------------------------------------------------------------------------


class TestQueuedCall:
    """A call the provider accepted and that is waiting in the dial queue."""

    def test_the_provider_status_is_applied(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "initiated"
        call = call_factory(CallStatus.QUEUED)

        outcome = sync.sync_call(db, sync_settings, call, provider)

        assert outcome.action == sync.ACTION_APPLIED
        assert call.status == CallStatus.INITIATED
        assert provider.get_calls == ["exec_sync_1"]

    def test_a_non_terminal_status_queues_no_work(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "ringing"
        call = call_factory(CallStatus.QUEUED)

        sync.sync_call(db, sync_settings, call, provider)

        assert queue.list_jobs(db, business_id=VOICE_BIZ) == []

    def test_the_transition_is_audited(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "ringing"
        call = call_factory(CallStatus.QUEUED)

        sync.sync_call(db, sync_settings, call, provider)

        entries = repo.list_audit_logs(db, VOICE_BIZ)
        updated = [
            e for e in entries if e.action == AuditAction.CALL_STATUS_UPDATED
        ]
        assert len(updated) == 1
        assert updated[0].audit_metadata["previous_status"] == CallStatus.QUEUED
        assert updated[0].audit_metadata["call_status"] == CallStatus.RINGING


class TestSuccessfulCall:
    """A call the provider reports as completed."""

    def test_the_call_completes(self, sync_settings, db, call_factory, provider):
        provider._status = "completed"
        call = call_factory(CallStatus.IN_PROGRESS)

        outcome = sync.sync_call(db, sync_settings, call, provider)

        assert outcome.action == sync.ACTION_APPLIED
        assert call.status == CallStatus.COMPLETED
        assert call.ended_at is not None

    def test_the_post_call_work_is_queued(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "completed"
        call = call_factory(CallStatus.IN_PROGRESS)

        sync.sync_call(db, sync_settings, call, provider)

        jobs = queue.list_jobs(db, business_id=VOICE_BIZ)
        assert len(jobs) == 1
        assert jobs[0].type == JobType.POSTCALL_PROCESS
        assert jobs[0].payload["call_id"] == call.id

    def test_the_provider_document_is_stored(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "completed"
        call = call_factory(CallStatus.IN_PROGRESS)

        sync.sync_call(db, sync_settings, call, provider)

        assert call.provider_payload["status"] == "completed"

    def test_the_sweep_queues_the_post_call_work_once(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "completed"
        call = call_factory(CallStatus.IN_PROGRESS)

        sync.sweep(db, sync_settings, provider=provider)
        sync.sweep(db, sync_settings, provider=provider)

        jobs = queue.list_jobs(db, business_id=VOICE_BIZ)
        assert len(jobs) == 1


class TestProviderFailure:
    """A call the provider reports as failed."""

    def test_the_call_fails(self, sync_settings, db, call_factory, provider):
        provider._status = "failed"
        call = call_factory(CallStatus.RINGING)

        outcome = sync.sync_call(db, sync_settings, call, provider)

        assert outcome.action == sync.ACTION_APPLIED
        assert call.status == CallStatus.FAILED
        assert call.ended_at is not None

    def test_the_failure_is_audited(self, sync_settings, db, call_factory, provider):
        provider._status = "failed"
        call = call_factory(CallStatus.RINGING)

        sync.sync_call(db, sync_settings, call, provider)

        entries = repo.list_audit_logs(db, VOICE_BIZ)
        updated = [e for e in entries if e.action == AuditAction.CALL_STATUS_UPDATED]
        assert updated[0].audit_metadata["call_status"] == CallStatus.FAILED

    def test_a_terminal_failure_queues_the_post_call_work(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "failed"
        call = call_factory(CallStatus.RINGING)

        sync.sync_call(db, sync_settings, call, provider)

        jobs = queue.list_jobs(db, business_id=VOICE_BIZ)
        assert len(jobs) == 1

    def test_a_call_the_provider_does_not_know_any_more_fails(
        self, sync_settings, db, call_factory
    ):
        provider = FakeProvider(
            fail_with=ProviderExecutionNotFoundError(
                "Bolna does not know the execution exec_sync_1"
            )
        )
        call = call_factory(CallStatus.QUEUED)

        outcome = sync.sync_call(db, sync_settings, call, provider)

        assert outcome.action == sync.ACTION_EXECUTION_MISSING
        assert call.status == CallStatus.FAILED
        assert "does not know" in (call.error_message or "")


# ---------------------------------------------------------------------------
# The stuck-call timeout
# ---------------------------------------------------------------------------


class TestStaleScheduledCall:
    """A call that never got past the dial queue."""

    @pytest.fixture()
    def stale(self, call_factory):
        # Due 30 minutes ago, which is past the 15 minute timeout.
        return call_factory(
            CallStatus.SCHEDULED,
            scheduled_at=_now() - timedelta(minutes=30),
        )

    def test_the_provider_is_asked_before_anything_changes(
        self, sync_settings, db, stale, provider
    ):
        provider._status = "scheduled"

        sync.sync_call(db, sync_settings, stale, provider)

        # The provider was polled first, and the call is only closed
        # because the provider confirmed it is still waiting.
        assert provider.get_calls == ["exec_sync_1"]

    def test_a_stuck_call_is_failed(self, sync_settings, db, stale, provider):
        provider._status = "scheduled"

        outcome = sync.sync_call(db, sync_settings, stale, provider)

        assert outcome.action == sync.ACTION_TIMEOUT
        assert stale.status == CallStatus.FAILED
        assert stale.ended_at is not None
        assert "scheduled" in (stale.error_message or "")

    def test_the_timeout_is_audited(self, sync_settings, db, stale, provider):
        provider._status = "scheduled"

        sync.sync_call(db, sync_settings, stale, provider)

        entries = repo.list_audit_logs(db, VOICE_BIZ)
        timeouts = [e for e in entries if e.action == AuditAction.CALL_TIMEOUT]
        assert len(timeouts) == 1
        assert timeouts[0].audit_metadata["provider_status"] == "scheduled"
        assert timeouts[0].audit_metadata["execution_id"] == "exec_sync_1"

    def test_the_timeout_queues_the_post_call_work(
        self, sync_settings, db, stale, provider
    ):
        provider._status = "scheduled"

        sync.sync_call(db, sync_settings, stale, provider)

        jobs = queue.list_jobs(db, business_id=VOICE_BIZ)
        assert len(jobs) == 1

    def test_a_call_that_is_not_stuck_yet_is_left_alone(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "scheduled"
        call = call_factory(
            CallStatus.SCHEDULED,
            scheduled_at=_now() - timedelta(minutes=2),
        )

        outcome = sync.sync_call(db, sync_settings, call, provider)

        assert outcome.action == sync.ACTION_UNCHANGED
        assert call.status == CallStatus.SCHEDULED
        assert queue.list_jobs(db, business_id=VOICE_BIZ) == []

    def test_the_keep_polling_policy_never_closes_a_call(
        self, sync_settings, db, stale, provider, monkeypatch
    ):
        monkeypatch.setenv("CALLORA_VOICE_STUCK_CALL_TIMEOUT_ACTION", "keep_polling")
        get_settings.cache_clear()
        settings = get_settings()
        provider._status = "scheduled"

        outcome = sync.sync_call(db, settings, stale, provider)

        assert outcome.action == sync.ACTION_UNCHANGED
        assert stale.status == CallStatus.SCHEDULED


class TestTimeoutNeedsTheProvider:
    """The timeout policy may only act on a provider confirmation."""

    def test_an_unreachable_provider_leaves_the_call_alone(
        self, sync_settings, db, call_factory
    ):
        provider = FakeProvider(fail_with=ProviderConnectionError("no network"))
        call = call_factory(
            CallStatus.SCHEDULED,
            scheduled_at=_now() - timedelta(minutes=30),
        )

        outcome = sync.sync_call(db, sync_settings, call, provider)

        assert outcome.action == sync.ACTION_PROVIDER_UNREACHABLE
        assert call.status == CallStatus.SCHEDULED
        assert call.ended_at is None
        assert queue.list_jobs(db, business_id=VOICE_BIZ) == []

    def test_a_provider_error_leaves_the_call_alone(
        self, sync_settings, db, call_factory
    ):
        provider = FakeProvider(fail_with=ProviderError("HTTP 500"))
        call = call_factory(
            CallStatus.SCHEDULED,
            scheduled_at=_now() - timedelta(minutes=30),
        )

        outcome = sync.sync_call(db, sync_settings, call, provider)

        assert outcome.action == sync.ACTION_PROVIDER_ERROR
        assert call.status == CallStatus.SCHEDULED

    def test_an_unknown_status_leaves_the_call_alone(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "wobbled"
        call = call_factory(
            CallStatus.SCHEDULED,
            scheduled_at=_now() - timedelta(minutes=30),
        )

        outcome = sync.sync_call(db, sync_settings, call, provider)

        assert outcome.action == sync.ACTION_UNKNOWN_STATUS
        assert call.status == CallStatus.SCHEDULED

    def test_a_call_the_provider_reports_as_running_is_not_failed(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "initiated"
        call = call_factory(
            CallStatus.SCHEDULED,
            scheduled_at=_now() - timedelta(minutes=30),
        )

        outcome = sync.sync_call(db, sync_settings, call, provider)

        assert outcome.action == sync.ACTION_APPLIED
        assert call.status == CallStatus.INITIATED


class TestActiveCallIsNeverFailed:
    """A live conversation is only ever ended by the provider."""

    @pytest.mark.parametrize(
        "status",
        [CallStatus.INITIATED, CallStatus.RINGING, CallStatus.IN_PROGRESS],
    )
    def test_an_active_call_is_never_failed_by_the_timeout(
        self, sync_settings, db, call_factory, provider, status
    ):
        provider._status = status
        call = call_factory(status, scheduled_at=_now() - timedelta(hours=5))

        outcome = sync.sync_call(db, sync_settings, call, provider)

        assert call.status == status
        assert outcome.action == sync.ACTION_UNCHANGED


# ---------------------------------------------------------------------------
# Unknown, stale and reordered events
# ---------------------------------------------------------------------------


class TestUnknownProviderStatus:
    def test_an_unknown_status_does_not_change_the_call(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "wobbled"
        call = call_factory(CallStatus.QUEUED)

        outcome = sync.sync_call(db, sync_settings, call, provider)

        assert outcome.action == sync.ACTION_UNKNOWN_STATUS
        assert call.status == CallStatus.QUEUED
        assert call.ended_at is None

    def test_an_unknown_status_queues_no_post_call_work(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "wobbled"
        call = call_factory(CallStatus.QUEUED)

        sync.sync_call(db, sync_settings, call, provider)

        assert queue.list_jobs(db, business_id=VOICE_BIZ) == []

    def test_an_unknown_status_keeps_the_in_flight_guard(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "wobbled"
        call = call_factory(CallStatus.QUEUED)

        sync.sync_call(db, sync_settings, call, provider)

        assert release_in_flight_guard(call) is False
        assert repo.count_in_flight_calls(db, VOICE_BIZ) == 1


class TestOutOfOrder:
    def test_an_older_status_never_moves_the_call_backwards(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "ringing"
        call = call_factory(CallStatus.IN_PROGRESS)

        outcome = sync.sync_call(db, sync_settings, call, provider)

        assert outcome.action == sync.ACTION_OUT_OF_ORDER
        assert call.status == CallStatus.IN_PROGRESS

    def test_a_terminal_status_is_never_overwritten(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "ringing"
        call = call_factory(CallStatus.COMPLETED)

        outcome = sync.sync_call(db, sync_settings, call, provider)

        assert call.status == CallStatus.COMPLETED
        assert outcome.action == sync.ACTION_OUT_OF_ORDER


# ---------------------------------------------------------------------------
# The in-flight guard
# ---------------------------------------------------------------------------


class TestInFlightGuard:
    def test_a_terminal_status_releases_the_guard(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "completed"
        call = call_factory(CallStatus.IN_PROGRESS)
        assert repo.count_in_flight_calls(db, VOICE_BIZ) == 1

        sync.sync_call(db, sync_settings, call, provider)

        assert release_in_flight_guard(call) is True
        assert repo.count_in_flight_calls(db, VOICE_BIZ) == 0

    def test_an_active_call_keeps_the_guard(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "in-progress"
        call = call_factory(CallStatus.RINGING)

        sync.sync_call(db, sync_settings, call, provider)

        assert release_in_flight_guard(call) is False
        assert repo.count_in_flight_calls(db, VOICE_BIZ) == 1

    def test_a_dropped_line_releases_the_guard(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "call-disconnected"
        call = call_factory(CallStatus.IN_PROGRESS)

        sync.sync_call(db, sync_settings, call, provider)

        assert call.status == CallStatus.DISCONNECTED
        assert release_in_flight_guard(call) is True


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


class TestSweep:
    def test_a_call_that_is_not_due_yet_is_not_polled(
        self, sync_settings, db, call_factory, provider
    ):
        call = call_factory(
            CallStatus.SCHEDULED,
            scheduled_at=_now() + timedelta(hours=1),
        )

        report = sync.sweep(db, sync_settings, provider=provider)

        assert report.checked == 0
        assert report.skipped == 1
        assert provider.get_calls == []
        assert call.status == CallStatus.SCHEDULED

    def test_a_due_call_is_polled(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "initiated"
        call = call_factory(CallStatus.QUEUED)

        report = sync.sweep(db, sync_settings, provider=provider)

        assert report.checked == 1
        assert report.applied == 1
        assert call.status == CallStatus.INITIATED

    def test_the_sweep_only_touches_the_business(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "initiated"
        mine = call_factory(CallStatus.QUEUED, execution_id="exec_mine")
        theirs = call_factory(
            CallStatus.QUEUED,
            execution_id="exec_theirs",
            business_id="biz_other",
        )

        sync.sweep(db, sync_settings, provider=provider, business_id=VOICE_BIZ)

        assert mine.status == CallStatus.INITIATED
        assert theirs.status == CallStatus.QUEUED
        assert provider.get_calls == ["exec_mine"]

    def test_the_sweep_counts_what_it_did(
        self, sync_settings, db, call_factory, provider
    ):
        provider._status = "initiated"
        call_factory(CallStatus.QUEUED, execution_id="exec_a")
        call_factory(CallStatus.QUEUED, execution_id="exec_b")

        report = sync.sweep(db, sync_settings, provider=provider)

        assert report.checked == 2
        assert report.applied == 2
        assert report.failed == 0

    def test_a_call_that_was_never_placed_is_left_alone(
        self, sync_settings, db, call_factory, provider
    ):
        call = call_factory(CallStatus.PENDING)
        call.provider_call_id = None
        db.commit()

        outcome = sync.sync_call(db, sync_settings, call, provider)

        assert outcome.action == sync.ACTION_NOT_PLACED
        assert provider.get_calls == []


# ---------------------------------------------------------------------------
# The background worker
# ---------------------------------------------------------------------------


class TestWorker:
    def test_the_worker_runs_the_sweep(
        self, sync_settings, db, call_factory, provider
    ):
        from app.db.session import get_session_factory
        from app.modules.voice.jobs.worker import Worker

        provider._status = "initiated"
        call = call_factory(CallStatus.QUEUED)

        worker = Worker(
            settings=sync_settings,
            session_factory=get_session_factory(),
            provider=provider,
        )
        worker.run_sync()

        db.expire_all()
        assert db.get(Call, call.id).status == CallStatus.INITIATED

    def test_the_sweep_is_not_run_twice_in_a_row(
        self, sync_settings, db, call_factory, provider, monkeypatch
    ):
        from app.modules.voice.jobs.worker import Worker

        provider._status = "initiated"
        call_factory(CallStatus.QUEUED)
        monkeypatch.setenv("CALLORA_VOICE_STATUS_SYNC_INTERVAL_SECONDS", "600")
        get_settings.cache_clear()
        settings = get_settings()

        calls = []
        original = sync.sweep

        def _counting_sweep(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(sync, "sweep", _counting_sweep)

        worker = Worker(settings=settings, session_factory=lambda: db)
        worker._maybe_sync()
        worker._maybe_sync()

        assert len(calls) == 1


class TestStatusSyncJob:
    """The sweep is also available as a queued background job."""

    def test_the_job_runs_the_sweep(
        self, sync_settings, db, call_factory, provider, monkeypatch
    ):
        from app.modules.voice.jobs.handlers import handle_status_sync

        provider._status = "initiated"
        call = call_factory(CallStatus.QUEUED)
        monkeypatch.setattr(
            "app.modules.voice.sync.build_provider", lambda *a, **k: provider
        )
        job = queue.enqueue(
            db,
            JobType.STATUS_SYNC,
            payload={"business_id": VOICE_BIZ},
            business_id=VOICE_BIZ,
        )

        result = handle_status_sync(db, job[0], sync_settings)

        assert result["checked"] == 1
        assert result["applied"] == 1
        assert call.status == CallStatus.INITIATED

    def test_the_job_is_scoped_to_its_business(
        self, sync_settings, db, call_factory, provider, monkeypatch
    ):
        from app.modules.voice.jobs.handlers import handle_status_sync

        provider._status = "initiated"
        theirs = call_factory(
            CallStatus.QUEUED,
            execution_id="exec_theirs",
            business_id="biz_other",
        )
        monkeypatch.setattr(
            "app.modules.voice.sync.build_provider", lambda *a, **k: provider
        )
        job = queue.enqueue(
            db,
            JobType.STATUS_SYNC,
            payload={"business_id": VOICE_BIZ},
            business_id=VOICE_BIZ,
        )

        result = handle_status_sync(db, job[0], sync_settings)

        assert result["checked"] == 0
        assert theirs.status == CallStatus.QUEUED


# ---------------------------------------------------------------------------
# The operator endpoint
# ---------------------------------------------------------------------------


class TestOpsEndpoint:
    def test_the_sweep_can_be_triggered_by_hand(
        self, sync_settings, db, call_factory, provider, monkeypatch, voice_client
    ):
        provider._status = "initiated"
        call = call_factory(CallStatus.QUEUED)
        monkeypatch.setattr(
            "app.modules.voice.sync.build_provider", lambda *a, **k: provider
        )

        response = voice_client.post(
            "/api/v1/voice/ops/sync-calls", headers=auth_headers()
        )

        assert response.status_code == 200
        body = response.json()
        assert body["checked"] == 1
        assert body["applied"] == 1
        assert body["outcomes"][0]["call_id"] == call.id
        db.expire_all()
        assert db.get(Call, call.id).status == CallStatus.INITIATED

    def test_the_sweep_is_scoped_to_the_business(
        self, sync_settings, db, call_factory, provider, monkeypatch, voice_client
    ):
        provider._status = "initiated"
        theirs = call_factory(
            CallStatus.QUEUED,
            execution_id="exec_theirs",
            business_id="biz_other",
        )
        monkeypatch.setattr(
            "app.modules.voice.sync.build_provider", lambda *a, **k: provider
        )

        response = voice_client.post(
            "/api/v1/voice/ops/sync-calls", headers=auth_headers()
        )

        assert response.status_code == 200
        assert response.json()["checked"] == 0
        assert theirs.status == CallStatus.QUEUED
