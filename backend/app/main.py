"""Callora FastAPI application.

Module 1 (Lead & Customer Import) exposes the lead and import routers on
top of the existing health/root endpoints.
Module 2 (CRM Synchronization) and agent query endpoints are registered
under /api/v1.
Module 6 (Outbound Voice Agent) registers the voice routers — business
configuration, leads, calls, handoffs, operations and the provider
webhooks — under /api/v1/voice.
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
from app.modules.recovery_dashboard import router as recovery_dashboard_api
from app.modules.analytics_agent import router as analytics_api
from app.modules.analytics_agent.dashboard_ui import render_analytics_dashboard
from fastapi.responses import HTMLResponse
from app.modules.voice.api import businesses as voice_businesses_api
from app.modules.voice.api import calls as voice_calls_api
from app.modules.voice.api import followup as voice_followup_api
from app.modules.voice.api import handoffs as voice_handoffs_api
from app.modules.voice.api import knowledge as voice_knowledge_api
from app.modules.voice.api import leads as voice_leads_api
from app.modules.voice.api import ops as voice_ops_api
from app.modules.voice.api import webhooks as voice_webhooks_api
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
    version="0.5.0",
    description=(
        "Callora platform API. Module 1: Lead & Customer Import – accepts "
        "CSV uploads and JSON/API submissions, maps them to the canonical "
        "lead schema, validates, deduplicates and persists records. "
        "Module 2: automatic CRM synchronization and agent-ready data storage. "
        "Module 6: configurable outbound AI voice agent – compliance-gated "
        "call initiation (Bolna), webhook intake, post-call analysis and "
        "human handoff."
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
app.include_router(recovery_dashboard_api.router, prefix=API_PREFIX)

# Outbound voice agent (Module 6).
app.include_router(voice_businesses_api.router, prefix=API_PREFIX)
app.include_router(voice_leads_api.router, prefix=API_PREFIX)
app.include_router(voice_calls_api.router, prefix=API_PREFIX)
app.include_router(voice_handoffs_api.router, prefix=API_PREFIX)
app.include_router(voice_ops_api.router, prefix=API_PREFIX)
app.include_router(voice_webhooks_api.router, prefix=API_PREFIX)
app.include_router(voice_knowledge_api.router, prefix=API_PREFIX)
app.include_router(voice_followup_api.router, prefix=API_PREFIX)

# Analytics Agent routes (Module 7).
# Registered at root level (/analytics/...) and under /api/v1 (/api/v1/analytics/...)
app.include_router(analytics_api.router)
app.include_router(analytics_api.router, prefix=API_PREFIX)


@app.get("/")
def read_root():
    return {"message": "Welcome to Callora FastAPI Backend"}


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"])
def read_dashboard(business_id: str = "biz_001"):
    return HTMLResponse(content=render_analytics_dashboard(business_id=business_id))
