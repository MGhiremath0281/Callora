"""Job handlers.

A handler receives the job and does the work. It must be safe to run
twice: the post-call workflow overwrites the outcome of a call instead of
appending, so a retried job produces the same result.
"""

import logging
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.config import Settings
from app.modules.voice import repository as repo
from app.modules.voice.jobs import queue
from app.modules.voice.postcall.graph import PostCallWorkflow
from app.modules.voice.postcall.llm import build_llm_client

logger = logging.getLogger("callora.voice.jobs.handlers")


def handle_postcall(session: Session, job, settings: Settings) -> Dict[str, Any]:
    """Run the post-call LangGraph workflow for one call."""
    call_id = job.payload.get("call_id")
    if not call_id:
        raise ValueError("the post-call job has no call_id")
    call = repo.get_call_any_business(session, str(call_id))
    if call is None:
        # The call was deleted; nothing to do, and retrying would not help.
        logger.info("postcall_job_skipped_missing_call", extra={"call_id": call_id})
        return {"skipped": "call_not_found"}

    llm = build_llm_client(settings)
    workflow = PostCallWorkflow(settings, llm)
    state = workflow.run(session, {"call_id": call.id})
    session.commit()

    outcome = repo.get_outcome(session, call.id)
    logger.info(
        "postcall_processed",
        extra={
            "call_id": call.id,
            "disposition": state.get("disposition"),
            "engine": state.get("engine"),
        },
    )
    return {
        "outcome_id": outcome.id if outcome else None,
        "disposition": state.get("disposition"),
        "engine": state.get("engine"),
    }


#: type -> handler
DEFAULT_HANDLERS = {
    "postcall.process": handle_postcall,
}


def get_handler(job_type: str):
    return DEFAULT_HANDLERS.get(job_type)
