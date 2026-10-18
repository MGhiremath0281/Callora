"""Row validation against the canonical lead structure.

Each mapped row is converted into a canonical lead payload. Errors are
collected per row (not fail-fast) so the import report can show every
problem in every rejected row.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.config import Settings
from app.services.normalization import (
    normalize_email,
    normalize_language,
    normalize_name,
    normalize_phone,
    normalize_source,
    parse_datetime,
    parse_decimal,
    truncate,
)

MAX_NAME_LENGTH = 255
MAX_SOURCE_LENGTH = 100
MAX_CUSTOM_FIELD_KEY_LENGTH = 100
MAX_CUSTOM_FIELD_VALUE_LENGTH = 1000


@dataclass
class RowValidationError:
    field: Optional[str]
    error_code: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {"field": self.field, "error_code": self.error_code, "message": self.message}


@dataclass
class ValidatedRow:
    values: Dict[str, Any]
    errors: List[RowValidationError] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def _sanitize_custom_fields(
    raw: Dict[str, Any], settings: Settings
) -> Dict[str, Any]:
    """Apply allowlist/size limits to captured custom fields."""
    if not raw:
        return {}
    allowlist = set(settings.custom_field_allowlist_set)
    auto = settings.auto_capture_custom_fields
    result: Dict[str, Any] = {}
    for key, value in list(raw.items())[: settings.max_custom_fields]:
        clean_key = truncate(key, MAX_CUSTOM_FIELD_KEY_LENGTH) or ""
        if not clean_key:
            continue
        if not auto and clean_key not in allowlist:
            continue
        if allowlist and clean_key not in allowlist:
            continue
        if isinstance(value, str):
            value = truncate(value, MAX_CUSTOM_FIELD_VALUE_LENGTH)
        result[clean_key] = value
    return result


def validate_row(mapped: Dict[str, Any], settings: Settings) -> ValidatedRow:
    """Validate and normalize one mapped row into canonical form."""
    errors: List[RowValidationError] = []

    name = normalize_name(mapped.get("name"))
    if not name:
        errors.append(RowValidationError("name", "missing_required", "name is required"))

    phone, phone_error = normalize_phone(mapped.get("phone"))
    if phone_error:
        errors.append(RowValidationError("phone", "invalid_phone", phone_error))

    email, email_error = normalize_email(mapped.get("email"))
    if email_error:
        errors.append(RowValidationError("email", "invalid_email", email_error))

    source = normalize_source(mapped.get("source"))
    source_was_missing = not source
    if source_was_missing:
        source = normalize_source(settings.default_lead_source)
    if len(source) > MAX_SOURCE_LENGTH:
        source = source[:MAX_SOURCE_LENGTH]

    language = normalize_language(mapped.get("language"))

    deal_value, deal_error = parse_decimal(mapped.get("deal_value"))
    if deal_error:
        errors.append(RowValidationError("deal_value", "invalid_number", deal_error))

    last_contact_at, date_error = parse_datetime(mapped.get("last_contact_at"))
    if date_error:
        errors.append(
            RowValidationError("last_contact_at", "invalid_date", date_error)
        )

    external_id = truncate(
        str(mapped["external_id"]).strip() if mapped.get("external_id") else None, 128
    )

    custom_fields = _sanitize_custom_fields(mapped.get("custom_fields") or {}, settings)

    values: Dict[str, Any] = {
        "name": truncate(name, MAX_NAME_LENGTH),
        "phone": phone,
        "email": email,
        "source": source,
        "language": language,
        "deal_value": deal_value,
        "last_contact_at": last_contact_at,
        "external_id": external_id,
        "custom_fields": custom_fields,
    }
    if source_was_missing:
        values.setdefault("_notes", []).append(
            f"no source column detected; defaulted to '{source}'"
        )
    return ValidatedRow(values=values, errors=errors)
