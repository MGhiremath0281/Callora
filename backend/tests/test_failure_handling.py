"""Failure-handling tests: bad files, limit violations, crashes and
event delivery failures must never lose data silently."""

import pytest

from app.models import EventStatus, ImportStatus, Lead, OutboxEvent
from app.modules.lead_import.events import publish_pending_events
from tests.csv_helpers import REALESTATE_HEADER, REALESTATE_ROWS, build_csv, upload
from tests.conftest import auth_headers


class TestBadFiles:
    def test_empty_file_rejected(self, client):
        response = upload(client, b"", auth_headers())
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "malformed_file"

    def test_header_only_file_rejected(self, client):
        response = upload(client, build_csv(REALESTATE_HEADER, []), auth_headers())
        assert response.status_code == 422

    def test_leading_blank_lines_are_tolerated(self, client, db):
        """Real spreadsheet exports sometimes start with empty lines."""
        csv_bytes = b"\n\n" + build_csv(
            ["Name", "Phone", "Source"], [["Rahul", "9876543210", "website"]]
        )
        response = upload(client, csv_bytes, auth_headers())
        assert response.status_code == 201, response.text
        assert response.json()["created"] == 1
        assert db.query(Lead).count() == 1

    def test_blank_lines_between_rows_are_skipped(self, client, db):
        csv_bytes = (
            build_csv(["Name", "Phone", "Source"], [["Rahul", "9876543210", "website"]])
            + b"\n\n"
            + build_csv([], [["Priya", "9876543211", "website"]])
        )
        response = upload(client, csv_bytes, auth_headers())
        assert response.status_code == 201, response.text
        assert response.json()["created"] == 2

    def test_garbage_bytes_rejected(self, client):
        response = upload(client, b"\x00\x01\x02\x03", auth_headers())
        assert response.status_code == 422

    def test_non_csv_filename_rejected(self, client):
        csv_bytes = build_csv(REALESTATE_HEADER, REALESTATE_ROWS[:1])
        response = upload(client, csv_bytes, auth_headers(), filename="leads.xlsx")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "malformed_file"

    def test_invalid_mappings_json_rejected(self, client):
        csv_bytes = build_csv(REALESTATE_HEADER, REALESTATE_ROWS[:1])
        response = client.post(
            "/leads/import",
            files={"file": ("leads.csv", csv_bytes, "text/csv")},
            data={"mappings": "{not json"},
            headers=auth_headers(),
        )
        assert response.status_code == 422


class TestConfigurableLimits:
    def test_row_limit_exceeded(self, client, monkeypatch):
        monkeypatch.setenv("CALLORA_MAX_ROWS_PER_IMPORT", "2")
        from app.config import get_settings

        get_settings.cache_clear()
        csv_bytes = build_csv(REALESTATE_HEADER, REALESTATE_ROWS[:3])
        response = upload(client, csv_bytes, auth_headers())
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "row_limit_exceeded"

    def test_file_size_limit_exceeded(self, client, monkeypatch):
        monkeypatch.setenv("CALLORA_MAX_UPLOAD_SIZE_MB", "1")
        from app.config import get_settings

        get_settings.cache_clear()
        # ~1.1 MB payload with a huge single column.
        padding = "x" * (1100 * 1024)
        csv_bytes = build_csv(
            ["Name", "Phone", "Notes"], [["Rahul", "9876543210", padding]]
        )
        response = upload(client, csv_bytes, auth_headers())
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"


