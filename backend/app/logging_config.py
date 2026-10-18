"""Structured JSON logging with request-scoped context.

Context values (business_id, import_id, request_id) are attached through
contextvars so every log line emitted while processing a request or an
import carries the relevant identifiers.
"""

import contextvars
import json
import logging
import sys
import time
from typing import Any, Dict, Optional

business_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("business_id", default="")
import_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("import_id", default="")
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")


class JsonFormatter(logging.Formatter):
    _STANDARD = set(logging.makeLogRecord({}).__dict__.keys()) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("business_id", "import_id", "request_id"):
            value = getattr(record, key, None)
            if value:
                payload[key] = value
        for key, value in record.__dict__.items():
            if key not in self._STANDARD and key not in payload and not key.startswith("_"):
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = str(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())


def bind_context(business: Optional[str] = None, import_id: Optional[str] = None) -> None:
    if business:
        business_id_var.set(business)
    if import_id:
        import_id_var.set(import_id)


def get_context() -> Dict[str, str]:
    return {
        "business_id": business_id_var.get(),
        "import_id": import_id_var.get(),
        "request_id": request_id_var.get(),
    }


class ContextAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict):
        extra = kwargs.setdefault("extra", {})
        extra.update({k: v for k, v in get_context().items() if v})
        return msg, kwargs


def get_logger(name: str) -> ContextAdapter:
    return ContextAdapter(logging.getLogger(name), {})
