"""Tests for the provider webhook intake.

The webhook is the only way the system learns what happened during a
call, so it has to be exactly right: a retried delivery must not be
applied twice, a late delivery must never move a call backwards, and
the heavy post-call analysis must be queued instead of run inline.
"""

import pytest

from app.modules.voice import repository as repo
from app.modules.voice.constants import (
    AuditAction,
    CallStatus,
    HandoffKind,
    HandoffStatus,
    JobStatus,
    JobType,
)
from app.modules.voice.errors import WebhookSignatureError
from app.modules.voice.jobs import queue
from app.modules.voice.models import AuditLog, Call, CallEvent, Handoff
from app.modules.voice.webhooks.service import (
    RESULT_DUPLICATE,
    RESULT_IGNORED,
    RESULT_PROCESSED,
    RESULT_UNMATCHED,
    WebhookService,
)
from tests.conftest import VOICE_BIZ, VOICE_LEAD_ID, VOICE_PHONE
from tests.fakes import FakeProvider


class SignedProvider(FakeProvider):
    """A provider that verifies a shared secret on the webhook."""

    def __init__(self, secret: str = "s3cret", **kwargs):
        super().__init__(**kwargs)
        self.secret = secret

    def verify_signature(self, headers, raw_body):
        if not headers:
            return False
        provided = {str(k).lower(): str(v) for k, v in headers.items()}.get(
            "x-bolna-signature"
        )
        if not provided:
            return False
        return provided == self.secret


@pytest.fixture()
def provider():
    return FakeProvider()


@pytest.fixture()
def service(voice_settings, db, provider):
    return WebhookService(voice_settings, db, provider)


@pytest.fixture()
def call(db, voice_business, voice_lead):
    """A call that the provider already knows about."""
    row = Call(
        business_id=VOICE_BIZ,
        lead_id=VOICE_LEAD_ID,
        provider="fake",
        provider_call_id="exec_1",
        status=CallStatus.QUEUED,
        to_number=VOICE_PHONE,
    )
    db.add(row)
    db.commit()
    return row


def _payload(status, execution_id="exec_1", **extra):
    data = {"id": execution_id, "status": status}
    data.update(extra)
    return data


# ---------------------------------------------------------------------------
# Applying an event
# ---------------------------------------------------------------------------


class TestApplyEvent:
    def test_a_lifecycle_event_is_applied(self, service, db, call):
        result = service.process_execution_event(_payload("ringing"))
        assert result.status == RESULT_PROCESSED
        assert result.call_id == call.id
        assert result.call_status == CallStatus.RINGING
        assert call.status == CallStatus.RINGING

    def test_the_event_is_stored(self, service, db, call):
        service.process_execution_event(_payload("ringing"))
        events = repo.list_call_events(db, VOICE_BIZ, call.id)
        assert len(events) == 1
        assert events[0].provider_status == "ringing"
        assert events[0].processed is True

    def test_the_transcript_is_stored(self, service, db, call):
        service.process_execution_event(_payload("in-progress", transcript="hello"))
        assert call.transcript == "hello"

    def test_the_recording_and_cost_are_stored(self, service, db, call):
        service.process_execution_event(
            _payload(
                "completed",
                conversation_duration=42,
                total_cost=1.75,
                telephony_data={"recording_url": "https://example.com/rec.mp3"},
            )
        )
        assert call.duration_seconds == 42
        assert float(call.cost) == 1.75
        assert call.recording_url == "https://example.com/rec.mp3"

    def test_the_event_is_audited(self, service, db, call):
        service.process_execution_event(_payload("ringing"))
        entries = repo.list_audit_logs(db, VOICE_BIZ)
        received = [e for e in entries if e.action == AuditAction.WEBHOOK_RECEIVED]
        assert len(received) == 1
        assert received[0].entity_id == db.query(CallEvent).one().id

    def test_an_unknown_execution_id_is_unmatched(self, service, db):
        result = service.process_execution_event(_payload("ringing", execution_id="nope"))
        assert result.status == RESULT_UNMATCHED
        assert result.call_id is None
        stored = db.query(CallEvent).one()
        assert stored.processed is False
        assert stored.processing_error == "unknown_call"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_the_same_delivery_twice_is_a_duplicate(self, service, db, call):
        payload = _payload("ringing")
        first = service.process_execution_event(payload)
        second = service.process_execution_event(payload)
        assert first.status == RESULT_PROCESSED
        assert second.status == RESULT_DUPLICATE
        assert second.event_id == first.event_id

    def test_a_duplicate_does_not_create_a_second_event(self, service, db, call):
        payload = _payload("ringing")
        service.process_execution_event(payload)
        service.process_execution_event(payload)
        assert db.query(CallEvent).count() == 1

    def test_a_duplicate_does_not_move_the_call(self, service, db, call):
        service.process_execution_event(_payload("ringing"))
        service.process_execution_event(_payload("ringing"))
        assert call.status == CallStatus.RINGING

    def test_a_duplicate_is_audited(self, service, db, call):
        payload = _payload("ringing")
        service.process_execution_event(payload)
        service.process_execution_event(payload)
        entries = repo.list_audit_logs(db, VOICE_BIZ)
        duplicates = [e for e in entries if e.action == AuditAction.WEBHOOK_DUPLICATE]
        assert len(duplicates) == 1

    def test_a_duplicate_does_not_create_a_second_job(self, service, db, call):
        payload = _payload("completed")
        service.process_execution_event(payload)
        service.process_execution_event(payload)
        jobs = queue.list_jobs(db, business_id=VOICE_BIZ)
        assert len(jobs) == 1


