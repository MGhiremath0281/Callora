"""The background worker.

The worker claims jobs from the durable PostgreSQL queue, runs the matching
handler and records the outcome. Failed jobs are retried with exponential
backoff until they exhaust their attempts, at which point they are parked
as ``dead`` and can be retried explicitly through the API.

Run it as a process::

    python -m app.modules.voice.jobs.worker

or in a container (see docker-compose.yml).
"""

import logging
import os
import signal
import socket
import time
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.session import get_session_factory
from app.modules.voice import sync
from app.modules.voice.errors import LLMUnavailableError
from app.modules.voice.jobs import queue
from app.modules.voice.jobs.handlers import DEFAULT_HANDLERS
from app.modules.voice.providers.base import VoiceProvider
from app.logging_config import setup_logging

logger = logging.getLogger("callora.voice.jobs.worker")

#: Errors that will never succeed on a retry.
NON_RETRYABLE = (LLMUnavailableError, LookupError, ValueError)


def default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


class Worker:
    """Processes queued jobs."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        session_factory: Optional[sessionmaker] = None,
        handlers: Optional[Dict[str, Callable[[Session, Any], Any]]] = None,
        worker_id: Optional[str] = None,
        job_types: Optional[List[str]] = None,
        provider: Optional[VoiceProvider] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._session_factory = session_factory or get_session_factory()
        self._handlers = handlers if handlers is not None else dict(DEFAULT_HANDLERS)
        self._worker_id = worker_id or default_worker_id()
        self._job_types = job_types
        self._provider = provider
        self._last_sync: Optional[float] = None

    # -- one iteration -----------------------------------------------------

    def run_once(self, limit: int = 5) -> int:
        """Process up to ``limit`` jobs. Returns the number processed."""
        processed = 0
        for _ in range(limit):
            session = self._session_factory()
            try:
                job = queue.claim_next(
                    session, self._worker_id, job_types=self._job_types
                )
                if job is None:
                    session.close()
                    return processed
                self._run_job(session, job)
                processed += 1
            finally:
                try:
                    session.close()
                except Exception:  # pragma: no cover - defensive
                    pass
        return processed

    def _run_job(self, session: Session, job) -> None:
        handler = self._handlers.get(job.type)
        if handler is None:
            queue.mark_failed(
                session,
                job,
                f"no handler is registered for job type '{job.type}'",
                retryable=False,
            )
            session.commit()
            return
        try:
            result = handler(session, job, self._settings)
        except Exception as exc:
            retryable = not isinstance(exc, NON_RETRYABLE)
            logger.warning(
                "job_failed",
                extra={"job_id": job.id, "type": job.type, "retryable": retryable},
            )
            queue.mark_failed(
                session,
                job,
                f"{type(exc).__name__}: {exc}",
                retryable=retryable,
                backoff_seconds=self._settings.voice_job_retry_backoff_seconds,
            )
            session.commit()
            return
        queue.mark_succeeded(session, job, result if isinstance(result, dict) else None)
        session.commit()
        logger.info("job_succeeded", extra={"job_id": job.id, "type": job.type})

    # -- the reconciliation sweep ------------------------------------------

    def run_sync(self) -> None:
        """Reconcile the calls whose status the provider did not report.

        The provider webhook is the fast path. This sweep is the safety
        net: it asks the provider about the calls that are due for a
        check, so a call can never stay in ``scheduled`` because a
        webhook was lost.
        """
        session = self._session_factory()
        try:
            report = sync.sweep(session, self._settings, provider=self._provider)
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("status_sync_failed")
            return
        finally:
            try:
                session.close()
            except Exception:  # pragma: no cover - defensive
                pass
        logger.info(
            "status_sync_completed",
            extra={
                "checked": report.checked,
                "applied": report.applied,
                "unchanged": report.unchanged,
                "skipped": report.skipped,
                "failed": report.failed,
            },
        )

    def _maybe_sync(self) -> None:
        """Run the sweep when it is due."""
        interval = self._settings.voice_status_sync_interval_seconds
        if interval <= 0:
            return
        now = time.monotonic()
        if self._last_sync is not None and now - self._last_sync < interval:
            return
        self._last_sync = now
        self.run_sync()

    # -- long running ------------------------------------------------------

    def run_forever(self, interval: float = 2.0, max_iterations: Optional[int] = None) -> None:
        """Poll the queue until interrupted."""
        stopping = {"flag": False}

        def _handle_signal(signum, frame):  # pragma: no cover - signal path
            stopping["flag"] = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handle_signal)
            except (ValueError, OSError):  # pragma: no cover - non-main thread
                pass

        iterations = 0
        logger.info("worker_started", extra={"worker_id": self._worker_id})
        while not stopping["flag"]:
            try:
                processed = self.run_once()
            except Exception:  # pragma: no cover - defensive
                logger.exception("worker_iteration_failed")
                processed = 0
            try:
                self._maybe_sync()
            except Exception:  # pragma: no cover - defensive
                logger.exception("worker_status_sync_failed")
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                break
            if processed == 0:
                time.sleep(interval)
        logger.info("worker_stopped", extra={"worker_id": self._worker_id})


def main() -> None:  # pragma: no cover - process entry point
    settings = get_settings()
    setup_logging(settings.log_level)
    worker = Worker(settings)
    worker.run_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
