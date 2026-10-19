"""Unit tests for the normalization helpers."""

from datetime import timezone
from decimal import Decimal

import pytest

from app.modules.lead_import.normalizer import (
    normalize_email,
    normalize_header,
    normalize_language,
    normalize_name,
    normalize_phone,
    parse_datetime,
    parse_decimal,
)


class TestNormalizeHeader:
    def test_basic(self):
        assert normalize_header("Mobile Number") == "mobile_number"
        assert normalize_header("  Property   Budget ") == "property_budget"
        assert normalize_header("E-mail") == "e_mail"

    def test_special_characters_and_case(self):
        assert normalize_header("¿Cuál es tu Teléfono?") == "cual_es_tu_telefono"
        assert normalize_header("Budget (INR)") == "budget_inr"

    def test_unicode_accents_are_stripped(self):
        assert normalize_header("Teléfono") == "telefono"


class TestNormalizePhone:
    def test_indian_local_number_is_kept_without_guessing(self):
        # A plain 10-digit number is stored as-is; no country code invented.
        assert normalize_phone("9876543210") == ("9876543210", None)

    def test_explicit_international_number(self):
        assert normalize_phone("+91 98123 45678") == ("+919812345678", None)
        assert normalize_phone("+1-555-123-4567") == ("+15551234567", None)

    def test_formatting_characters_are_stripped(self):
        assert normalize_phone("(98765) 43210") == ("9876543210", None)
        assert normalize_phone("98765-43210") == ("9876543210", None)

    def test_too_short_or_too_long(self):
        _, error = normalize_phone("123")
        assert error is not None
        assert "10 to 15 digits" in error

    def test_letters_are_rejected(self):
        _, error = normalize_phone("98765ABC210")
        assert error is not None

    def test_empty_and_missing(self):
        assert normalize_phone("")[1] is not None
        assert normalize_phone(None)[1] is not None
        assert normalize_phone("   ")[1] is not None


class TestNormalizeEmail:
    def test_valid_email_is_lowercased(self):
        assert normalize_email("  Rahul@Example.COM ") == ("rahul@example.com", None)

    def test_invalid_email(self):
        email, error = normalize_email("not-an-email")
        assert email is None
        assert error is not None

    def test_empty_is_optional_ok(self):
        assert normalize_email("") == (None, None)
        assert normalize_email(None) == (None, None)


class TestNormalizeName:
    def test_whitespace_collapsed(self):
        assert normalize_name("  Rahul   Sharma ") == "Rahul Sharma"

    def test_all_caps_is_title_cased(self):
        assert normalize_name("PRIYA PATEL") == "Priya Patel"

    def test_mixed_case_untouched(self):
        assert normalize_name("McDonald") == "McDonald"

    def test_accents_stripped(self):
        assert normalize_name("José García") == "Jose Garcia"


class TestParseDecimal:
    def test_plain_number(self):
        assert parse_decimal("1200000") == (Decimal("1200000"), None)

    def test_indian_grouping_and_currency(self):
        assert parse_decimal("₹75,00,000") == (Decimal("7500000"), None)

    def test_western_grouping_and_symbol(self):
        assert parse_decimal("$1,250,000.50") == (Decimal("1250000.50"), None)

    def test_float_input(self):
        assert parse_decimal(500.25) == (Decimal("500.25"), None)

    def test_garbage(self):
        value, error = parse_decimal("abc")
        assert value is None
        assert error is not None

    def test_negative_rejected(self):
        value, error = parse_decimal("-5")
        assert value is None
        assert error is not None

    def test_empty_is_optional(self):
        assert parse_decimal("") == (None, None)
        assert parse_decimal(None) == (None, None)


class TestParseDatetime:
    def test_iso_date_is_utc(self):
        dt, error = parse_datetime("2026-01-15")
        assert error is None
        assert dt is not None
        assert dt.tzinfo == timezone.utc
        assert dt.year == 2026 and dt.month == 1 and dt.day == 15

    def test_iso_timestamp_with_z(self):
        dt, error = parse_datetime("2026-01-15T10:30:00Z")
        assert error is None
        assert dt.hour == 10

    def test_day_first_format(self):
        dt, error = parse_datetime("15/02/2026")
        assert error is None
        assert (dt.day, dt.month, dt.year) == (15, 2, 2026)

    def test_invalid(self):
        value, error = parse_datetime("garbage-date")
        assert value is None
        assert error is not None

    def test_empty_is_optional(self):
        assert parse_datetime("") == (None, None)


class TestNormalizeLanguage:
    def test_lowercased(self):
        assert normalize_language(" Hindi ") == "hindi"

    def test_empty(self):
        assert normalize_language("") is None
        assert normalize_language(None) is None
