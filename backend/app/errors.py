"""Domain exceptions and FastAPI error handlers.

All error responses share one documented envelope::

    {
      "error": {
        "code": "machine_readable_code",
        "message": "human readable summary",
        "details": [ ... optional structured details ... ]
      }
    }
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import get_settings


class AppError(Exception):
    """Base class for all Callora module errors."""

    status_code: int = 400
    code: str = "app_error"

    def __init__(self, message: str, details: Optional[List[Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or []

    def to_payload(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        return {"error": body}


class AuthenticationError(AppError):
    status_code = 401
    code = "authentication_failed"


class LeadNotFoundError(AppError):
    status_code = 404
    code = "lead_not_found"

    def __init__(self, lead_id: str):
        super().__init__(f"Lead '{lead_id}' was not found")
        self.lead_id = lead_id


class ImportNotFoundError(AppError):
    status_code = 404
    code = "import_not_found"

    def __init__(self, import_id: str):
        super().__init__(f"Import '{import_id}' was not found")
        self.import_id = import_id


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"


class RowLimitExceededError(AppError):
    status_code = 413
    code = "row_limit_exceeded"


class UnsupportedMediaTypeError(AppError):
    status_code = 415
    code = "unsupported_media_type"


class MalformedFileError(AppError):
    status_code = 422
    code = "malformed_file"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ImportProcessingError(AppError):
    """Raised when an import batch crashes during processing. The batch
    itself persists with status=failed so the report stays available."""

    status_code = 500
    code = "import_failed"

    def __init__(self, import_id: str):
        super().__init__(
            f"Import '{import_id}' failed during processing; "
            f"retrieve the report at /imports/{import_id}",
            details=[{"import_id": import_id, "status_url": f"/imports/{import_id}"}],
        )
        self.import_id = import_id


# ---------------------------------------------------------------------------
# Module 2 — CRM Synchronization errors
# ---------------------------------------------------------------------------


class CRMConnectionNotFoundError(AppError):
    status_code = 404
    code = "crm_connection_not_found"

    def __init__(self, connection_id: str):
        super().__init__(f"CRM connection '{connection_id}' not found or inactive")
        self.connection_id = connection_id


class CRMConnectionInactiveError(AppError):
    status_code = 400
    code = "crm_connection_inactive"

    def __init__(self, connection_id: str):
        super().__init__(f"CRM connection '{connection_id}' is inactive")
        self.connection_id = connection_id


class CRMAuthError(AppError):
    status_code = 502
    code = "crm_authentication_failed"


class CRMTimeoutError(AppError):
    status_code = 504
    code = "crm_timeout"


class CRMUnavailableError(AppError):
    status_code = 503
    code = "crm_unavailable"


class CRMIdempotencyConflictError(AppError):
    status_code = 409
    code = "crm_idempotency_conflict"

    def __init__(self, key: str):
        super().__init__(
            f"Idempotency key '{key}' was already used with a different payload"
        )
        self.key = key


class CRMSyncError(AppError):
    status_code = 502
    code = "crm_sync_failed"


class CRMConnectionValidationError(AppError):
    """The submitted CRM connection failed connectivity/authentication
    validation. The connection is NOT saved and never reported verified."""

    status_code = 502
    code = "crm_connection_validation_failed"


# ---------------------------------------------------------------------------
# Module 3 — Recovery Agent (Opportunity Detection) errors
# ---------------------------------------------------------------------------


class RecoveryLeadsUnavailableError(AppError):
    """The agent-leads API could not be reached (network error or 5xx).

    The sweep never runs on partial data; the workflow itself is
    unaffected and can be retried."""

    status_code = 503
    code = "recovery_leads_unavailable"


class RecoveryLeadsTimeoutError(AppError):
    """The agent-leads API did not respond within the configured timeout."""

    status_code = 504
    code = "recovery_leads_timeout"


class RecoveryLeadsInvalidResponseError(AppError):
    """The agent-leads API returned a payload that violates the contract
    (bad JSON, missing ``items``, non-list items...)."""

    status_code = 502
    code = "recovery_leads_invalid_response"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def _request_validation_handler(_: Request, exc: RequestValidationError):
        details = [
            {
                "location": ".".join(str(part) for part in err["loc"]),
                "message": err["msg"],
                "type": err["type"],
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "request_validation_error",
                    "message": "The request body or parameters failed validation",
                    "details": details,
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(request: Request, exc: Exception):
        logger = logging.getLogger("callora.errors")
        logger.exception(
            "unhandled_exception", extra={"path": request.url.path}
        )
        if get_settings().debug:
            raise exc
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred",
                }
            },
        )
