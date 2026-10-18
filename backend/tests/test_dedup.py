"""Unit tests for duplicate detection."""

from app.models import Lead
from app.services.dedup import REASON_EXISTING, REASON_IN_FILE, DuplicateChecker, filter_duplicates


def _lead(db, phone, business="biz_001", email=None):
    db.add(
        Lead(
            business_id=business,
            name="Existing",
            phone=phone,
            email=email,
            source="website",
            custom_fields={},
        )
    )
    db.commit()


class TestAgainstExistingLeads:
    def test_existing_phone_detected(self, db, settings):
        _lead(db, "9876543210")
        reasons = filter_duplicates(db, "biz_001", [{"phone": "9876543210", "email": None}])
        assert reasons == [REASON_EXISTING]

    def test_different_business_not_duplicate(self, db, settings):
        _lead(db, "9876543210", business="biz_002")
        reasons = filter_duplicates(db, "biz_001", [{"phone": "9876543210", "email": None}])
        assert reasons == [None]

    def test_no_country_code_guessing(self, db, settings):
        # "+919876543210" is a different value from "9876543210" on
        # purpose: ambiguous values must not be silently merged.
        _lead(db, "9876543210")
        reasons = filter_duplicates(db, "biz_001", [{"phone": "+919876543210", "email": None}])
        assert reasons == [None]


class TestWithinFileDuplicates:
    def test_repeated_phone_in_file(self, db, settings):
        rows = [
            {"phone": "9876543210", "email": None},
            {"phone": "9876543210", "email": None},
            {"phone": "9111111111", "email": None},
        ]
        reasons = filter_duplicates(db, "biz_001", rows)
        assert reasons == [None, REASON_IN_FILE, None]

    def test_stateful_checker_registers_created_rows(self, db, settings):
        checker = DuplicateChecker(db, "biz_001")
        first = {"phone": "9876543210"}
        assert checker.check(first) is None
        checker.register_created(first)
        # Later rows of the same file are in-file duplicates.
        assert checker.check({"phone": "9876543210"}) == REASON_IN_FILE

    def test_persisted_rows_are_seen_as_existing_by_fresh_checkers(self, db, settings):
        _lead(db, "9876543210")
        db.commit()
        # A fresh checker (e.g. a retried import) sees the persisted row
        # in the database.
        fresh = DuplicateChecker(db, "biz_001")
        assert fresh.check({"phone": "9876543210"}) == REASON_EXISTING

    def test_register_persisted_mid_import(self, db, settings):
        checker = DuplicateChecker(db, "biz_001")
        values = {"phone": "9876543210"}
        assert checker.check(values) is None
        checker.register_persisted(values)
        assert checker.check({"phone": "9876543210"}) == REASON_EXISTING


class TestUniqueConstraint:
    def test_database_enforces_phone_uniqueness_per_business(self, db, settings):
        import pytest
        from sqlalchemy.exc import IntegrityError

        _lead(db, "9876543210")
        db.add(
            Lead(
                business_id="biz_001",
                name="Clone",
                phone="9876543210",
                source="website",
                custom_fields={},
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
