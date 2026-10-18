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

    # --- Derived helpers -----------------------------------------------

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, v: str) -> str:
        if not v:
            raise ValueError("database_url must not be empty")
        return v

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
