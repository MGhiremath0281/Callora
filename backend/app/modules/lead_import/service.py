"""Import pipeline orchestration.

Implements the Module 1 workflow end to end:

parse -> detect/map -> validate -> normalize -> deduplicate -> persist
      -> report -> publish events

The pipeline is usable in two modes:

* **synchronous** – small files are processed inline and the response
  contains the final summary;
* **asynchronous** – files above ``sync_processing_row_threshold`` rows
  are processed by a background task; status and errors stay available
  through ``GET /imports/{import_id}`` afterwards.
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.logging_config import get_logger
from app.models import ImportBatch, ImportRowError, ImportStatus, Lead
from app.modules.lead_import import events as events_service
from app.modules.lead_import.dedup import DuplicateChecker
from app.modules.lead_import.mapping import (
    MappingSuggestion,
    build_row_values,
    detect_mappings,
    merge_first_last_name,
    resolve_mappings,
)
from app.modules.lead_import.normalizer import normalize_header
from app.modules.lead_import.validators import RowValidationError, validate_row

logger = get_logger(__name__)

COMMIT_CHUNK_SIZE = 500

# Error codes surfaced through the row-level errors endpoint.
ERROR_INVALID = "validation_error"
ERROR_DUPLICATE = "duplicate"


@dataclass
class ParsedFile:
    columns: List[str]
    rows: List[Dict[str, str]] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)


def parse_csv_bytes(data: bytes, settings: Settings) -> ParsedFile:
    """Parse an uploaded CSV into raw dict rows.

    Raises documented errors for empty files, oversized payloads and row
    limit violations. Encoding falls back from UTF-8 (with BOM) to
    cp1252, which covers typical spreadsheet exports.
    """
    from app.errors import MalformedFileError, PayloadTooLargeError, RowLimitExceededError

    if len(data) > settings.max_upload_size_bytes:
        raise PayloadTooLargeError(
            f"file exceeds the configured limit of {settings.max_upload_size_mb} MB"
        )
    if not data.strip():
        raise MalformedFileError("the uploaded file is empty")

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")

    try:
        # csv.reader (not DictReader) handles blank lines anywhere in the
        # file — common in real spreadsheet exports — while still
        # supporting newlines embedded in quoted fields.
        reader = csv.reader(io.StringIO(text))
        raw_rows = [row for row in reader if any(cell.strip() for cell in row)]
    except csv.Error as exc:
        raise MalformedFileError(f"the CSV file could not be parsed: {exc}") from exc

    if not raw_rows:
        raise MalformedFileError("the CSV file has no header row")

    header = [cell.strip() for cell in raw_rows[0]]
    columns = [c for c in header if c]
    if not columns:
        raise MalformedFileError("the CSV file has no usable header columns")

    rows: List[Dict[str, str]] = []
    for raw in raw_rows[1:]:
        row: Dict[str, str] = {}
        for index, key in enumerate(header):
            if not key:
                continue
            value = raw[index].strip() if index < len(raw) else ""
            row[key] = value
        # Skip rows that carry no data in any named column.
        if any(row.values()):
            rows.append(row)

    if not rows:
        raise MalformedFileError("the CSV file contains no data rows")
    if len(rows) > settings.max_rows_per_import:
        raise RowLimitExceededError(
            f"file contains {len(rows)} rows, exceeding the configured limit "
            f"of {settings.max_rows_per_import}"
        )
    return ParsedFile(columns=columns, rows=rows)


def detect_preview(columns: List[str]) -> Tuple[List[MappingSuggestion], Dict[str, Optional[str]]]:
    """Return the mapping suggestions (with ambiguity flags) and the
    conservative resolved mapping used when nothing is overridden."""
    suggestions = merge_first_last_name(detect_mappings(columns))
    resolved = resolve_mappings(columns)
    return suggestions, resolved


def apply_custom_field_policy(
    custom_fields: Dict[str, Any], settings: Settings
) -> Dict[str, Any]:
    allowlist = set(settings.custom_field_allowlist_set)
    if allowlist:
        return {k: v for k, v in custom_fields.items() if k in allowlist}
    if not settings.auto_capture_custom_fields:
        return {}
    return custom_fields


def create_batch(
    session: Session,
    business_id: str,
    source_type: str,
    filename: Optional[str],
    mapping: Optional[Dict[str, Any]] = None,
) -> ImportBatch:
    batch = ImportBatch(
        business_id=business_id,
        status=ImportStatus.PENDING,
        source_type=source_type,
        original_filename=filename,
        mapping=mapping or {},
    )
    session.add(batch)
    session.flush()
    return batch


def _record_error(
    session: Session,
    import_id: str,
    row_number: int,
    error: RowValidationError,
    raw_row: Dict[str, Any],
    error_code: str = ERROR_INVALID,
) -> None:
    session.add(
        ImportRowError(
            import_id=import_id,
            row_number=row_number,
            field=error.field,
            error_code=error_code,
            message=error.message,
            raw_data={k: v for k, v in raw_row.items() if v},
        )
    )


def process_import(
    session: Session,
    import_id: str,
    parsed: ParsedFile,
    final_mapping: Dict[str, Optional[str]],
    settings: Settings,
) -> list[str]:
    """Run the full pipeline for one import batch and persist everything.

    Counts reconcile exactly: ``total_rows == created + duplicates +
    rejected``. Per-row inserts use savepoints so one bad row (for
    example a uniqueness race on phone) is reported as a duplicate
    without aborting the batch.

    Returns a list of lead IDs that were successfully created, used to
    trigger automatic CRM synchronization after the import commits.
    """
    batch = session.get(ImportBatch, import_id)
    if batch is None:
        raise ValueError(f"import batch {import_id} not found")

    batch.status = ImportStatus.PROCESSING
    batch.started_at = datetime.now(timezone.utc)
    session.flush()

    checker = DuplicateChecker(session, batch.business_id)
    created = duplicates = rejected = 0
    created_lead_ids: list[str] = []

    try:
        for index, row in enumerate(parsed.rows, start=2):  # row 1 is the header
            mapped = build_row_values(row, final_mapping)
            mapped["custom_fields"] = apply_custom_field_policy(
                mapped.get("custom_fields") or {}, settings
            )
            validated = validate_row(mapped, settings)

            if not validated.is_valid:
                rejected += 1
                for error in validated.errors:
                    _record_error(session, batch.id, index, error, row)
                continue

            reason = checker.check(validated.values)
            if reason:
                duplicates += 1
                _record_error(
                    session,
                    batch.id,
                    index,
                    RowValidationError(
                        "phone" if reason == "existing_lead" else "phone",
                        reason,
                        "a lead with this phone number already exists"
                        if reason == "existing_lead"
                        else "this phone number appears more than once in the uploaded file",
                    ),
                    row,
                    error_code=ERROR_DUPLICATE,
                )
                continue

            values = {k: v for k, v in validated.values.items() if not k.startswith("_")}
            lead = Lead(business_id=batch.business_id, **values)
            try:
                with session.begin_nested():
                    session.add(lead)
                    session.flush()
            except IntegrityError:
                # Uniqueness race: another writer committed this phone
                # between our dedup check and the insert.
                duplicates += 1
                _record_error(
                    session,
                    batch.id,
                    index,
                    RowValidationError("phone", "existing_lead", "a lead with this phone number already exists"),
                    row,
                    error_code=ERROR_DUPLICATE,
                )
                continue

            events_service.enqueue_lead_created(session, batch.business_id, lead.id)
            checker.register_created(validated.values)
            created_lead_ids.append(lead.id)
            created += 1

            if (created + duplicates + rejected) % COMMIT_CHUNK_SIZE == 0:
                session.flush()

        batch.total_rows = parsed.row_count
        batch.created_count = created
        batch.duplicate_count = duplicates
        batch.rejected_count = rejected
        batch.status = ImportStatus.COMPLETED
        batch.completed_at = datetime.now(timezone.utc)
        session.flush()
    except Exception as exc:  # noqa: BLE001 - must be reported on the batch
        session.rollback()
        batch.status = ImportStatus.FAILED
        batch.total_rows = parsed.row_count
        batch.created_count = created
        batch.duplicate_count = duplicates
        batch.rejected_count = rejected
        batch.error_message = str(exc)[:2000]
        batch.completed_at = datetime.now(timezone.utc)
        session.add(batch)
        session.flush()
        logger.exception("import failed", extra={"import_id": import_id})
        raise

    return created_lead_ids


def import_summary(batch: ImportBatch) -> Dict[str, Any]:
    """Public summary returned by the import endpoints."""
    return {
        "import_id": batch.id,
        "status": batch.status,
        "total_rows": batch.total_rows,
        "created": batch.created_count,
        "duplicates": batch.duplicate_count,
        "rejected": batch.rejected_count,
        "errors_url": f"/api/v1/imports/{batch.id}/errors",
    }


def finalize_import(session: Session, settings: Settings) -> None:
    """Deliver pending outbox events after a successful commit."""
    try:
        summary = events_service.publish_pending_events(session, settings)
        if summary["delivered"] or summary["failed"]:
            logger.info("event publication pass", extra=summary)
    except Exception:  # noqa: BLE001 - delivery never fails the import
        logger.exception("event publication pass crashed")