class TestProcessingCrash:
    def test_crash_marks_batch_failed_and_keeps_report_available(self, client, db, monkeypatch):
        """A crash mid-import must record a failed batch with an error
        message that remains retrievable."""
        import app.modules.lead_import.service as importer

        calls = {"count": 0}
        original = importer.build_row_values

        def flaky(row, mapping, name_parts=None):
            calls["count"] += 1
            if calls["count"] > 2:
                raise RuntimeError("simulated crash")
            return original(row, mapping, name_parts)

        monkeypatch.setattr(importer, "build_row_values", flaky)

        rows = [[f"Customer {i}", f"98765430{i:02d}", "website"] for i in range(5)]
        csv_bytes = build_csv(["Name", "Phone", "Source"], rows)
        response = upload(client, csv_bytes, auth_headers())

        # The response is an error envelope that points at the failed
        # batch instead of a bare 500.
        assert response.status_code == 500
        error = response.json()["error"]
        assert error["code"] == "import_failed"
        import_id = error["details"][0]["import_id"]

        status_body = client.get(f"/imports/{import_id}", headers=auth_headers()).json()
        assert status_body["status"] == ImportStatus.FAILED
        assert "simulated crash" in status_body["error_message"]

    def test_failed_batch_commits_nothing(self, client, db, monkeypatch):
        import app.modules.lead_import.service as importer

        def boom(*args, **kwargs):
            raise RuntimeError("system down")

        monkeypatch.setattr(importer, "build_row_values", boom)
        rows = [["Rahul", "9876543210", "website"]]
        csv_bytes = build_csv(["Name", "Phone", "Source"], rows)
        response = upload(client, csv_bytes, auth_headers())
        assert response.status_code == 500
        import_id = response.json()["error"]["details"][0]["import_id"]
        status_body = client.get(f"/imports/{import_id}", headers=auth_headers()).json()
        assert status_body["status"] == ImportStatus.FAILED
        # Atomicity: no half-imported leads from the failed batch.
        assert db.query(Lead).count() == 0


class TestEventDeliveryFailure:
    def _create_lead(self, client):
        return client.post(
            "/leads",
            json={"name": "Rahul", "phone": "9876543210", "source": "website"},
            headers=auth_headers(),
        ).json()["id"]

    def test_failed_delivery_stays_pending_and_is_retried(
        self, client, db, settings, monkeypatch
    ):
        monkeypatch.setenv("CALLORA_EVENT_WEBHOOK_URL", "http://events.example/webhook")
        from app.config import get_settings

        get_settings.cache_clear()
        current = get_settings()

        def failing_post(url, event, timeout=5.0):
            raise ConnectionError("webhook is down")

        import app.modules.lead_import.events as events

        monkeypatch.setattr(events, "_post_webhook", failing_post)
        lead_id = self._create_lead(client)

        event = db.query(OutboxEvent).filter(OutboxEvent.lead_id == lead_id).one()
        assert event.status == EventStatus.PENDING
        assert event.attempts == 1
        assert "webhook is down" in (event.last_error or "")

        # Delivery recovers: the same event is retried and published.
        monkeypatch.setattr(events, "_post_webhook", lambda url, e, timeout=5.0: None)
        summary = publish_pending_events(db, current)
        db.commit()
        assert summary["delivered"] == 1
        db.refresh(event)
        assert event.status == EventStatus.PUBLISHED
        assert event.attempts == 2
        assert event.published_at is not None

    def test_published_events_are_not_redelivered(self, client, db, settings, monkeypatch):
        monkeypatch.setenv("CALLORA_EVENT_WEBHOOK_URL", "http://events.example/webhook")
        from app.config import get_settings

        get_settings.cache_clear()
        current = get_settings()

        delivered = []
        import app.modules.lead_import.events as events

        monkeypatch.setattr(
            events, "_post_webhook", lambda url, e, timeout=5.0: delivered.append(e.id)
        )
        self._create_lead(client)

        assert len(delivered) == 1
        summary = publish_pending_events(db, current)
        db.commit()
        assert summary["delivered"] == 0  # idempotent: no double delivery
        assert len(delivered) == 1

    def test_delivery_attempts_are_capped(self, client, db, settings, monkeypatch):
        monkeypatch.setenv("CALLORA_EVENT_WEBHOOK_URL", "http://events.example/webhook")
        monkeypatch.setenv("CALLORA_EVENT_PUBLISH_MAX_ATTEMPTS", "2")
        from app.config import get_settings

        get_settings.cache_clear()
        current = get_settings()

        import app.modules.lead_import.events as events

        monkeypatch.setattr(events, "_post_webhook", lambda url, e, timeout=5.0: (_ for _ in ()).throw(ConnectionError("down")))
        self._create_lead(client)  # delivery pass 1 (in-request)

        publish_pending_events(db, current)  # delivery pass 2
        db.commit()

        event = db.query(OutboxEvent).one()
        assert event.status == EventStatus.PENDING
        assert event.attempts == 2

        # The next pass observes the exhausted attempt budget and marks
        # the event as failed instead of retrying forever.
        publish_pending_events(db, current)
        db.commit()
        db.refresh(event)
        assert event.status == EventStatus.FAILED
        assert event.attempts == 2
