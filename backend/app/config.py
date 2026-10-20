"""Application configuration.

All runtime limits and secrets are configurable via environment variables
with the ``CALLORA_`` prefix so the same image can run in dev, test, and
production without code changes.
"""

from functools import lru_cache
from typing import Dict, List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CALLORA_", env_file=".env", extra="ignore")

    # --- Database -------------------------------------------------------
    database_url: str = "postgresql+psycopg2://callora:callora@localhost:5432/callora"

    # --- Security -------------------------------------------------------
    # Comma separated mapping of "business_id:api_key". Example:
    # "biz_001:secret1,biz_002:secret2"
    api_keys: str = "biz_001:dev-key-biz-001"

    # --- Import limits --------------------------------------------------
    max_upload_size_mb: int = Field(default=10, ge=1, le=100)
    max_rows_per_import: int = Field(default=50_000, ge=1)
    max_custom_fields: int = Field(default=50, ge=0)

    # Imports with more rows than this threshold are processed
    # asynchronously; smaller ones are processed inline.
    sync_processing_row_threshold: int = Field(default=1_000, ge=1)

    # Fallback lead source when the uploaded data carries no source column.
    default_lead_source: str = "import"

    # Unmapped columns are captured into ``custom_fields``. When this is
    # disabled only the columns listed in ``custom_field_allowlist`` are kept.
    auto_capture_custom_fields: bool = True
    custom_field_allowlist: str = ""

    # --- Events ---------------------------------------------------------
    # When set, pending lead.created events are delivered via HTTP POST.
    # When unset the transactional outbox remains the delivery mechanism
    # and downstream modules read events from it.
    event_webhook_url: Optional[str] = None
    event_publish_max_attempts: int = Field(default=5, ge=1)

    # --- Logging --------------------------------------------------------
    log_level: str = "INFO"

    # When true, unhandled exceptions are re-raised instead of being masked
    # by the generic 500 handler. Useful in tests and local debugging.
    debug: bool = False
    
    
    # --- CRM Synchronization -------------------------------------------
    # Base URL of the external UrbanNestCRM service. Must be an absolute
    # http(s) URL reachable from wherever this app runs. Inside Docker
    # that means a hostname resolvable from the backend container — a
    # compose service name on a shared network (e.g.
    # http://urbannestcrm:9000) or host.docker.internal when the CRM runs
    # on the host. "localhost:9000" inside the container is the container
    # itself and will never reach the CRM. See docker-compose.yml and
    # .env.example.
    crm_base_url: str | None = None
    crm_api_key: str | None = None
    crm_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    # The "business_name" value UrbanNestCRM's POST /contacts schema
    # requires; identifies whose leads are being synchronized.
    crm_business_name: str = "UrbanNest Realty"
    # Development escape hatch: allow loopback/private CRM destinations
    # (e.g. a fake CRM on localhost). Must remain false in production —
    # connection URLs are SSRF-validated against private ranges.
    crm_allow_private_urls: bool = False
    # Explicit, development-only allowlist of non-public CRM destinations.
    # Comma-separated `host:port` entries; empty by default so production
    # still rejects private/loopback/link-local destinations. Example for a
    # fake CRM on the Docker host:
    #   CALLORA_CRM_DEV_ALLOWED_DESTINATIONS=host.docker.internal:9000
    crm_dev_allowed_destinations: str = ""

    # --- Module 3 — Recovery Agent (Opportunity Detection) ---------------
    # Recovery criteria (pure rules, evaluated per lead).
    recovery_sla_minutes: int = Field(default=120, ge=1)
    recovery_stall_days: int = Field(default=7, ge=1)
    recovery_score_threshold: float = Field(default=0.40, ge=0.0, le=1.0)
    recovery_max_attempts: int = Field(default=3, ge=1)
    # Quiet hours only gate *outreach*; when disabled (default) a sweep
    # run at night still produces recommendations instead of archiving
    # every candidate. The ACT stage enables this before sending.
    recovery_enforce_quiet_hours: bool = False
    recovery_quiet_hours_start: int = Field(default=21, ge=0, le=23)
    recovery_quiet_hours_end: int = Field(default=8, ge=0, le=23)
    # The agent fetches leads through GET /api/v1/agent/leads. When
    # recovery_api_base_url is unset the request is dispatched
    # in-process (ASGI transport) through the exact same API contract,
    # including X-API-Key authentication. Set it to a base URL (e.g.
    # http://backend:8001) to fetch over the network instead.
    recovery_api_base_url: Optional[str] = None
    recovery_fetch_page_size: int = Field(default=200, ge=1, le=500)
    recovery_fetch_timeout_seconds: float = Field(default=30.0, gt=0, le=120)

    # --- LLM (Recovery Strategist) ---------------------------------------
    # Optional: when no key is configured the strategist degrades to the
    # deterministic rules-fallback planner and the sweep still completes.
    groq_api_key: Optional[str] = None
    groq_model: str = "openai/gpt-oss-120b"
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)

    # --- Derived helpers -----------------------------------------------

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, v: str) -> str:
        if not v:
            raise ValueError("database_url must not be empty")
        return v

    @field_validator("crm_base_url")
    @classmethod
    def _validate_crm_base_url(cls, v: Optional[str]) -> Optional[str]:
        """Fail fast on CRM base URLs that could never be reached."""
        if v is None or not v.strip():
            return None
        cleaned = v.strip().rstrip("/")
        if not cleaned.lower().startswith(("http://", "https://")):
            raise ValueError(
                "crm_base_url must be an absolute http(s) URL reachable from "
                "the backend, e.g. http://urbannestcrm:9000 (container "
                "network) or http://host.docker.internal:9000 (CRM on host); "
                f"got {v!r}"
            )
        return cleaned

    @property
    def api_key_map(self) -> Dict[str, str]:
        """Parse ``api_keys`` into ``{business_id: api_key}``."""
        mapping: Dict[str, str] = {}
        for pair in self.api_keys.split(","):
            pair = pair.strip()
            if not pair:
                continue
            business_id, _, key = pair.partition(":")
            if business_id and key:
                mapping[business_id.strip()] = key.strip()
        return mapping

    @property
    def custom_field_allowlist_set(self) -> List[str]:
        return [c.strip() for c in self.custom_field_allowlist.split(",") if c.strip()]

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