# ---------------------------------------------------------------------------
# Out-of-order deliveries
# ---------------------------------------------------------------------------


class TestOutOfOrder:
    def test_an_older_event_never_moves_the_call_backwards(self, service, db, call):
        service.process_execution_event(_payload("in-progress", seq=1))
        assert call.status == CallStatus.IN_PROGRESS
        result = service.process_execution_event(_payload("ringing", seq=2))
        assert result.status == RESULT_IGNORED
        assert call.status == CallStatus.IN_PROGRESS

    def test_an_ignored_event_is_still_recorded(self, service, db, call):
        service.process_execution_event(_payload("in-progress", seq=1))
        service.process_execution_event(_payload("ringing", seq=2))
        events = repo.list_call_events(db, VOICE_BIZ, call.id)
        assert len(events) == 2
        assert events[1].processed is False
        assert events[1].processing_error == "out_of_order"

    def test_a_terminal_status_is_never_overwritten(self, service, db, call):
        service.process_execution_event(_payload("completed", seq=1))
        assert call.status == CallStatus.COMPLETED
        result = service.process_execution_event(_payload("ringing", seq=2))
        assert result.status == RESULT_IGNORED
        assert call.status == CallStatus.COMPLETED

    def test_the_documented_bolna_sequence_works(self, service, db, call):
        """Bolna sends 'call-disconnected' right before 'completed'."""
        service.process_execution_event(_payload("call-disconnected", seq=1))
        assert call.status == CallStatus.DISCONNECTED
        result = service.process_execution_event(_payload("completed", seq=2))
        assert result.status == RESULT_PROCESSED
        assert call.status == CallStatus.COMPLETED
        assert call.ended_at is not None


# ---------------------------------------------------------------------------
# Background work
# ---------------------------------------------------------------------------


class TestPostCallJob:
    def test_a_terminal_status_queues_the_post_call_work(self, service, db, call):
        result = service.process_execution_event(_payload("completed"))
        assert result.job_id
        jobs = queue.list_jobs(db, business_id=VOICE_BIZ)
        assert len(jobs) == 1
        job = jobs[0]
        assert job.type == JobType.POSTCALL_PROCESS
        assert job.status == JobStatus.PENDING
        assert job.payload["call_id"] == call.id

    def test_a_non_terminal_status_queues_nothing(self, service, db, call):
        result = service.process_execution_event(_payload("ringing"))
        assert result.job_id is None
        assert queue.list_jobs(db, business_id=VOICE_BIZ) == []


# ---------------------------------------------------------------------------
# Unknown provider statuses
# ---------------------------------------------------------------------------


class TestUnknownStatus:
    """A status Callora has never seen says nothing about the call.

    It used to be mapped onto ``error``, which terminated a call that
    was possibly still running, released the duplicate-call guard and
    queued a post-call analysis for a conversation that had not
    happened.
    """

    def test_an_unknown_status_does_not_terminate_the_call(
        self, service, db, call
    ):
        result = service.process_execution_event(_payload("wobbled"))

        assert result.status == RESULT_IGNORED
        assert call.status == CallStatus.QUEUED
        assert call.ended_at is None

    def test_an_unknown_status_queues_no_post_call_work(self, service, db, call):
        service.process_execution_event(_payload("wobbled"))

        assert queue.list_jobs(db, business_id=VOICE_BIZ) == []

    def test_an_unknown_status_keeps_the_duplicate_call_guard(
        self, service, db, call
    ):
        service.process_execution_event(_payload("wobbled"))

        assert repo.count_in_flight_calls(db, VOICE_BIZ) == 1

    def test_an_unknown_status_is_recorded_as_not_processed(
        self, service, db, call
    ):
        service.process_execution_event(_payload("wobbled"))

        events = repo.list_call_events(db, VOICE_BIZ, call.id)
        assert len(events) == 1
        assert events[0].processed is False
        assert events[0].processing_error == "unknown_provider_status"


