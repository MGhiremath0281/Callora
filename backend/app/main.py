"""Callora FastAPI application.

Module 1 (Lead & Customer Import) exposes the lead and import routers on
top of the existing health/root endpoints.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import imports as imports_api
from app.api import leads as leads_api
from app.config import get_settings
from app.errors import register_exception_handlers
from app.logging_config import setup_logging
from app.modules.crm_sync import router as crm_sync_api

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    yield


app = FastAPI(
    title="Callora Backend",
    version="0.2.0",
    description=(
        "Callora platform API. Module 1: Lead & Customer Import – accepts "
        "CSV uploads and JSON/API submissions, maps them to the canonical "
        "lead schema, validates, deduplicates and persists records."
    ),
    lifespan=lifespan,
)

register_exception_handlers(app)

# Versioned API surface.
app.include_router(leads_api.router, prefix=API_PREFIX)
app.include_router(imports_api.router, prefix=API_PREFIX)
app.include_router(crm_sync_api.router, prefix=API_PREFIX)

# Unversioned aliases so the documented endpoints (/leads, /imports/...)
# work directly as specified in the Module 1 API contract.
app.include_router(leads_api.router)
app.include_router(imports_api.router)


@app.get("/")
def read_root():
    return {"message": "Welcome to Callora FastAPI Backend"}


@app.get("/health")
def health_check():
    return {"status": "healthy"}
