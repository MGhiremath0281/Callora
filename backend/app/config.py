"""Application configuration.

All runtime limits and secrets are configurable via environment variables
with the ``CALLORA_`` prefix so the same image can run in dev, test, and
production without code changes.
"""

from functools import lru_cache
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.logging_config import get_logger

#: ``backend/`` — the directory that holds the application package.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
#: The repository root — the directory that holds ``docker-compose.yml`` and
#: the shared ``.env`` file.
_REPO_ROOT = _BACKEND_DIR.parent

logger = get_logger("callora.config")

#: Environment names that unlock the development-only overrides. Every other
#: value — including an unknown or misspelled one — is treated as production,
#: so a typo can never relax a compliance rule.
DEVELOPMENT_ENVIRONMENTS = frozenset(
    {"local", "development", "dev", "test", "testing"}
)


def _dotenv_files() -> tuple[str, ...]:
    """The dotenv files to load, most general first.

    The paths are resolved from the package location, not from the current
    working directory, so the same file is read whether the app is started
    from the repository root, from ``backend/``, or inside the container.

    The test suite sets ``CALLORA_IGNORE_DOTENV=1`` so a developer's local
    secrets can never influence a test run.
    """
    if os.getenv("CALLORA_IGNORE_DOTENV", "").strip().lower() in ("1", "true", "yes"):
        return ()
    # Later entries win, so ``backend/.env`` overrides the shared
    # ``<repo>/.env``.
    return (str(_REPO_ROOT / ".env"), str(_BACKEND_DIR / ".env"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CALLORA_",
        env_file=_dotenv_files(),
        extra="ignore",
    )

    # --- Database -------------------------------------------------------
    database_url: str = "postgresql+psycopg2://callora:callora@localhost:5432/callora"

    # --- Security -------------------------------------------------------
    # Comma separated mapping of "business_id:api_key". Example:
    # "biz_001:secret1,biz_002:secret2"
    api_keys: str = "biz_001:dev-key-biz-001"

    # --- Deployment environment -----------------------------------------
    # Which environment this process runs in. "production" is the default
    # and the only environment in which every compliance rule is
    # unconditionally enforced. The development-only overrides below are
    # honoured *only* when this names an explicit development environment.
    environment: str = "production"

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

    # --- Outbound Voice Agent (Bolna) ------------------------------------
    # Global kill switch for real outbound calling. While this is false the
    # orchestrator performs the full compliance gate and persists a call
    # record, but never contacts the voice provider. It must be explicitly
    # enabled (and provider credentials provisioned) before money is spent.
    voice_enable_outbound_calls: bool = False

    # --- Redis (temporary state / rate limiting / caching) ---------------
    # Redis is an accelerator, never the source of truth for a compliance
    # decision: every check it supports has a PostgreSQL fallback.
    redis_url: str = "redis://localhost:6379/0"
    redis_enabled: bool = True
    redis_key_prefix: str = "callora"
    redis_socket_timeout_seconds: float = Field(default=1.0, gt=0.0, le=30.0)
    # Per-business call-initiation rate limit (requests per minute).
    voice_rate_limit_per_minute: int = Field(default=60, ge=1, le=10_000)
    # TTL of the cached DND / business-config lookups.
    voice_cache_ttl_seconds: int = Field(default=300, ge=1, le=86_400)

    # --- Bolna (voice provider) ------------------------------------------
    bolna_api_key: Optional[str] = None
    bolna_base_url: str = "https://api.bolna.ai"
    bolna_timeout_seconds: float = Field(default=20.0, gt=0.0, le=120.0)
    # Shared secret used to verify webhook signatures. Bolna does not
    # document an HMAC scheme for execution webhooks, so this is optional:
    # when it is unset the webhook endpoint cannot verify signatures and
    # only accepts requests from the documented Bolna source IPs (if
    # configured). Set it to enable HMAC-SHA256 verification.
    bolna_webhook_secret: Optional[str] = None
    # Fallback agent id used when a business does not configure its own.
    bolna_default_agent_id: Optional[str] = None
    # Deprecated — no longer used. The caller number of an outbound call
    # is never defaulted: it is either carried by the request or absent,
    # and an absent one is left out of the Bolna request so Bolna places
    # the call through the telephony provider of the agent. The setting is
    # kept (optional, blank means "not configured") so an existing
    # deployment still starts and still places calls; it is ignored.
    bolna_default_from_number: Optional[str] = None
    # Default per-call timeouts used by the provider adapter.
    voice_provider_max_attempts: int = Field(default=3, ge=1, le=10)
    voice_provider_retry_backoff_seconds: float = Field(default=1.0, ge=0.0, le=60.0)

    # --- Provider status reconciliation ----------------------------------
    # The provider webhook is the fast path. The reconciliation sweep is
    # the safety net for the webhooks that never arrive (no webhook_url
    # on the agent, a dropped request, a rejected payload): it polls the
    # provider for the status of the calls Callora knows about.
    # How often a single call may be checked with the provider.
    voice_status_poll_interval_seconds: int = Field(default=60, ge=0, le=3_600)
    # How often the background worker runs the sweep. 0 disables it.
    voice_status_sync_interval_seconds: int = Field(default=60, ge=0, le=3_600)
    # How many calls are checked in one sweep.
    voice_status_sync_max_calls_per_sweep: int = Field(default=50, ge=1, le=1_000)
    # How long a call may stay in a pre-dial state (scheduled / queued)
    # before the timeout policy applies. The provider is always asked
    # first: the call is only closed when the provider confirms that it
    # is still waiting and the call was due long enough ago. A call that
    # is talking to somebody is never closed this way.
    voice_scheduled_call_timeout_seconds: int = Field(default=900, ge=0, le=86_400)
    # What the timeout policy does: "fail" closes the call as failed,
    # "keep_polling" only reports it and leaves it in place.
    voice_stuck_call_timeout_action: str = "fail"

    # --- Outbound calling policy defaults --------------------------------
    # IANA timezone used to evaluate the permitted calling window.
    voice_default_timezone: str = "Asia/Kolkata"
    voice_default_calling_window_start: int = Field(default=10, ge=0, le=23)
    voice_default_calling_window_end: int = Field(default=19, ge=0, le=23)
    # Maximum number of outbound calls placed for one lead per calendar day
    # in the business timezone.
    voice_max_calls_per_lead_per_day: int = Field(default=1, ge=1, le=50)
    # Maximum number of outbound calls placed per business per calendar day.
    voice_max_calls_per_business_per_day: int = Field(default=500, ge=1, le=100_000)
    # Development-only: skip the daily per-lead and per-business call
    # quotas so a local integration can be exercised repeatedly. The
    # override needs two explicit opt-ins — this flag *and* a development
    # ``environment`` — and it relaxes nothing else: consent, DND, the
    # calling window, the duplicate-call protection, the concurrency cap
    # and the provider check all stay in force. Outside a development
    # environment the flag is ignored (see ``call_quotas_disabled``).
    disable_call_quotas: bool = False
    # Maximum number of calls in flight for one business at the same time.
    voice_max_concurrent_calls: int = Field(default=5, ge=1, le=1_000)
    # Minimum gap between two calls to the same lead (anti-duplicate rule).
    #
    # The value is expressed in *seconds* so a short local-development
    # cooldown (60 seconds) can be configured without a code change, while
    # the production default stays at 60 minutes (3600 seconds). ``0``
    # disables the cooldown — the in-flight guard (a call that Bolna still
    # reports as queued, ringing or active) always stays in force.
    voice_duplicate_call_cooldown_seconds: int = Field(default=3600, ge=0, le=604_800)
    # Deprecated: the former minute-based variable. It is still honoured —
    # converted to seconds — when the seconds variable above is not set, so
    # an existing deployment keeps its behaviour. A legacy ``60`` still means
    # 60 minutes and never 60 seconds.
    voice_duplicate_call_cooldown_minutes: Optional[int] = Field(
        default=None, ge=0, le=10_080
    )
    # Languages the voice pipeline is configured for. Used to validate
    # per-call language selection (Hindi / Kannada / English).
    voice_supported_languages: str = "en,hi,kn"
    # Maximum transcript size (characters) handed to the post-call LLM.
    voice_transcript_max_chars: int = Field(default=40_000, ge=500, le=400_000)

    # --- Recovery workflow execution ------------------------------------
    # Maximum number of approved/running/paused workflow runs per business.
    workflow_max_active_runs: int = Field(default=20, ge=1, le=10_000)
    # Anti-duplicate gap between two non-voice workflow contacts (seconds).
    # 0 disables the message cooldown; consent/DND rules always remain.
    workflow_message_cooldown_seconds: int = Field(default=3600, ge=0, le=604_800)
    # A workflow run may only execute without explicit approval when the
    # business has switched this on AND the action is a dry run or an
    # internal, non-outbound step. Real outbound calls/messages always
    # require approval, regardless of this flag.
    workflow_allow_unapproved_internal: bool = False
    # Reminder delivery adapters. "none" reports channel_not_configured
    # instead of pretending a reminder was sent.
    reminder_delivery_provider: str = "none"
    # Calendar booking provider. "internal" records a Callora-owned
    # appointment; external providers are only confirmed on a real
    # provider response with an event id.
    booking_provider: str = "internal"
    # Minimum observed call outcomes per window before analytics reports a
    # rate comparison as sufficient.
    analytics_min_sample_per_window: int = Field(default=5, ge=1, le=10_000)
    # Leakage analytics need at least this many analysed calls before any
    # leakage percentage is reported.
    analytics_min_calls_for_leakage: int = Field(default=10, ge=1, le=100_000)

    # --- Post-call AI workflow (LangGraph) -------------------------------
    # "groq" uses the Groq chat model through LangChain; "langchain" uses
    # whatever LangChain chat model is configured; "null" disables the LLM
    # (the workflow then only records what the provider already extracted).
    voice_llm_provider: str = "groq"
    voice_llm_model: Optional[str] = None
    voice_llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    voice_llm_timeout_seconds: float = Field(default=60.0, gt=0.0, le=600.0)
    # Maximum number of post-call workflow attempts before a job is parked
    # as permanently failed.
    voice_job_max_attempts: int = Field(default=5, ge=1, le=50)
    voice_job_retry_backoff_seconds: float = Field(default=10.0, ge=0.0, le=3_600.0)

    # --- Bolna custom functions (mid-call tools) --------------------------
    # Shared secret the Bolna runtime presents as "Authorization: Bearer
    # <token>" on /api/v1/voice/tools/*. An empty value disables the tool
    # endpoints entirely (401 on every request) — the app runs fine without
    # the integration configured. Business API keys from CALLORA_API_KEYS
    # are accepted on the same endpoints as an operator path.
    voice_tool_secret: str = ""
    voice_tool_rate_limit_per_minute: int = Field(default=120, ge=1, le=10_000)
    # Whether a scheduled callback automatically enqueues a dispatch job
    # (which re-runs the full compliance gate before dialing). Default off:
    # without it callbacks are recorded for the team to action manually.
    voice_auto_callback_dispatch: bool = False
    voice_callback_max_days_ahead: int = Field(default=30, ge=1, le=365)
    voice_qualification_min_score: float = Field(default=0.6, ge=0.0, le=1.0)

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

    @field_validator("bolna_default_from_number", mode="before")
    @classmethod
    def _normalize_bolna_default_from_number(cls, v: Any) -> Optional[str]:
        """Treat a blank caller number as "not configured".

        The value is deprecated and never used to fill in a caller number,
        but it stays optional: an unset, empty or whitespace-only value
        must never keep the application from starting.
        """
        if v is None:
            return None
        text = str(v).strip()
        return text or None

    @field_validator("voice_stuck_call_timeout_action", mode="before")
    @classmethod
    def _validate_stuck_call_timeout_action(cls, v: Any) -> str:
        """Only the two documented timeout policies are accepted."""
        text = str(v or "").strip().lower()
        if text not in ("fail", "keep_polling"):
            raise ValueError(
                "voice_stuck_call_timeout_action must be 'fail' or "
                f"'keep_polling', got {v!r}"
            )
        return text

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

    @property
    def voice_supported_language_set(self) -> set[str]:
        return {s.strip().lower() for s in self.voice_supported_languages.split(",") if s.strip()}

    @property
    def duplicate_call_cooldown_seconds(self) -> int:
        """The effective duplicate-call cooldown, in seconds.

        The canonical variable is
        ``CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_SECONDS`` (default 3600
        seconds = 60 minutes). The deprecated minute-based variable is only
        consulted when the canonical one is absent, and it is converted to
        seconds, so a legacy ``60`` keeps meaning 60 minutes.

        ``0`` disables the cooldown. It never disables the in-flight guard:
        a call that the provider still reports as queued, ringing or active
        always blocks a second call to the same lead.
        """
        if "voice_duplicate_call_cooldown_seconds" in self.model_fields_set:
            return self.voice_duplicate_call_cooldown_seconds
        if self.voice_duplicate_call_cooldown_minutes is not None:
            return self.voice_duplicate_call_cooldown_minutes * 60
        return self.voice_duplicate_call_cooldown_seconds

    @property
    def is_development(self) -> bool:
        """True only for an explicitly named development environment.

        Anything else — "production", an empty value or a typo — is
        treated as production, so an unrecognised name can never unlock a
        development-only override.
        """
        return self.environment.strip().lower() in DEVELOPMENT_ENVIRONMENTS

    @property
    def call_quotas_disabled(self) -> bool:
        """Whether the daily call quotas may be skipped.

        The override needs two explicit opt-ins:

        * ``CALLORA_DISABLE_CALL_QUOTAS=true`` — the request itself, and
        * ``CALLORA_ENVIRONMENT`` naming a development environment.

        Outside such an environment the flag is ignored and reported, so a
        deployment that only sets the flag cannot silently drop the daily
        quotas in production.
        """
        if not self.disable_call_quotas:
            return False
        if not self.is_development:
            logger.warning(
                "call_quota_override_refused",
                extra={
                    "environment": self.environment,
                    "detail": (
                        "the daily call quotas stay enforced outside a "
                        "development environment"
                    ),
                },
            )
            return False
        return True


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.bolna_default_from_number:
        # The variable is ignored: the caller number of a call comes from
        # the request or not at all. Say so once, so an operator does not
        # keep a setting that looks effective and is not.
        logger.warning(
            "bolna_default_from_number_ignored",
            extra={
                "detail": (
                    "CALLORA_BOLNA_DEFAULT_FROM_NUMBER is deprecated and "
                    "ignored; the caller number of a call is taken from the "
                    "request only"
                ),
            },
        )
    return settings