# ---------------------------------------------------------------------------
# Retries and the duplicate-call guard
# ---------------------------------------------------------------------------


class TestRetries:
    def test_a_retried_delivery_after_the_call_ended_changes_nothing(
        self, service, db, call
    ):
        first = service.process_execution_event(_payload("completed"))
        second = service.process_execution_event(_payload("completed"))

        assert first.status == RESULT_PROCESSED
        assert second.status == RESULT_DUPLICATE
        assert call.status == CallStatus.COMPLETED
        assert len(queue.list_jobs(db, business_id=VOICE_BIZ)) == 1

    def test_a_terminal_status_releases_the_duplicate_call_guard(
        self, service, db, call
    ):
        assert repo.count_in_flight_calls(db, VOICE_BIZ) == 1

        service.process_execution_event(_payload("completed"))

        assert repo.count_in_flight_calls(db, VOICE_BIZ) == 0

    def test_an_active_call_keeps_the_duplicate_call_guard(self, service, db, call):
        service.process_execution_event(_payload("ringing"))

        assert repo.count_in_flight_calls(db, VOICE_BIZ) == 1

    def test_a_dropped_line_releases_the_duplicate_call_guard(
        self, service, db, call
    ):
        service.process_execution_event(_payload("call-disconnected"))

        assert call.status == CallStatus.DISCONNECTED
        assert repo.count_in_flight_calls(db, VOICE_BIZ) == 0


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


class TestSignature:
    def test_an_invalid_signature_is_rejected(self, voice_settings, db, call):
        provider = SignedProvider(secret="s3cret")
        service = WebhookService(voice_settings, db, provider)
        with pytest.raises(WebhookSignatureError):
            service.process_execution_event(
                _payload("ringing"), headers={"x-bolna-signature": "wrong"}
            )

    def test_a_valid_signature_is_accepted(self, voice_settings, db, call):
        provider = SignedProvider(secret="s3cret")
        service = WebhookService(voice_settings, db, provider)
        result = service.process_execution_event(
            _payload("ringing"), headers={"x-bolna-signature": "s3cret"}
        )
        assert result.status == RESULT_PROCESSED
        assert result.signature_valid is True

    def test_a_rejected_event_is_audited(self, voice_settings, db, call):
        provider = SignedProvider(secret="s3cret")
        service = WebhookService(voice_settings, db, provider)
        with pytest.raises(WebhookSignatureError):
            service.process_execution_event(
                _payload("ringing"), headers={"x-bolna-signature": "wrong"}
            )
        # A rejected delivery is not attributed to a business: the
        # signature is verified before the payload is trusted.
        rejected = (
            db.query(AuditLog)
            .filter(AuditLog.action == AuditAction.WEBHOOK_REJECTED)
            .all()
        )
        assert len(rejected) == 1
        assert rejected[0].reason_code == "invalid_signature"


# ---------------------------------------------------------------------------
# Warm transfers
# ---------------------------------------------------------------------------


class TestPreTransfer:
    def test_a_warm_transfer_is_recorded(self, voice_settings, db, voice_business, call):
        voice_business.warm_transfer_enabled = True
        voice_business.warm_transfer_number = "+919000000001"
        db.commit()
        service = WebhookService(voice_settings, db, FakeProvider())
        result = service.process_pre_transfer_event(
            {
                "id": "exec_1",
                "transfer_reason": "the customer asked for a human",
                "conversation_summary": "Wants a site visit on Saturday",
                "transferred_to": "+919000000001",
            }
        )
        assert result.status == RESULT_PROCESSED
        handoff = db.query(Handoff).one()
        assert handoff.kind == HandoffKind.WARM_TRANSFER
        assert handoff.status == HandoffStatus.INITIATED
        assert handoff.call_id == call.id
        assert handoff.target_number == "+919000000001"
        assert "site visit" in (handoff.briefing or "")

    def test_a_transfer_without_support_is_marked_as_such(
        self, voice_settings, db, voice_business, call
    ):
        service = WebhookService(voice_settings, db, FakeProvider())
        service.process_pre_transfer_event({"id": "exec_1"})
        handoff = db.query(Handoff).one()
        assert handoff.status == HandoffStatus.NOT_SUPPORTED

    def test_a_transfer_for_an_unknown_call_is_unmatched(
        self, voice_settings, db, voice_business
    ):
        service = WebhookService(voice_settings, db, FakeProvider())
        result = service.process_pre_transfer_event({"id": "exec_unknown"})
        assert result.status == RESULT_UNMATCHED
        assert db.query(Handoff).count() == 0
