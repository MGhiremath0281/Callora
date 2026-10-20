"""Callora FastAPI application.

Module 1 (Lead & Customer Import) exposes the lead and import routers on
top of the existing health/root endpoints.
Module 2 (CRM Synchronization) and agent query endpoints are registered
under /api/v1.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import agent_leads as agent_leads_api
from app.api import crm_connections as crm_connections_api
from app.api import imports as imports_api
from app.api import leads as leads_api
from app.config import get_settings
from app.errors import register_exception_handlers
from app.logging_config import setup_logging
from app.modules.crm_sync import connection_seed
from app.modules.crm_sync import router as crm_sync_api
from app.modules.recovery_agent import router as recovery_agent_api
from app.db.session import get_session_factory

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)

    # Seed CRM connections from config.
    try:
        session = get_session_factory()()
        connection_seed.seed_crm_connections(session)
        session.close()
    except Exception:
        pass

    yield


app = FastAPI(
    title="Callora Backend",
    version="0.4.0",
    description=(
        "Callora platform API. Module 1: Lead & Customer Import – accepts "
        "CSV uploads and JSON/API submissions, maps them to the canonical "
        "lead schema, validates, deduplicates and persists records. "
        "Module 2: automatic CRM synchronization and agent-ready data storage."
    ),
    lifespan=lifespan,
)

register_exception_handlers(app)

# All business routes are served under /api/v1 only.
app.include_router(leads_api.router, prefix=API_PREFIX)
app.include_router(imports_api.router, prefix=API_PREFIX)
app.include_router(crm_sync_api.router, prefix=API_PREFIX)
app.include_router(crm_connections_api.router, prefix=API_PREFIX)
app.include_router(agent_leads_api.router, prefix=API_PREFIX)
app.include_router(recovery_agent_api.router, prefix=API_PREFIX)


@app.get("/")
def read_root():
    return {"message": "Welcome to Callora FastAPI Backend"}


@app.get("/health")
def health_check():
    return {"status": "healthy"}
