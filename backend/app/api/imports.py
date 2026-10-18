"""Import endpoints: CSV upload, preview, status and row-level errors.

The CSV import workflow follows the Module 1 pipeline:

1. ``POST /leads/import/preview`` – detect mappings, validate, count
   duplicates and show samples **without storing anything**. Ambiguous
   mappings are reported so the business can confirm them.
2. ``POST /leads/import`` – run the pipeline for real. Small files are
   processed synchronously; larger ones run in the background and the
   response returns ``202`` with a pending status that can be polled via
   ``GET /imports/{import_id}``.
"""

import json
from typing import Dict, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import authenticate_business
from app.config import Settings, get_settings
from app.db.session import get_db, session_scope
from app.errors import ImportNotFoundError, ImportProcessingError, MalformedFileError
from app.logging_config import bind_context, get_logger
from app.models import IdempotencyKey, ImportBatch, ImportRowError
from app.schemas.imports import (
    ImportErrorListResponse,
    ImportErrorModel,
    ImportPreviewResponse,
    ImportStatusResponse,
    ImportSummary,
)
from app.services import importer as importer_service

logger = get_logger(__name__)

router = APIRouter(prefix="", tags=["imports"])


def _parse_overrides(raw: Optional[str]) -> Dict[str, str]:
    """Parse the optional ``mappings`` form field (JSON object)."""
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MalformedFileError(f"the mappings field must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict) or not all(
        isinstance(k, str) and (v is None or isinstance(v, str)) for k, v in parsed.items()
    ):
        raise MalformedFileError(
            "the mappings field must be a JSON object of column -> canonical field"
        )
    return {k: v for k, v in parsed.items() if v is None or v.strip()}


def _batch_or_404(db: Session, business_id: str, import_id: str) -> ImportBatch:
    batch = db.get(ImportBatch, import_id)
    if batch is None or batch.business_id != business_id:
        raise ImportNotFoundError(import_id)
    return batch


def _status_response(batch) -> ImportStatusResponse:
    return ImportStatusResponse(
        **importer_service.import_summary(batch),
        source_type=batch.source_type,
        original_filename=batch.original_filename,
        mapping=batch.mapping or {},
        error_message=batch.error_message,
        created_at=batch.created_at,
        started_at=batch.started_at,
        completed_at=batch.completed_at,
    )


def _read_upload(file: UploadFile) -> bytes:
    if file.filename and not file.filename.lower().endswith(".csv"):
        raise MalformedFileError("only .csv files are supported")
    return file.file.read()


