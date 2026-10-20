"""Application configuration.

All runtime limits and secrets are configurable via environment variables
with the ``CALLORA_`` prefix so the same image can run in dev, test, and
production without code changes.
"""

from functools import lru_cache
from typing import Dict, List, Optional

from pydantic import AliasChoices, Field, field_validator
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

    # --- Module 3 — deterministic ELIGIBILITY rules ----------------------
    # The eligibility gate runs BEFORE scoring/recommendation and only
    # admits opportunities that are provably recoverable. All of these
    # default to the existing business behaviour ("do not invent rules"):
    # a phone is the only contact channel the engine can actually use, and
    # an unknown/missing phone must never be treated as eligibility
    # evidence — that is the production defect this gate fixes.
    #
    # Canonical global recontact suppression (in addition to the per-lead
    # ``custom_fields.opt_out`` / ``opted_out`` flags already honoured by
    # the detector). Comma separated. This is business *configuration*,
    # not a hardcoded rule: the column/flag names come from the existing
    # import mapping (``Do Not Contact`` → ``do_not_contact``).
    recovery_opt_out_flags: str = "opt_out,opted_out,do_not_contact,dnc,unsubscribed,suppressed"
    # A lead whose canonical status already IS the recovery end-state is
    # not an opportunity to recover. Matched case-insensitively against
    # ``status`` only when ``is_recovered`` is absent (an explicit flag is
    # always honoured first).
    recovery_recovered_statuses: str = "recovered"
    # Statuses that an operator has closed out of recovery. Such leads are
    # not re-opened by a later sweep — they are *ignored* (no signal, no
    # candidate, no case), so any persisted case keeps its lifecycle.
    recovery_ignored_statuses: str = "dismissed,suppressed,resolved,completed,in_recovery"

    # --- Module 5 — Priority Scoring -------------------------------------
    # Deterministic ranking of the *eligible* recovery cases produced by
    # Modules 3/4. Scoring never re-runs eligibility: it consumes cases
    # that already passed the gate and ranks them by expected business
    # value of acting now. No LLM is involved in any number produced
    # here — every factor is computed from stored lead data and the
    # verified recovery signals already persisted on the case.
    #
    # Default weights (assumptions documented in wworkflow.md):
    #   business value 30 — deal size dominates "who to call first";
    #   urgency 25       — SLA breach magnitude drives time-sensitivity;
    #   buying intent 20 — evidence of engagement converts better;
    #   recovery potential 15 — strength of the detected opportunity;
    #   contactability 10 — a reachable lead is worth more than an
    #                        unreachable one at equal everything else.
    # Weights are normalized (they need not sum to 100); each factor
    # contributes weight × ratio, total = 100 × Σ/Σweights.
    priority_weight_business_value: float = Field(default=30.0, ge=0.0)
    priority_weight_urgency: float = Field(default=25.0, ge=0.0)
    priority_weight_intent: float = Field(default=20.0, ge=0.0)
    priority_weight_recovery_potential: float = Field(default=15.0, ge=0.0)
    priority_weight_contactability: float = Field(default=10.0, ge=0.0)
    # Deal value at which the business-value factor reaches full points
    # (same reference the recoverability score already uses, so the two
    # stay comparable). Missing/unknown deal value scores 0 — it is
    # never treated as evidence of value.
    priority_deal_value_reference: float = Field(default=10_000_000.0, gt=0.0)
    # Time beyond the SLA / stall window at which urgency reaches full
    # points (defaults: 7 days beyond SLA, 30 days of stall).
    priority_urgency_full_breach_minutes: int = Field(default=10_080, ge=1)
    priority_urgency_full_stall_days: int = Field(default=30, ge=1)
    # Optional custom_fields flags (comma separated, truthy) that count
    # as buying-intent evidence (e.g. "budget_confirmed,site_visit").
    # Empty by default: Callora must not assume intent the data does
    # not carry. Prior recorded contact counts as engagement regardless.
    priority_intent_flags: str = ""
    # Priority level thresholds (score >= threshold). critical >= 80,
    # high >= 60, medium >= 40, low below that — configurable so a
    # business can tune triage without code changes.
    priority_level_critical_min: float = Field(default=80.0, ge=0.0, le=100.0)
    priority_level_high_min: float = Field(default=60.0, ge=0.0, le=100.0)
    priority_level_medium_min: float = Field(default=40.0, ge=0.0, le=100.0)

    @property
    def recovery_opt_out_flag_set(self) -> set[str]:
        return {f.strip().lower() for f in self.recovery_opt_out_flags.split(",") if f.strip()}

    @property
    def priority_intent_flag_set(self) -> set[str]:
        return {f.strip().lower() for f in self.priority_intent_flags.split(",") if f.strip()}

    @property
    def recovery_recovered_status_set(self) -> set[str]:
        return {s.strip().lower() for s in self.recovery_recovered_statuses.split(",") if s.strip()}

    @property
    def recovery_ignored_status_set(self) -> set[str]:
        return {s.strip().lower() for s in self.recovery_ignored_statuses.split(",") if s.strip()}

    # --- LLM (Recovery Strategist) ---------------------------------------
    # Optional: when no key is configured the strategist degrades to the
    # deterministic rules-fallback planner and the sweep still completes.
    # Canonical env var is GROQ_API_KEY (matches .env); the legacy
    # CALLORA_GROQ_API_KEY name is still accepted as a fallback.
    groq_api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("GROQ_API_KEY", "CALLORA_GROQ_API_KEY"),
    )
    groq_model: str = "openai/gpt-oss-120b"
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)

    # --- Outreach content (Recovery Dashboard) ---------------------------
    # Display name used to sign AI-drafted outreach (email signatures,
    # voice-script sign-offs) and to resolve "[Agent Name]"-style
    # placeholders the model may emit. It is a name/label only — it must
    # never be used to infer the agent's gender. When unset, a neutral
    # default ("Callora Team") is used so no draft ever ships with an
    # unresolved placeholder.
    agent_display_name: Optional[str] = None
    # Safe fallback when a lead's language is missing or is not one of
    # the languages the outreach templates support ("hindi", "english").
    # This is the only place the fallback language is decided — Hindi is
    # never hardcoded for all leads.
    default_outreach_language: str = "english"

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