@router.post(
    "/leads/import/preview",
    response_model=ImportPreviewResponse,
    summary="Preview mappings, validity and duplicates without importing",
)
def preview_import(
    file: UploadFile = File(..., description="CSV file to analyze"),
    mappings: Optional[str] = Form(
        default=None,
        description='Business-confirmed mappings, e.g. \'{"Customer Name": "name"}\'',
    ),
    business_id: str = Depends(authenticate_business),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    bind_context(business=business_id)
    overrides = _parse_overrides(mappings)
    parsed = importer_service.parse_csv_bytes(_read_upload(file), settings)
    preview = importer_service.build_preview(db, business_id, parsed, settings, overrides)
    return preview


@router.post(
    "/leads/import",
    response_model=ImportSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a CSV and start the import workflow",
)
def import_leads(
    background_tasks: BackgroundTasks,
    response: Response,
    file: UploadFile = File(..., description="CSV file to import"),
    mappings: Optional[str] = Form(
        default=None,
        description='Business-confirmed mappings, e.g. \'{"Customer Name": "name"}\'',
    ),
    business_id: str = Depends(authenticate_business),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    bind_context(business=business_id)

    if idempotency_key:
        replay = (
            db.execute(
                select(IdempotencyKey).where(
                    IdempotencyKey.business_id == business_id,
                    IdempotencyKey.scope == "csv_import",
                    IdempotencyKey.key == idempotency_key,
                )
            )
            .scalars()
            .first()
        )
        if replay and replay.response_ref:
            batch = _batch_or_404(db, business_id, replay.response_ref)
            response.status_code = status.HTTP_200_OK
            response.headers["Idempotency-Replayed"] = "true"
            return _status_response(batch)

    overrides = _parse_overrides(mappings)
    parsed = importer_service.parse_csv_bytes(_read_upload(file), settings)
    suggestions, _ = importer_service.detect_preview(parsed.columns)
    final_mapping = importer_service.resolve_mappings(parsed.columns, overrides)

    ambiguous = [s.to_dict() for s in suggestions if s.ambiguous and s.canonical_field]
    mapping_record = {
        "resolved": final_mapping,
        "ambiguous_suggestions": ambiguous,
        "overrides": overrides,
    }

    batch = importer_service.create_batch(
        db,
        business_id,
        source_type="csv_upload",
        filename=file.filename,
        mapping=mapping_record,
    )
    db.commit()
    bind_context(import_id=batch.id)

    if idempotency_key:
        try:
            with db.begin_nested():
                db.add(
                    IdempotencyKey(
                        business_id=business_id,
                        scope="csv_import",
                        key=idempotency_key,
                        response_ref=batch.id,
                    )
                )
                db.flush()
            db.commit()
        except IntegrityError:
            logger.warning("concurrent import idempotency key", extra={"key": idempotency_key})

    if parsed.row_count > settings.sync_processing_row_threshold:
        # Large files: process in the background; status and errors stay
        # available through GET /imports/{import_id}.
        background_tasks.add_task(
            _process_import_task, batch.id, parsed, final_mapping
        )
        db.refresh(batch)
        response.status_code = status.HTTP_202_ACCEPTED
        return _status_response(batch)

    try:
        importer_service.process_import(db, batch.id, parsed, final_mapping, settings)
        db.commit()
    except Exception:  # noqa: BLE001 - failure is recorded on the batch
        db.commit()  # persist the failed status written by process_import
        logger.exception("synchronous import failed", extra={"import_id": batch.id})
        # The batch record survives with its error message; the client
        # receives an error envelope pointing at the retrievable report.
        raise ImportProcessingError(batch.id)

    importer_service.finalize_import(db, settings)
    db.commit()
    db.refresh(batch)
    return _status_response(batch)


def _process_import_task(
    import_id: str,
    parsed: importer_service.ParsedFile,
    final_mapping: Dict[str, Optional[str]],
) -> None:
    """Background entry point: own session, commit, then publish events."""
    settings = get_settings()
    bind_context(import_id=import_id)
    with session_scope() as session:
        try:
            importer_service.process_import(session, import_id, parsed, final_mapping, settings)
        except Exception:  # noqa: BLE001 - failure is recorded on the batch
            logger.exception("background import failed", extra={"import_id": import_id})
    with session_scope() as session:
        importer_service.finalize_import(session, settings)


@router.get(
    "/imports/{import_id}",
    response_model=ImportStatusResponse,
    summary="Retrieve import status and summary",
)
def get_import(
    import_id: str,
    business_id: str = Depends(authenticate_business),
    db: Session = Depends(get_db),
):
    bind_context(business=business_id, import_id=import_id)
    batch = _batch_or_404(db, business_id, import_id)
    return _status_response(batch)


@router.get(
    "/imports/{import_id}/errors",
    response_model=ImportErrorListResponse,
    summary="Retrieve row-level validation errors",
)
def get_import_errors(
    import_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    business_id: str = Depends(authenticate_business),
    db: Session = Depends(get_db),
):
    bind_context(business=business_id, import_id=import_id)
    batch = _batch_or_404(db, business_id, import_id)
    filters = [ImportRowError.import_id == batch.id]
    total = db.execute(select(func.count()).select_from(ImportRowError).where(*filters)).scalar_one()
    rows = list(
        db.execute(
            select(ImportRowError)
            .where(*filters)
            .order_by(ImportRowError.row_number, ImportRowError.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars()
    )
    return ImportErrorListResponse(
        import_id=batch.id,
        total=total,
        page=page,
        page_size=page_size,
        items=[
            ImportErrorModel(
                row_number=r.row_number,
                field=r.field,
                error_code=r.error_code,
                message=r.message,
                raw_data=r.raw_data or {},
            )
            for r in rows
        ],
    )
