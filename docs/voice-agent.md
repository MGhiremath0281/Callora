# Module 6 — Outbound AI Voice Agent (Bolna)

A configurable outbound AI voice sales agent for Indian businesses.

The agent places a call through [Bolna](https://www.bolna.ai), records what
happened, and turns the conversation into structured intelligence (summary,
qualification result, refusal category, follow-up actions) plus a briefing
for a human when the lead has to be handed over.

Everything lives in the backend: the API, the compliance gate, the webhook
intake, the background job queue and the post-call AI workflow. There is no
frontend in this milestone.

> **No real call is placed by default.** `CALLORA_VOICE_ENABLE_OUTBOUND_CALLS`
> is `false` out of the box, so every accepted call is recorded as
> `simulated` and no provider is contacted. Turning it on is a deliberate,
> documented step — see [Enabling real calling](#7-enabling-real-calling).

---

## Table of contents

1. [Architecture](#1-architecture)
2. [Local setup](#2-local-setup)
3. [Configuration reference](#3-configuration-reference)
4. [The compliance gate](#4-the-compliance-gate)
5. [API reference](#5-api-reference)
   - [5.6 Follow-up Q&A and Business Knowledge Base](#56-follow-up-qa-and-business-knowledge-base)
   - [5.7 Bolna Live Custom Tool Integration](#57-bolna-live-custom-tool-integration)
   - [5.8 Conversation Memory & AI Guardrails](#58-conversation-memory--ai-guardrails)
   - [5.9 Bolna Agent Studio Custom Functions](#59-bolna-agent-studio-custom-functions)
6. [The post-call workflow](#6-the-post-call-workflow)
7. [Enabling real calling](#7-enabling-real-calling)
8. [Provider costs and risks](#8-provider-costs-and-risks)
9. [Testing & Automated Evaluation](#9-testing--automated-evaluation)
10. [Operational runbook](#10-operational-runbook)
11. [Known limitations](#11-known-limitations)

---

## 1. Architecture

```
                       ┌──────────────────────────────────────────┐
   X-API-Key           │  FastAPI  /api/v1/voice/*                │
   ───────────────────▶│  calls · business · leads · dnd          │
                       │  handoffs · ops · webhooks                │
                       └───────────────┬──────────────────────────┘
                                       │
                                       ▼
                       ┌──────────────────────────────────────────┐
                       │  CallOrchestrator                        │
                       │   1. compliance gate (fail closed)       │
                       │   2. persist decision + audit trail      │
                       │   3. create the call record              │
                       │   4. contact the provider                │
                       │   5. record provider ids and status      │
                       └───────┬──────────────────────┬───────────┘
                               │                      │
                allowed         │                      │  blocked
                               ▼                      ▼
                  ┌────────────────────┐     ┌──────────────────────────┐
                  │ BolnaProvider      │     │ call.status = "blocked"  │
                  │ POST /call         │     │ 403 compliance_blocked   │
                  │ GET /executions/id │     │ decision + audit written │
                  │ POST /call/id/stop │     └──────────────────────────┘
                  └─────────┬──────────┘
                            │ webhook (execution / pre-transfer)
                            │        ▲
                            │        └── reconciliation sweep (poll
                            │            the provider for the calls it
                            │            never reported on)
                            ▼
                  ┌──────────────────────────────────────────┐
                  │ WebhookService                          │
                  │  verify signature → dedupe → apply      │
                  │  (never move a call backwards) → queue  │
                  └───────────────┬──────────────────────────┘
                                  ▼
                  ┌──────────────────────────────────────────┐
                  │ voice_jobs (PostgreSQL, durable)         │
                  │  dedupe_key · atomic claim · backoff     │
                  └───────────────┬──────────────────────────┘
                                  ▼  python -m app.modules.voice.jobs.worker
                  ┌──────────────────────────────────────────┐
                  │ LangGraph post-call workflow             │
                  │  load_context → extract_qualification   │
                  │  → classify_refusal → summarize         │
                  │  → build_handoff → persist               │
                  └───────────────┬──────────────────────────┘
                                  ▼
             call_outcomes · handoffs · audit_logs
```

### Module layout

| Path | Responsibility |
|---|---|
| `backend/app/modules/voice/models.py` | The 12 new tables (business config, calls, events, outcomes, handoffs, consent, DND, decisions, audit, jobs) |
| `backend/app/modules/voice/repository.py` | Every SQL statement of the module |
| `backend/app/modules/voice/compliance.py` | The nine mandatory pre-call checks |
| `backend/app/modules/voice/orchestrator.py` | The only component allowed to contact a provider |
| `backend/app/modules/voice/providers/` | Provider adapters (`bolna.py`) behind a registry |
| `backend/app/modules/voice/lifecycle.py` | The shared call state machine (one place decides what a status change means) |
| `backend/app/modules/voice/sync.py` | The reconciliation sweep (poll the provider for the calls it never reported on) |
| `backend/app/modules/voice/webhooks/service.py` | Signature, dedupe, ordering, persistence |
| `backend/app/modules/voice/jobs/` | Durable queue, handlers, worker |
| `backend/app/modules/voice/postcall/` | LangGraph workflow, prompts, LLM client |
| `backend/app/modules/voice/handoff/service.py` | Warm transfer / callback bookkeeping |
| `backend/app/modules/voice/api/` | The HTTP layer (routers, deps, serializers) |
| `backend/app/modules/voice/schemas.py` | Request/response models (the OpenAPI contract) |

### Storage rules

* **PostgreSQL is the source of truth.** Consent records, the DND registry,
  call history, compliance decisions and the job queue all live there.
* **Redis is an accelerator only.** It holds the rate-limit counter, cached
  lookups and short-lived call state. Every method degrades to a no-op when
  Redis is unreachable, so losing Redis slows the backend down but never
  turns a compliance block into an approval.
* **A missing mandatory check fails closed.** If a check cannot be
  completed, the call is blocked and the reason is audited.

---

## 2. Local setup

### Option A — Docker Compose (recommended)

```bash
docker compose up --build
```

| Service | URL | Notes |
|---|---|---|
| Backend API | http://localhost:8001 | FastAPI, migrations applied on boot |
| Swagger UI | http://localhost:8001/docs | Interactive API documentation |
| PostgreSQL | `localhost:5433` | user/password/db = `callora`/`callora`/`callora` |
| Redis | `localhost:6380` | rate limiting and cache |
| Worker | — | `python -m app.modules.voice.jobs.worker` |

### Option B — Local virtualenv

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt

export CALLORA_DATABASE_URL="sqlite:///./callora.db"   # or a Postgres URL
export CALLORA_API_KEYS="biz_001:dev-key-biz-001,biz_002:dev-key-biz-002"
export CALLORA_REDIS_URL="redis://localhost:6379/0"
export CALLORA_REDIS_ENABLED="false"                   # optional in dev
export CALLORA_VOICE_ENABLE_OUTBOUND_CALLS="false"    # never call a human by accident

alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Secrets (the Bolna API key, the Groq key) go in **`<repo>/.env`** — the
file next to `docker-compose.yml`. The backend resolves it from the
package location, not from the current working directory, so it works
whether you start the app from the repository root or from `backend/`.
Shell variables always win over the file, and the test suite ignores the
file entirely (`CALLORA_IGNORE_DOTENV=1`) so your local secrets can never
leak into a test run.

In a second terminal, run the background worker:

```bash
cd backend
python -m app.modules.voice.jobs.worker
```

### Option C — Without Redis and without an LLM

Both are optional and the backend degrades explicitly:

* `CALLORA_REDIS_ENABLED=false` → the rate limiter becomes a no-op.
* `CALLORA_VOICE_LLM_PROVIDER=null` → the post-call workflow produces an
  outcome from the provider payload (`engine = "provider-fallback"`) instead
  of calling a model.

### Smoke test (no provider, no call)

No Bolna account is needed for this: a simulated call never reaches the
provider, so the `provider_ready` check is skipped.

```bash
curl -s http://localhost:8001/health
# {"status":"healthy"}

curl -s -X POST http://localhost:8001/api/v1/voice/calls \
     -H "X-API-Key: dev-key-biz-001" \
     -H "Content-Type: application/json" \
     -d '{"lead_id":"lead_xxx","dry_run":true}'
# 201, "simulated": true — no provider was contacted
```

(The request still needs a lead with a phone number and a voice consent
record, otherwise the other checks of the gate block it.)

---

## 3. Configuration reference

All variables use the `CALLORA_` prefix and are read by
`backend/app/config.py` (`Settings`). Everything below has a working
default; nothing is required to boot the service.

### Core

| Variable | Default | Meaning |
|---|---|---|
| `CALLORA_DATABASE_URL` | `postgresql+psycopg2://callora:callora@localhost:5432/callora` | SQLAlchemy URL. SQLite works for local development. |
| `CALLORA_API_KEYS` | `biz_001:dev-key-biz-001` | `business_id:api_key` pairs, comma separated. Resolves the `X-API-Key` header. |
| `CALLORA_LOG_LEVEL` | `INFO` | Root log level. |
| `CALLORA_DEBUG` | `false` | `true` re-raises unhandled exceptions instead of masking them as a 500. |
| `CALLORA_ENVIRONMENT` | `production` | Deployment environment marker. Only `local`, `development`, `dev`, `test` and `testing` unlock the development-only overrides below; every other value (including a typo) is treated as production. |

### Voice provider — Bolna

| Variable | Default | Meaning |
|---|---|---|
| `CALLORA_BOLNA_API_KEY` | *(unset)* | Bolna API key. Without it every call that would actually be **placed** is blocked. A simulated call (dry run, or outbound calling disabled) skips the check — the provider is never contacted, so it has no credentials to check. |
| `CALLORA_BOLNA_BASE_URL` | `https://api.bolna.ai` | API base URL. |
| `CALLORA_BOLNA_TIMEOUT_SECONDS` | `20.0` | Per-request timeout for provider calls. |
| `CALLORA_BOLNA_WEBHOOK_SECRET` | *(unset)* | Enables HMAC-SHA256 verification of webhook signatures. See [Webhook signature](#54-webhook-signature). |
| `CALLORA_BOLNA_DEFAULT_AGENT_ID` | *(unset)* | Fallback Bolna agent when a business has none. |
| `CALLORA_BOLNA_DEFAULT_FROM_NUMBER` | *(unset)* | Optional fallback caller id in E.164. When it is unset (or empty) the caller-number field is left out of the Bolna request and Bolna places the call through the telephony provider of the agent. |
| `CALLORA_VOICE_PROVIDER_MAX_ATTEMPTS` | `3` | Attempts when the provider is *unreachable*. |
| `CALLORA_VOICE_PROVIDER_RETRY_BACKOFF_SECONDS` | `1.0` | Linear backoff between those attempts. |

### Outbound calling policy (deployment defaults)

These are the defaults a business inherits on first read of
`GET /api/v1/voice/business`; every one of them is then per-business and
editable through `PATCH /api/v1/voice/business`.

| Variable | Default | Meaning |
|---|---|---|
| `CALLORA_VOICE_ENABLE_OUTBOUND_CALLS` | `false` | **Master switch.** While false, allowed calls are recorded as `simulated` and no provider is contacted. |
| `CALLORA_VOICE_DEFAULT_TIMEZONE` | `Asia/Kolkata` | Timezone the calling window is evaluated in. |
| `CALLORA_VOICE_DEFAULT_CALLING_WINDOW_START` | `10` | First hour (local) a call may be placed. |
| `CALLORA_VOICE_DEFAULT_CALLING_WINDOW_END` | `19` | Hour (local) the window closes. `start == end` means "no restriction". |
| `CALLORA_VOICE_MAX_CALLS_PER_LEAD_PER_DAY` | `1` | Per-lead daily quota. |
| `CALLORA_VOICE_MAX_CALLS_PER_BUSINESS_PER_DAY` | `500` | Per-business daily quota. |
| `CALLORA_DISABLE_CALL_QUOTAS` | `false` | **Development only.** Skips the two daily quotas above. Honoured only when `CALLORA_ENVIRONMENT` names a development environment; everywhere else the flag is ignored (and logged) and the quotas stay enforced. See [Development-only: skipping the daily quotas](#development-only-skipping-the-daily-quotas). |
| `CALLORA_VOICE_MAX_CONCURRENT_CALLS` | `5` | Calls allowed in flight at the same time. |
| `CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_SECONDS` | `3600` | Minimum gap between two calls to the same lead, in seconds. The default is 60 minutes; a local development deployment can lower it (e.g. `60`). `0` disables it. The in-flight guard is never affected by this value. |
| `CALLORA_VOICE_SUPPORTED_LANGUAGES` | `en,hi,kn` | Languages the deployment is configured for. |
| `CALLORA_VOICE_RATE_LIMIT_PER_MINUTE` | `60` | Per-business call-initiation limit (Redis counter). |

### Call status reconciliation

The provider webhook is the fast path; the reconciliation sweep is the safety
net for the webhooks that never arrive (the Bolna agent without a
`webhook_url`, a dropped request, a rejected payload). See
[Call status synchronization](#55-call-status-synchronization).

| Variable | Default | Meaning |
|---|---|---|
| `CALLORA_VOICE_STATUS_POLL_INTERVAL_SECONDS` | `60` | Minimum age of the last provider check before a call is checked again. |
| `CALLORA_VOICE_STATUS_SYNC_INTERVAL_SECONDS` | `60` | How often the worker runs the sweep. `0` disables it (the operator endpoint still works). |
| `CALLORA_VOICE_STATUS_SYNC_MAX_CALLS_PER_SWEEP` | `50` | How many calls are checked in one sweep. |
| `CALLORA_VOICE_SCHEDULED_CALL_TIMEOUT_SECONDS` | `900` | How long a call may stay in a pre-dial state (`pending` / `scheduled` / `queued`) before the timeout policy applies. The provider is always asked first. |
| `CALLORA_VOICE_STUCK_CALL_TIMEOUT_ACTION` | `fail` | `fail` closes a stuck pre-dial call as `failed`; `keep_polling` only reports it. A call in `initiated`, `ringing` or `in_progress` is never closed by the timeout. |

### Redis (accelerator)

| Variable | Default | Meaning |
|---|---|---|
| `CALLORA_REDIS_ENABLED` | `true` | Set `false` to run without Redis (the rate limiter becomes a no-op). |
| `CALLORA_REDIS_URL` | `redis://localhost:6379/0` | Connection URL. |
| `CALLORA_REDIS_KEY_PREFIX` | `callora` | Prefix of every key the module writes. |
| `CALLORA_REDIS_SOCKET_TIMEOUT_SECONDS` | `1.0` | Connect/socket timeout; keeps a dead Redis from stalling requests. |
| `CALLORA_VOICE_CACHE_TTL_SECONDS` | `300` | TTL of cached lookups. |

### Post-call AI workflow (LangGraph)

| Variable | Default | Meaning |
|---|---|---|
| `CALLORA_VOICE_LLM_PROVIDER` | `groq` | `groq`, `langchain`, or `null` (no model). |
| `CALLORA_VOICE_LLM_MODEL` | *(unset)* | Model id; falls back to `CALLORA_GROQ_MODEL`. |
| `CALLORA_VOICE_LLM_TEMPERATURE` | `0.0` | Sampling temperature for the analysis. |
| `CALLORA_VOICE_LLM_TIMEOUT_SECONDS` | `60.0` | Per-model timeout. |
| `CALLORA_VOICE_TRANSCRIPT_MAX_CHARS` | `40000` | Transcript size handed to the model. |
| `GROQ_API_KEY` | *(unset)* | Key for the Groq client (also accepted as `CALLORA_GROQ_API_KEY`). |
| `CALLORA_GROQ_MODEL` | `openai/gpt-oss-120b` | Default model for the Groq client. |
| `CALLORA_VOICE_JOB_MAX_ATTEMPTS` | `5` | Attempts before a job is parked as `dead`. |
| `CALLORA_VOICE_JOB_RETRY_BACKOFF_SECONDS` | `10.0` | Base of the exponential retry backoff. |

---

## 4. The compliance gate

`POST /api/v1/voice/calls` is the **only** way to place a call. It always
runs the gate first, and there is no flag, query parameter or admin
endpoint that skips it.

### The nine mandatory checks

| # | Check | Blocks when |
|---|---|---|
| 1 | `business_active` | The business configuration is missing or deactivated. |
| 2 | `lead_exists` | The lead does not belong to the business, or has no usable E.164 number. |
| 3 | `consent` | No voice consent record, or it is revoked/expired. Skipped when the business sets `require_consent = false`. |
| 4 | `dnd` | The number is on the local DND registry, the lead is flagged `do_not_call`, or an external DND source reports it. |
| 5 | `calling_hours` | The local time is outside the permitted window, or the weekday is not allowed. |
| 6 | `duplicate_call` | Another call to this lead is in flight (Bolna still reports it as queued, ringing or active), or the lead was called less than the cooldown ago. |
| 7 | `quota` | The per-lead or per-business daily quota is exhausted. **Skipped in a development environment** that explicitly disables the daily quotas — nothing else is. |
| 8 | `concurrency` | The business already has `max_concurrent_calls` calls in flight. |
| 9 | `provider_ready` | No agent id is configured, or the provider credentials are missing. **Skipped for a simulated call** — the provider is never contacted, so it has no credentials to check. |

**Fail closed.** Every check is wrapped: if it raises, the check is recorded
as `error` with the reason code `check_unavailable` and the call is
blocked. A check that cannot answer never answers "yes".

### Simulated calls and `provider_ready`

A call is *simulated* when `dry_run: true` is sent, or when
`CALLORA_VOICE_ENABLE_OUTBOUND_CALLS` is `false`. In that mode the provider
is never contacted, so `provider_ready` is reported as `skipped`
(non-blocking) instead of failing:

```json
{"name": "provider_ready", "outcome": "skipped", "mandatory": false,
 "reason": "the call will not be placed with the provider",
 "details": {"provider": "bolna", "simulated": true}}
```

This is what lets a developer exercise the whole flow — gate, call record,
webhooks, post-call analysis — from Swagger or curl **without a Bolna
account**. The moment a call would really be placed the check is mandatory
again: with outbound calling enabled and no credentials the request is
still rejected with `403 compliance_blocked` /
`provider_not_configured`, and no provider request is ever made.

### Development-only: skipping the daily quotas

The per-lead daily quota defaults to **one** call, so a second test call to
the same lead on the same day is refused with
`daily_lead_quota_exceeded`. A local deployment can skip the two daily
quotas (per lead and per business) while it exercises an integration:

```bash
# .env — local development only
CALLORA_ENVIRONMENT=local
CALLORA_DISABLE_CALL_QUOTAS=true
```

Both variables are required, and the pair is refused outside a
development environment:

| `CALLORA_ENVIRONMENT` | `CALLORA_DISABLE_CALL_QUOTAS` | Result |
|---|---|---|
| `local`, `development`, `dev`, `test`, `testing` | `true` | The two daily quotas are skipped. |
| `local`, `development`, `dev`, `test`, `testing` | `false` | The quotas are enforced. |
| `production` (or any other/unknown value) | `true` | The quotas are enforced; the override is refused and logged as `call_quota_override_refused`. |

**Only the two daily quotas are skipped.** Every other check of the gate
still runs and still blocks the call: the business must be active, the lead
must have a valid number and a granted voice consent, the number must not
be on a do-not-disturb list, the call must be inside the permitted calling
window, another call to the same lead must not be in flight, the
concurrency cap must not be reached, and the provider must be ready. The
decision records the bypass, so it stays auditable:

```json
{"name": "quota", "outcome": "skipped", "mandatory": false,
 "reason": "the daily call quotas are disabled for this development environment",
 "details": {"disabled": true, "environment": "local",
             "per_lead": 1, "per_business": 500}}
```

`GET /api/v1/voice/business` reports the state of the override as
`call_quotas_disabled`.

**The other call-frequency rules are not affected** and can still refuse a
repeated test call. Relax them explicitly (and only locally) when you need
to:

| Rule | Setting | Default |
|---|---|---|
| Minimum gap between two calls to the same lead | `CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_SECONDS` | `3600` (60 minutes) (`0` disables it) |
| Calls in flight per business | `max_concurrent_calls` (per business) | `5` |
| Call initiations per minute | `CALLORA_VOICE_RATE_LIMIT_PER_MINUTE` | `60` |
| Permitted calling window | `calling_window_start` / `calling_window_end` (per business) | `10`–`19` local |

### The duplicate-call cooldown and the in-flight guard

The `duplicate_call` check is **two independent rules**, and both are
mandatory:

1. **The in-flight guard.** While Bolna still reports the previous call as
   queued, ringing or active, a second call to the same lead is refused
   (`duplicate_call_in_flight`). It is evaluated first and is never
   weakened by the cooldown below — not even when the cooldown is `0`.
2. **The cooldown.** After the previous call reached a terminal state
   (completed, failed, cancelled, …), the lead is protected for a
   configurable gap (`duplicate_call_cooldown`). The gap is measured in
   **seconds**, so a local deployment can make a repeated test call
   possible without waiting for an hour:

```bash
# .env — local development only
CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_SECONDS=60
```

| Setting | Value | Effect |
|---|---|---|
| `CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_SECONDS` | unset | The production default of 3600 seconds (60 minutes). |
| `CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_SECONDS` | `60` | A second call to the same lead is allowed 60 seconds after the previous one. |
| `CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_SECONDS` | `0` | The cooldown is disabled. The in-flight guard still blocks. |

The deprecated `CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_MINUTES` is still
honoured when the seconds variable is not set, and it keeps its unit: a
legacy `60` still means 60 minutes, never 60 seconds. When both are set,
the seconds variable wins.

### What a decision produces

* A row in `compliance_decisions` with the full per-check breakdown, the
  blocked reasons and the `policy_version`.
* An entry in `audit_logs` (`call.requested`, and `call.blocked` when the
  gate refused).
* A row in `calls` with `status = "blocked"` and a `blocked_reason`. A
  blocked call **never** reaches the provider.
* For an allowed call, the HTTP response carries the decision id and every
  check result so the caller can see exactly what was evaluated.

### Idempotency

Send `Idempotency-Key: <key>` on `POST /api/v1/voice/calls`:

* the same key with the same body replays the original call (no second
  call is placed);
* the same key with a **different** body is rejected with
  `409 idempotency_conflict`.

The key is stored in the shared `idempotency_keys` table under the scope
`voice.call`, together with a SHA-256 hash of the request body.

---

## 5. API reference

Base path: `/api/v1/voice`. All endpoints except the two webhooks require
`X-API-Key` and are scoped to that business — another tenant gets `404`,
never another tenant's data.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/business` | Read the voice configuration (created on first read). |
| `PATCH` | `/business` | Change the voice configuration (audited). |
| `GET` | `/leads` | List the leads with their voice state. |
| `GET` | `/leads/{lead_id}` | Read the voice state of one lead. |
| `PUT` | `/leads/{lead_id}/profile` | Change the voice state (`do_not_call`, language, notes). |
| `GET` | `/leads/{lead_id}/consent` | Read every consent record. |
| `PUT` | `/leads/{lead_id}/consent` | Record the consent evidence. |
| `GET` | `/dnd` | List the do-not-disturb registry. |
| `POST` | `/dnd` | Add a number to the registry. |
| `DELETE` | `/dnd/{entry_id}` | Deactivate an entry (never deleted — the history stays auditable). |
| `POST` | `/calls` | **Request an outbound call.** Runs the compliance gate. |
| `GET` | `/calls` | List the calls of the business. |
| `GET` | `/calls/{call_id}` | Read one call. |
| `GET` | `/calls/{call_id}/events` | Read the provider events that were applied. |
| `GET` | `/calls/{call_id}/outcome` | Read the structured post-call outcome. |
| `POST` | `/calls/{call_id}/refresh` | Re-read the state from the provider (recovery path for a missed webhook). |
| `POST` | `/calls/{call_id}/cancel` | Cancel a call that has not started yet. |
| `POST` | `/calls/{call_id}/postcall` | Re-queue the post-call workflow. |
| `GET` | `/handoffs` | List the handoffs. |
| `GET` | `/handoffs/{handoff_id}` | Read one handoff. |
| `POST` | `/handoffs` | Ask a salesperson to call a lead back. |
| `PATCH` | `/handoffs/{handoff_id}` | Update the status / outcome of a handoff. |
| `GET` | `/ops/stats` | Summary of the voice activity. |
| `GET` | `/ops/jobs` | List the background jobs. |
| `GET` | `/ops/jobs/{job_id}` | Read one background job. |
| `POST` | `/ops/jobs/{job_id}/retry` | Re-queue a dead job (audited). |
| `GET` | `/ops/audit` | Read the audit trail. |
| `GET` | `/ops/compliance-decisions` | Read the persisted compliance decisions. |
| `POST` | `/ops/sync-calls` | Reconcile the calls with the provider (manual version of the background sweep). |
| `POST` | `/webhooks/bolna/execution` | **Provider webhook** — call lifecycle events. |
| `POST` | `/webhooks/bolna/pre-transfer` | **Provider webhook** — warm-transfer events. |
| `POST` | `/knowledge` | Create a verified business knowledge item (FAQs, pricing, features, policy). |
| `GET` | `/knowledge` | List verified business knowledge items (filtered by category, tags, is_active). |
| `POST` | `/knowledge/batch` | Bulk insert verified knowledge items. |
| `GET` | `/knowledge/{item_id}` | Read a specific knowledge item. |
| `PUT` | `/knowledge/{item_id}` | Update an existing knowledge item (audited). |
| `DELETE` | `/knowledge/{item_id}` | Delete a knowledge item. |
| `POST` | `/answer-question` | **Live Follow-Up Question Answering.** Grounded answer generation with conversation memory, AI guardrails, and qualification flow continuation. |
| `GET` | `/bolna/tool-definition` | Tool specification schema for Bolna custom tool integration. |

30 paths, 39 operations. The full, always-current contract is the OpenAPI
document at `/openapi.json` (`/docs` renders it).

### 5.1 Requesting a call

```http
POST /api/v1/voice/calls
X-API-Key: dev-key-biz-001
Idempotency-Key: order-4711-rahul
Content-Type: application/json

{
  "lead_id": "lead_voice_0001",
  "language": "hi",
  "scheduled_at": null,
  "extra_context": {"campaign": "monsoon-2026"},
  "dry_run": false
}
```

`201 Created`:

```json
{
  "call": {
    "id": "call_9f2c...",
    "business_id": "biz_001",
    "lead_id": "lead_voice_0001",
    "provider": "bolna",
    "status": "simulated",
    "to_number": "+919876543210",
    "language": "hi",
    "simulated": true,
    "compliance_decision_id": "cd_41a0...",
    "metadata": {"simulated": true, "reason": "outbound calling is disabled for this deployment"}
  },
  "compliance_decision_id": "cd_41a0...",
  "compliance_decision": "allowed",
  "compliance_checks": [
    {"name": "consent", "outcome": "passed", "mandatory": true, "reason": "voice consent is on record"}
  ],
  "simulated": true,
  "message": "the call was recorded as simulated; no provider was contacted"
}
```

`403 Forbidden` — the gate refused:

```json
{
  "error": {
    "code": "compliance_blocked",
    "message": "the call was blocked by the compliance gate",
    "details": [
      {"check": "consent", "reason_code": "consent_missing",
       "reason": "no voice consent record exists for this lead", "mandatory": true}
    ]
  }
}
```

Other status codes: `401` (missing/invalid API key), `404` (unknown lead or
call), `409` (`idempotency_conflict`), `422` (validation), `429`
(`rate_limit_exceeded`), `503`/`502`/`504` (provider unreachable / error /
timeout).

The caller number is optional: send `"from_number"` to call from a
specific number, otherwise the number of the business
(`bolna_from_number`) or the deployment default is used — and when none
of them is set, Bolna places the call itself (no caller number is sent).

### 5.2 The webhook endpoints

The webhook endpoints are **not** protected by `X-API-Key` — the caller is
Bolna, not a tenant.

* The body is parsed and stored **before** the 200 response, so a crash
  after the acknowledgement can never lose data.
* The event is deduplicated by a SHA-256 fingerprint of
  `(provider, execution_id, status, payload)` with a unique constraint on
  `(provider, event_key)`. A redelivery is acknowledged with
  `{"status": "duplicate"}` — the provider must not retry it.
* A call never moves backwards. A late `initiated` event on a `completed`
  call is stored but ignored (`{"status": "ignored_out_of_order"}`).
* A status Callora does not know never terminates a call. It says nothing
  about whether the call is over, so the call keeps its status, keeps the
  duplicate-call guard and queues no post-call work. The status is stored
  and the reconciliation sweep resolves it by polling the provider.
* An event that matches no known call is stored and acknowledged with
  `{"status": "unmatched"}`.
* A terminal status queues the post-call job (never runs the analysis
  inline).
* Live customer follow-up questions and custom tool webhooks are received at
  `POST /api/v1/voice/webhooks/bolna/followup`. They resolve the call via
  `execution_id` or `call_id`, query verified PostgreSQL business knowledge,
  evaluate AI guardrails, update conversation memory, and return telephone-ready
  speech answers without requiring tenant `X-API-Key` headers.

### 5.3 Webhook signature

Bolna does not document an HMAC scheme for execution webhooks. Set
`CALLORA_BOLNA_WEBHOOK_SECRET` to enable verification: the raw body is
signed with HMAC-SHA256 and compared against the value of the first
header found among `x-bolna-signature`, `x-webhook-signature`,
`x-hub-signature-256`, `x-signature` (an optional `sha256=` prefix is
accepted).

* secret set, signature missing or wrong → `401 webhook_signature_invalid`,
  the event is not stored, and the rejection is audited.
* secret set, signature valid → processed, `signature_valid: true`.
* secret unset → the provider cannot be verified cryptographically; the
  event is processed and `signature_valid` is `null`. Restrict the endpoint
  at the network layer in that case.

### 5.4 The operator endpoints

`GET /api/v1/voice/ops/stats` answers "what happened, what was allowed and
what is stuck" without a database client:

```json
{
  "business_id": "biz_001",
  "calls_total": 128,
  "calls_by_status": {"completed": 61, "no_answer": 40, "blocked": 19, "simulated": 8},
  "calls_today": 12,
  "blocked_today": 3,
  "handoffs_open": 4,
  "consent_granted": 96,
  "consent_missing": 5,
  "dnd_entries": 2,
  "jobs_pending": 1,
  "jobs_failed": 0,
  "total_cost": 47.32,
  "currency": "INR"
}
```

`POST /api/v1/voice/ops/sync-calls` reconciles the calls of the business with
the provider — the manual version of the background sweep (see
[Call status synchronization](#55-call-status-synchronization)):

```json
{
  "business_id": "biz_001",
  "checked": 3,
  "applied": 1,
  "unchanged": 2,
  "skipped": 0,
  "failed": 0,
  "outcomes": [
    {
      "call_id": "call_6144f2be5cc64f46",
      "execution_id": "a6a1e2e5-3620-426d-9f54-5cad8f807509",
      "action": "applied",
      "previous_status": "scheduled",
      "status": "initiated",
      "provider_status": "initiated",
      "detail": "the call status is now 'initiated'"
    }
  ]
}
```

The `action` of an outcome is one of `applied` (the provider moved the call
forward), `unchanged` (the provider agrees with Callora), `out_of_order` (the
provider reported an older status), `unknown_provider_status` (the provider
reported a status Callora does not know — the call was left alone),
`provider_unreachable` / `provider_error` (the provider could not be asked —
the call was left alone), `execution_missing` (the provider does not know the
execution any more), `timeout` (the timeout policy closed the call),
`not_placed` (the call was never handed to the provider) or `not_due` (the
call is scheduled for later).

### 5.5 Call status synchronization

A call has to end, even when the provider never tells Callora how it ended.
The synchronization has two paths and one set of rules.

**The fast path — the provider webhook.** Bolna pushes an execution event to
`/api/v1/voice/webhooks/bolna/execution` whenever the status of a call
changes. This is the normal path; see
[the webhook endpoints](#52-the-webhook-endpoints).

> **The Bolna agent needs a webhook URL.** Without
> `webhook_url` on the agent, Bolna never pushes an event, and the call only
> moves through the sweep below. Set it to
> `https://<your-host>/api/v1/voice/webhooks/bolna/execution`.

**The safety net — the reconciliation sweep.** The worker polls the provider
for the calls it has not heard about. A call is checked when it is *due* (a
scheduled call from its `scheduled_at`, every other call from the moment the
provider accepted it) and at most once per
`CALLORA_VOICE_STATUS_POLL_INTERVAL_SECONDS`. The sweep runs every
`CALLORA_VOICE_STATUS_SYNC_INTERVAL_SECONDS` and checks at most
`CALLORA_VOICE_STATUS_SYNC_MAX_CALLS_PER_SWEEP` calls.

Both paths use the same state machine (`voice/lifecycle.py`), so they cannot
disagree:

* a call never moves backwards — the statuses are ranked, and an older event
  is ignored;
* a terminal status is never overwritten by a later event;
* an unknown provider status never terminates a call;
* a terminal status ends the call (`ended_at`), queues the post-call job
  and releases the duplicate-call guard and the concurrency slot.

**The timeout policy.** A call that never got past the dial queue
(`pending`, `scheduled`, `queued`) and that the provider *still* reports as
pre-dial more than `CALLORA_VOICE_SCHEDULED_CALL_TIMEOUT_SECONDS` after it
was due is closed as `failed` (`CALLORA_VOICE_STUCK_CALL_TIMEOUT_ACTION=fail`,
the default). The provider is always asked first: the call is only closed
when the provider confirms that it is still waiting. Set the action to
`keep_polling` to report the stuck call without closing it.

A call that is talking to somebody is **never** closed because time passed.
`initiated`, `ringing` and `in_progress` are polled, but only the provider
can end them. A provider that cannot be reached leaves every call untouched.

Every transition is logged with both the Callora call id and the provider
execution id (`call_id`, `provider_call_id`, `execution_id`), and audited
(`call.status_updated`, `call.status_synced`, `call.timeout`).

### 5.6 Follow-up Q&A and Business Knowledge Base

Outbound voice sales calls frequently encounter follow-up customer inquiries (e.g., pricing, features, eligibility, return policies, competitor comparisons) rather than adhering solely to predefined qualification scripts. The Knowledge Base provides verified, business-scoped factual storage in PostgreSQL, while the Follow-Up Question API provides retrieval-grounded answering.

#### Knowledge Item Categories
- `pricing`: Rate cards, tiers, billing cycles, discounts, payment options.
- `product_feature`: Capabilities, integrations, supported languages, mobile apps, APIs.
- `policy`: Refund, cancellation, SLA, uptime, privacy, warranties.
- `eligibility`: Prerequisites, minimum team size, technical requirements, rooftop standards.
- `objection_handling`: Value propositions, competitor differentiators, cost comparisons.
- `escalation_rule`: Criteria for live warm transfers and callbacks.

#### Pan-India Knowledge Base Coverage
Callora includes a comprehensive pan-India knowledge base out of the box (`backend/scripts/seed_knowledge.py`), verified for real estate enterprises (such as UrbanNest Realty) and pan-India sales organizations:
- **South India Hubs**: Bengaluru (Whitefield, Sarjapur, Electronic City, Manyata, Devanahalli), Hyderabad (HITEC City, Gachibowli, Financial District, Jubilee Hills), Chennai (OMR, Anna Nagar, ECR), Kochi (Marine Drive, Kakkanad InfoPark).
- **West India Hubs**: Mumbai MMR (Worli, Bandra West, Andheri, Powai, Thane, Navi Mumbai), Pune (Hinjawadi IT Park, Wakad, Baner, Kharadi), Ahmedabad & GIFT City Gandhinagar.
- **North India Hubs**: Delhi NCR (Gurugram Golf Course Ext & Dwarka Expressway, Noida Sector 62 & 150), Chandigarh Tri-City (Mohali IT City, Panchkula), Jaipur (Jagatpura, Vaishali Nagar), Lucknow (Gomti Nagar Extension, Shaheed Path).
- **East & Central India Hubs**: Kolkata (New Town Rajarhat, Salt Lake Sector V, EM Bypass), Bhubaneswar (Infocity, Patia), Indore (Super Corridor, Vijay Nagar).
- **Commercial & Co-working Corridors**: Grade-A commercial office suites across BKC Mumbai, Cyber City Gurugram, Outer Ring Road Bengaluru, and HITEC City Hyderabad.
- **Pan-India Banking & Home Loans**: Pre-approvals across SBI, HDFC Bank, ICICI Bank, Axis Bank (interest rates from 8.35% p.a., up to 90% financing) and PMAY subsidy guidance (up to ₹2.67 Lakhs).
- **Pan-India RERA & Legal Approvals**: 100% state RERA registration across MahaRERA, K-RERA, UP-RERA, HRERA, TS-RERA, TNRERA, and WBRERA with escrow protections and state stamp duty guidelines.
- **Pan-India NRI Investment Desk**: FEMA/RBI compliant advisory, NRE/NRO accounts, Power of Attorney documentation, and 360-degree virtual property tours for Gulf/US/UK/European buyers.
- **Customer Amenities & Transport**: Complimentary chauffeur-driven site visit pickups and drop-offs across all major metro corridors.
- **Multilingual Regional Voice Support**: Conversational voice AI code-switching in English, Hindi, Kannada, Tamil, Telugu, Marathi, and Bengali.
- **Telecom & Calling Window Compliance**: Strict adherence to TRAI 9:00 AM – 8:00 PM IST calling hours, National DND registry checks, and instant verbal opt-out processing.

#### Follow-Up Answering API: `POST /api/v1/voice/answer-question`

Validates business and call context, performs tenant-isolated knowledge retrieval, evaluates AI guardrails, updates conversation memory, and returns grounded spoken telephone answers.

**Request:**
```http
POST /api/v1/voice/answer-question
X-API-Key: dev-key-biz-001
Content-Type: application/json

{
  "business_id": "biz_001",
  "call_id": "call_9f2c...",
  "question": "How much does Callora cost per month?",
  "language": "en",
  "context": {
    "qualification_state": {"team_size": "10"}
  }
}
```

**Response (`200 OK`):**
```json
{
  "answer": "Callora offers three pricing plans. Starter is ₹5,000 per month for up to 5 users. Professional is ₹12,000 per month for up to 20 users with automated workflows and CRM sync. To see if this is the best fit for your team, may I ask: What is your monthly budget?",
  "source_references": ["Callora Pricing Guide 2026"],
  "should_handoff": false,
  "handoff_recommended": false,
  "handoff_reason": null,
  "handoff_type": null,
  "handoff_id": null,
  "action": "answer",
  "confidence": 1.0,
  "qualification_continuation": "To see if this is the best fit for your team, may I ask: What is your monthly budget?",
  "memory_state": {
    "call_id": "call_9f2c...",
    "business_id": "biz_001",
    "turns_count": 1,
    "questions_answered_count": 1,
    "qualification_fields": {"team_size": "10"},
    "pending_qualification_keys": ["budget"],
    "handoff_status": "none",
    "opt_out_detected": false,
    "customer_intent": "inquire_business_knowledge"
  }
}
```

### 5.7 Bolna Live Custom Tool Integration

Bolna supports runtime API / custom tool invocation where the LLM agent can call an external webhook when the customer asks a question outside the core prompt.

#### Tool Definition Endpoint: `GET /api/v1/voice/bolna/tool-definition`
Returns an OpenAI-compatible function definition and Bolna tool configuration that can be attached to Bolna agents:

```json
{
  "tool_name": "answer_customer_question",
  "description": "Answers customer questions regarding business pricing, product features, eligibility, policies, and objection handling during a live call.",
  "parameters_schema": {
    "type": "object",
    "properties": {
      "question": {
        "type": "string",
        "description": "The exact question asked by the customer."
      },
      "language": {
        "type": "string",
        "description": "The language of the conversation, e.g. en, hi, kn."
      }
    },
    "required": ["question"]
  },
  "api_config": {
    "url": "https://api.callora.ai/api/v1/voice/answer-question",
    "method": "POST",
    "headers": {
      "Content-Type": "application/json",
      "X-API-Key": "{CALLORA_API_KEY}"
    },
    "static_parameters": {
      "business_id": "{business_id}",
      "call_id": "{call_id}"
    }
  }
}
```

During call initiation, Callora's `CallOrchestrator` automatically injects `call_id` and `business_id` into Bolna's `user_data` payload. When Bolna invokes the tool, these parameters are bound into the request, ensuring seamless and authenticated business context validation.

### 5.8 Conversation Memory & AI Guardrails

#### Conversation Memory (`VoiceCallMemory`)
Tracks live state in PostgreSQL:
- **`turns_count`**: Number of conversational exchanges.
- **`questions_answered`**: Complete list of questions, answers, source references, and actions taken during the call.
- **`qualification_fields`**: Key-value pairs extracted during qualification (e.g. `team_size`, `budget`).
- **`pending_qualification_keys`**: Qualification keys remaining to be asked.
- **`handoff_status`**: `none`, `requested`, or `initiated`.
- **`opt_out_detected`**: Flagged immediately when a customer requests DND or removal.

#### AI Guardrails (`AIGuardrails`)
- **Anti-Hallucination Grounding**: Answers are strictly grounded in verified facts. Grounding verification ensures no unauthorized discounts, prices, or terms are invented.
- **Ambiguity Detection**: Questions lacking necessary context (e.g., *"Can you do better on the deal?"*) trigger natural clarification prompts instead of assumptions.
- **Missing / Unsupported Knowledge Fallback**: Questions outside verified knowledge trigger graceful fallback acknowledgment and offer a warm transfer or priority callback.
- **Immediate Opt-Out / Refusal**: Requests such as *"Stop calling me"*, *"Remove my number"*, or *"I am on DND"* immediately register business DND, flag `do_not_call: true`, and terminate outreach.
- **Voice Speech Formatting**: Strips markdown, asterisks, bullets, headers, and URLs, and formats responses concisely (1–3 sentences) for natural telephone text-to-speech synthesis.

### 5.9 Bolna Agent Studio Custom Functions

Six live, mid-call tools the voice agent (Tanu) can invoke during a conversation.
They **extend** the platform — every one of them is implemented on the existing
Callora components (canonical leads, the voice repository, the compliance gate,
handoffs, the job queue, the CRM sync service). None of them duplicates an
existing module, and none lets the language model issue an arbitrary database
update.

#### Endpoint definitions: `GET /api/v1/voice/bolna/custom-functions`

Returns the ready-to-paste Bolna Agent Studio definitions of all six functions
(OpenAI-style function schema + `key: "custom_task"` + `value.{method,url,param,api_token,headers}`
with `%(param)s` specifiers). Bolna POSTs the collected parameters as a JSON
body and hands the raw JSON response back to the LLM. The `api_token` value in
the definitions is a **placeholder** — the real secret is never served over HTTP.

#### Authentication

* `Authorization: Bearer <token>` (Bolna's `value.api_token` arrives as this
  header). A token matching **`CALLORA_VOICE_TOOL_SECRET`** identifies the Bolna
  runtime; a token matching a **business API key** (`CALLORA_API_KEYS`) resolves
  to that business directly and additionally unlocks `force` on opportunity
  creation.
* With the shared tool secret, `business_id` in the body is required and is
  *verified*, never trusted. A persisted provider execution id resolves the
  stored call, whose business is authoritative — a mismatch is a 403.
* The integration is **off** unless `CALLORA_VOICE_TOOL_SECRET` is set: every
  tool request is rejected with 401 and the application runs normally.

#### Response envelope (every response, success or failure)

```json
{"success": true,  "message": "...", "data": {...}}
{"success": false, "error": {"code": "MACHINE_READABLE", "message": "..."}}
```

No stack traces, SQL text or configuration values are ever included.

#### Common parameters

Every request may carry `execution_id` (Bolna auto-injects) and `call_id`;
`business_id` is required with the shared secret and must match the token.

#### Conversation sequence

```
1. get_lead_details        — start of the conversation
2. (ask the qualification questions)
3. qualify_lead            — once the required answers exist
4. save_call_outcome       — near the end, once the outcome is clear
   (may embed the callback; or:)
   schedule_callback       — when the customer asked for a specific time
5. create_opportunity      — only after qualify_lead returned "qualified"
6. sync_lead_to_crm        — once, at the very end (if a CRM is connected)
```

#### 1. `get_lead_details` — `POST /api/v1/voice/tools/lead-details`

| | |
|---|---|
| Purpose | Retrieve the lead the conversation belongs to: name, preferred language, current status, known qualification answers, previous outcome. |
| Params | `lead_id` (required, string), `execution_id`, `call_id`, `business_id` (required with the tool secret). |
| Tanu invokes | At the start of a conversation, or to confirm what was already settled before asking again. |
| Tanu must not invoke | For lead ids a customer mentioned that belong to somebody else, or repeatedly during the same call. |
| Example response | `{"success": true, "message": "Lead details retrieved", "data": {"lead_id": "lead_voice_0001", "name": "Rahul Sharma", "status": "new", "known_fields": {"budget": "5000000"}}}` |

#### 2. `save_call_outcome` — `POST /api/v1/voice/tools/save-outcome`

| | |
|---|---|
| Purpose | Persist what the conversation established: the outcome, the answers actually given, a short summary and an optional callback request — transactionally and idempotently. |
| Params | `lead_id` (required), `outcome` (required; one of `interested`, `not_interested`, `callback_requested`, `qualified`, `unqualified`, `no_answer`, `busy`, `failed`), `customer_interest`, `qualification_answers` (object — only what the customer actually said), `summary`, `callback_requested`, `callback_time` (ISO-8601; naive times are interpreted in `callback_timezone`), `callback_timezone`, `callback_reason`. |
| Rules enforced | `qualified` only applies when the rule engine agrees (otherwise falls back to `interested`); status never moves backwards (every suppression is audited); a callback that fails consent/DND/calling-hours does not abort the outcome — it is reported inside `data.callback.error`. |
| Tanu invokes | Exactly once, near the end of the conversation, once the outcome is clear. |
| Tanu must not invoke | Speculatively mid-call, repeatedly with different outcomes for the same conversation, or with invented answer values. |
| Example response | `{"success": true, "message": "Lead outcome saved successfully", "data": {"lead_id": "lead_voice_0001", "outcome": "interested", "lead_status": "interested", "lead_status_applied": true}}` |

#### 3. `qualify_lead` — `POST /api/v1/voice/tools/qualify-lead`

| | |
|---|---|
| Purpose | Evaluate the collected answers against the business's configured `qualification_questions` rules and return a deterministic verdict. |
| Params | `lead_id` (required), `answers` (object keyed by question key; omit unanswered questions), `execution_id`, `call_id`, `business_id`. |
| Rule keys (per question) | `min_value` / `max_value` (numeric), `matches_any` (case-insensitive substring), `allowed_values` (exact choice). A required unanswered question yields `insufficient_data` — unknowns are never fabricated. |
| Tanu invokes | Once all required questions are answered, or to check what is still missing. |
| Tanu must not invoke | To force a "qualified" verdict — the engine, not the model, decides. |
| Example response | `{"success": true, "message": "Qualification evaluated: qualified", "data": {"lead_id": "lead_voice_0001", "qualification": {"status": "qualified", "score": 1.0}, "lead_status": "qualified"}}` |

#### 4. `create_opportunity` — `POST /api/v1/voice/tools/create-opportunity`

| | |
|---|---|
| Purpose | Open a sales opportunity (`voice_opportunities`) for the lead, storing the qualification result that justified it. |
| Params | `lead_id` (required), `answers`, `execution_id`, `call_id`, `business_id`, `force` (only honoured for a business API key, never the shared Bolna token). |
| Tanu invokes | Only after `qualify_lead` returned `qualified` **and** the customer confirmed they want to proceed. Repeated calls replay the existing opportunity. |
| Tanu must not invoke | For unqualified or `insufficient_data` leads — the endpoint answers `QUALIFICATION_NOT_MET` and no opportunity is created. |
| Example response | `{"success": true, "message": "Opportunity created successfully", "data": {"lead_id": "lead_voice_0001", "opportunity_id": "vopp_...", "status": "open", "created": true, "lead_status": "opportunity_created"}}` |

#### 5. `schedule_callback` — `POST /api/v1/voice/tools/schedule-callback`

| | |
|---|---|
| Purpose | Record a callback the customer explicitly asked for, at the time and in the timezone they named. Stored in UTC; a naive `requested_at` is interpreted *in* `timezone`, an offset-carrying time is absolute. |
| Params | `lead_id` (required), `requested_at` (required, ISO-8601), `timezone` (required, IANA name), `reason`, `execution_id`, `call_id`, `business_id`. |
| Checks | Consent, DND and the business calling window are evaluated **at the requested moment** (via `evaluate_callback_request`). Violations answer 403 (`CONSENT_NOT_GRANTED` / `DND_RESTRICTED`) or 422 (`callback_outside_calling_hours`, invalid time/timezone). |
| Dispatch | With `CALLORA_VOICE_AUTO_CALLBACK_DISPATCH=true` the callback enqueues a `callback.dispatch` job that re-runs the full compliance gate before dialing. Default off: the handoff stays for the team (`dispatch.mode = "pending_manual"`). |
| Tanu invokes | When the customer wants to continue at a specific later time — repeat the time back to them before confirming. |
| Tanu must not invoke | To promise a callback the customer did not ask for, or for times outside what the customer actually said. |
| Example response | `{"success": true, "message": "Callback scheduled successfully", "data": {"scheduled": true, "callback_id": "ho_...", "requested_at": "2026-10-29T06:30:00+00:00", "timezone": "Asia/Kolkata", "dispatch": {"mode": "pending_manual"}}}` |

#### 6. `sync_lead_to_crm` — `POST /api/v1/voice/tools/crm-sync`

| | |
|---|---|
| Purpose | Push the lead and its latest outcome to the business's connected CRM (reuses `CRMSyncService.sync_lead`). |
| Params | `lead_id` (required), `connection_id` (defaults to `default`), `execution_id`, `call_id`, `business_id`. |
| Honesty | Success is only reported when the external CRM confirmed (`created`/`updated`); with no active connection the endpoint answers 409 `CRM_NOT_CONFIGURED`; a provider failure answers 502 `CRM_SYNC_FAILED` — never a fake success. |
| Tanu invokes | Once, at the very end of a productive conversation (after `save_call_outcome`). |
| Tanu must not invoke | For conversations with no outcome, repeatedly, or when the customer did not consent to the interaction. |
| Example response | `{"success": true, "message": "Lead created in the external CRM.", "data": {"lead_id": "lead_voice_0001", "sync_id": "...", "status": "created", "external_crm_id": "CRM-TEST-001", "connection_id": "default"}}` |

#### Live outcome vs final call status (webhook reconciliation)

The post-call workflow preserves a **live** outcome recorded by the custom
functions: fields the live conversation established (`disposition`, `summary`,
qualification data — marked `field_confidence.source = "live_conversation"`)
are never overwritten by the later LLM pass. A disagreement is audited with
action `outcome.conflict` and the verified live outcome wins.

#### Deployment notes

* The endpoints must be reachable by Bolna over **HTTPS** with a valid
  certificate; the URLs are derived from the deployment's public base URL.
* Set `value.api_token` in each Bolna tool configuration to
  `Bearer <CALLORA_VOICE_TOOL_SECRET>`; fill `%(business_id)s` with the
  business's literal Callora business id (Bolna does not inject it).
* Rate limiting defaults to 120 requests/minute per business (accelerator
  only; an unreachable Redis never blocks a request).

---

## 6. The post-call workflow

The workflow is a bounded, linear LangGraph. Every node does a fixed amount
of work and the graph never loops, so one call can never cost more than a
handful of model calls.

```
load_context ─┬─► extract_qualification ─► classify_refusal ─► summarize ─┬─► build_handoff ─► persist
               │                                                            └─► persist
               └─(no transcript)────────────────────────────────────────► summarize
```

| Node | Produces |
|---|---|
| `load_context` | The business, the lead, the transcript (truncated), the provider's own extracted data. |
| `extract_qualification` | The answers to the business' qualification questions, a `qualified` flag, a score and the missing questions. |
| `classify_refusal` | Whether the lead refused, the refusal category (`price`, `not_interested`, `do_not_contact`, `callback_later`, …) and whether a callback was requested. |
| `summarize` | A short factual summary, the disposition (`qualified`, `not_qualified`, `refused`, `callback_requested`, `no_conversation`), the follow-up actions and the detected language. |
| `build_handoff` | The briefing for a human: what the lead wants, what was asked, what to do next, and the priority. Only runs when a human is needed. |
| `persist` | Writes `call_outcomes` (one row per call — a re-run updates it), copies the summary onto the call and creates or refreshes the handoff. |

### Degradation

| Situation | Behaviour |
|---|---|
| No LLM configured (`CALLORA_VOICE_LLM_PROVIDER=null`) | The workflow still produces an outcome from the provider payload. `engine = "provider-fallback"`. |
| The model fails or answers with unusable JSON | The job fails and is retried with exponential backoff. **No partial outcome is written.** |
| The call has no transcript | `disposition = "no_conversation"`, the model is never called. |
| The post-call job runs twice | The second run updates the same outcome row instead of creating a second one. |

### Handoffs

Two kinds:

* `warm_transfer` — the agent transferred the call to a human during the
  conversation (recorded from the `pre-transfer` webhook).
* `callback` — somebody has to call the lead back.

Statuses: `requested → initiated → completed` (or `failed` / `cancelled`).
A handoff carries a briefing, a reason, a reason code and a `context` blob
with the qualification answers, the refusal, the follow-ups and the
recommended action.

---

## 7. Enabling real calling

Real calling is a three-step decision. Do not skip step 1.

### Step 1 — provider account and agent

Create a Bolna account, provision a phone number and build the agent (the
prompt, the welcome message, the language). Note the **agent id**. The
**caller id** (E.164) is optional: without one Bolna places the call
through the telephony provider configured on the agent (Vobiz, Twilio,
Plivo, ...). Put the webhook URL of your deployment on the agent
(`https://<host>/api/v1/voice/webhooks/bolna/execution`).

> **The webhook URL is not optional in practice.** An agent without one makes
> Bolna push no execution event, and the call then only moves through the
> reconciliation sweep ([5.5](#55-call-status-synchronization)) — it still
> ends, but it takes a poll cycle instead of a second.

### Step 2 — credentials and the compliance policy

Put the credentials in the **`.env` file in the repository root**
(`<repo>/.env`, next to `docker-compose.yml`). That single file is read by
Docker Compose *and* by the backend itself, no matter which directory you
start it from:

```bash
# <repo>/.env  — gitignored, never committed
CALLORA_BOLNA_API_KEY=<your key>
CALLORA_BOLNA_DEFAULT_AGENT_ID=<agent id>
CALLORA_BOLNA_DEFAULT_FROM_NUMBER=+91XXXXXXXXXX   # optional, see below
CALLORA_BOLNA_WEBHOOK_SECRET=<shared secret>   # optional but recommended
```

If you prefer not to use a file, export the variables in the shell that
starts the backend and the worker — shell variables win over the file:

```bash
export CALLORA_BOLNA_API_KEY="<your key>"
export CALLORA_BOLNA_DEFAULT_AGENT_ID="<agent id>"
export CALLORA_BOLNA_DEFAULT_FROM_NUMBER="+91XXXXXXXXXX"   # optional
export CALLORA_BOLNA_WEBHOOK_SECRET="<shared secret>"   # optional but recommended
```

> **The caller number is optional.** Bolna's `/call` endpoint takes only
> the agent id and the recipient: when `from_phone_number` is absent
> Bolna places the call itself, through the telephony provider of the
> agent. Leave `CALLORA_BOLNA_DEFAULT_FROM_NUMBER` empty (or unset) and
> Callora sends no caller number at all. Set it only when the call must
> come from a specific number — and then only to a number the provider
> account owns, in E.164: Bolna rejects a number it does not know
> (`Calling from_number ... doesn't exist for <provider>`).

> **Only the key is needed to exercise the flow.** With
> `CALLORA_VOICE_ENABLE_OUTBOUND_CALLS=false` (the default) the key is
> never sent anywhere — the provider is not contacted at all, so the
> `provider_ready` check is skipped. The key only matters the moment the
> switch is turned on.

Then configure the policy per business and **verify it**:

```bash
curl -s -X PATCH http://localhost:8001/api/v1/voice/business \
     -H "X-API-Key: dev-key-biz-001" -H "Content-Type: application/json" \
     -d '{"calling_window_start": 10, "calling_window_end": 19,
          "require_consent": true, "dnd_enabled": true,
          "max_calls_per_lead_per_day": 1, "max_concurrent_calls": 2}'
```

Make sure every lead that may be called has a voice consent record:

```bash
curl -s -X PUT http://localhost:8001/api/v1/voice/leads/lead_voice_0001/consent \
     -H "X-API-Key: dev-key-biz-001" -H "Content-Type: application/json" \
     -d '{"status": "granted", "channel": "voice", "source": "website-form",
          "granted_at": "2026-10-21T09:00:00Z",
          "evidence": {"form_id": "f_812", "ip": "203.0.113.7"}}'
```

### Step 3 — the switch

```bash
export CALLORA_VOICE_ENABLE_OUTBOUND_CALLS="true"
```

Restart the backend and the worker. From now on an allowed call is placed
with Bolna and the response contains `"simulated": false`.

Until this is set, every accepted call is `simulated` and costs nothing.

> **Recommendation:** before enabling the switch for real, run a small
> pilot — one business, a handful of leads, a narrow calling window — and
> watch `GET /api/v1/voice/ops/stats` and `GET /api/v1/voice/ops/audit`
> for a few days.

---

## 8. Provider costs and risks

### 8.1 What a call costs

Bolna prices a call as the sum of five components across three parts
(source: [bolna.ai/pricing](https://www.bolna.ai/pricing) and the Bolna
pricing docs, retrieved 2026-10-21 — **verify against the live pricing page
before quoting a number to a customer**).

| Part | Component | Billed by |
|---|---|---|
| A — Voice AI | Speech-to-text | call duration (rounded to seconds) |
| | LLM | tokens generated |
| | Text-to-speech | characters synthesized |
| B — Telephony | The carrier | call duration (rounded to minutes) |
| C — Platform | Bolna platform fee | call duration |

Published figures:

* Pay-as-you-go starts at **$0.06 per minute (~₹5.52)**, billed from a
  prepaid wallet; larger top-ups move the effective rate to roughly
  **4.5¢/min (~₹4.15)**, the Pilot plan to about **4.2¢/min (~₹4)**.
* The Pilot plan is a one-off purchase billed in 30-second pulses:
  **$500 for 12,000 minutes (~4.2¢/min)** or **$300 for 6,500 minutes
  (~4.6¢/min)**.
* Every new account gets **$5 in free credits**.
* The flat per-minute rate bundles a curated list of "preferred" ASR, LLM
  and TTS models. Using a model outside that list moves that component out
  of the flat rate and onto variable provider pricing — this is the most
  common reason two similar agents cost different amounts.
* **BYOK** (bringing your own LLM/TTS/STR keys) removes those components
  from the Bolna bill; on the Basic plan a single BYOK component moves the
  whole call to base token pricing.
* Agents and concurrency are **not** billed per minute: concurrency is an
  account setting, and how many agents a workspace may hold is a plan
  limit.

### 8.2 What Callora itself costs

| Item | Cost |
|---|---|
| Bolna voice minutes | The dominant cost (see above). |
| Telephony | Per minute, by carrier and country. |
| Post-call analysis (LangGraph + Groq) | A few model calls per completed call — orders of magnitude cheaper than a voice minute, but not free. Use `CALLORA_VOICE_LLM_PROVIDER=null` to remove it entirely. |
| PostgreSQL, Redis, the worker | Negligible; a small instance each. |

### 8.3 Risks and how the design mitigates them

| Risk | Impact | Mitigation in this module |
|---|---|---|
| **Calling a number that must not be called** | Regulatory exposure and reputation damage. | The compliance gate is mandatory and fail-closed; consent and DND are checked against PostgreSQL before every call; a blocked call never reaches the provider. |
| **Unwanted calls / spam complaints** | TRAI and telco penalties, blacklisting of the calling number. | Per-lead daily quota, per-business daily quota, concurrency cap, duplicate-call cooldown, `do_not_call` kill switch, DND registry. The two daily quotas can be skipped in a development environment only — and only with an explicit opt-in; everywhere else they stay enforced. |
| **Calling outside the permitted hours** | Regulatory exposure. | `calling_hours` check per business timezone and weekday. The default window (10:00–19:00 local) is deliberately inside the 10:00–21:00 window that TRAI's Unsolicited Commercial Communications Regulations set for promotional voice calls. **Confirm the current rule set with counsel.** |
| **Twice dialling the same lead** | Complaints, wasted minutes. | In-flight check + cooldown + idempotency key on the initiation endpoint. |
| **Losing a call record (crash between provider call and DB write)** | A call nobody can reconcile. | The decision and the call row are committed **before** the provider is contacted; the provider id is written immediately after, and `POST /calls/{id}/refresh` re-reads the provider state. |
| **Double billing on retry** | Wasted money and two live calls. | Only a *connection* failure (the request never left the process) is retried automatically. A timeout or a provider error is recorded and left for an operator. |
| **Webhook storm / duplicate deliveries** | Duplicate analysis, corrupted state. | Fingerprint-based dedupe with a unique constraint; duplicate deliveries are acknowledged, not retried. |
| **Out-of-order webhook events** | A completed call flipped back to "in progress". | A monotonic status ranking: an event may never move a call backwards; it is stored and ignored. |
| **Losing the post-call analysis** | No summary, no qualification result. | The job queue lives in PostgreSQL, the worker retries with exponential backoff, and a job that exhausts its attempts is parked as `dead` and can be retried from `POST /ops/jobs/{id}/retry`. |
| **Losing Redis** | Rate limiting stops working. | Redis is an accelerator only; every method degrades to a no-op. The compliance gate never reads a cached answer as its only evidence. |
| **LLM hallucinating** | A fabricated qualification result reaches a salesperson. | Prompts ground the analysis on the transcript; a model failure produces **no** outcome rather than a wrong one; the raw model payload is persisted next to the structured result for review. |
| **Provider price change / wallet exhaustion** | Unexpected spend or calls that silently stop. | `GET /ops/stats` reports the accumulated cost; Bolna stops calling when the wallet is empty (surface it through the provider's own alerts). |
| **Concurrency ceiling on the provider account** | Calls queued at Bolna instead of dialling. | Bolna queues outbound calls above your concurrency limit (trial: 2, paid: 10 from, scaling with usage, higher on Enterprise). Keep `max_concurrent_calls` at or below the account limit, and check the limit in the Bolna dashboard (Settings → Organisation → Account Info). |
| **Recording/transcript privacy** | DPDP Act exposure; customer objection. | Recordings and transcripts stay with the provider by default; Callora stores the transcript, the summary and the payload. Set a retention policy for `calls.transcript` and `calls.provider_payload`, and obtain the consent that your data-retention policy requires. |
| **Numbers without consent** | Regulatory exposure. | `require_consent` is on by default and the consent row keeps the source, the timestamps and the evidence payload so a decision can be audited against what was known at the time. |

### 8.4 India-specific compliance context (verify with counsel)

The gate is built so that the policy is *configurable and auditable* rather
than hard-coded, because the rules change. The defaults follow the shape of
TRAI's Unsolicited Commercial Communications Regulations (2018, as amended
in 2021):

* promotional voice calls are restricted to a 10:00–21:00 window;
* the National Customer Preference Register (DND) and the caller's own
  preference must be honoured;
* a recipient who asks to be removed must be taken off the list
  immediately — this is what the `do_not_call` flag and the DND registry
  implement.

**These notes are engineering context, not legal advice.** Before going
live, confirm the current rules with counsel, map each rule to a
configured check, and record the mapping in the business' policy. The gate
is the enforcement point: if a rule is not expressed as a check, nothing
enforces it.

---

## 9. Testing

### 9.1 Automated tests

The suite is offline by construction: **no test contacts Bolna, Redis or an
LLM.** The provider is the `FakeProvider` double, the model is the
`ScriptedLLM` double, and outbound calling is switched off in the fixture.

```bash
cd backend
python -m pytest tests/test_voice_compliance.py -q   # the gate
python -m pytest tests/test_voice_cooldown.py -q    # the duplicate-call cooldown
python -m pytest tests/test_voice_orchestrator.py -q # call initiation
python -m pytest tests/test_voice_webhooks.py -q     # webhook intake
python -m pytest tests/test_voice_jobs.py -q         # the durable queue
python -m pytest tests/test_voice_postcall.py -q     # the LangGraph workflow
python -m pytest tests/test_voice_api.py -q          # the HTTP layer
python -m pytest tests/test_voice_knowledge.py -q    # knowledge base CRUD & search
python -m pytest tests/test_voice_followup.py -q     # follow-up question answering API & memory
python -m pytest tests/test_voice_evaluation.py -q   # 48-question automated evaluation benchmark
python -m pytest -q                                  # everything
```

> **Tip — the first import is slow.** On a cold file cache the very first
> `import app.main` costs ~30 s (the machine has to read the interpreter's
> bytecode and the packages from disk; Windows Defender scans them). It
> drops to ~3 s once the cache is warm, so a "slow" run is usually a cold
> one. The Groq/LLM stack is never imported at start-up — the import is
> lazy and happens only when a post-call analysis actually calls a model.

| File | Tests | Covers |
|---|---|---|
| `tests/test_voice_compliance.py` | 59 | Every mandatory check in isolation, fail-closed behaviour, DND, consent, calling window, quotas, concurrency, duplicate-call rules, and `provider_ready` in simulated vs. placed mode. |
| `tests/test_voice_cooldown.py` | 24 | The configurable duplicate-call cooldown: the production default, the environment variable, the deprecated minute-based variable, the enforced window, and the in-flight guard that stays mandatory. |
| `tests/test_voice_orchestrator.py` | 29 | Simulated / blocked / provider paths, the retry semantics, the audit trail, `refresh_status`, provider credentials. |
| `tests/test_voice_webhooks.py` | 31 | Applying events, dedupe, out-of-order delivery, the post-call job, the duplicate-call guard, signature verification, the pre-transfer event. |
| `tests/test_voice_jobs.py` | 23 | Enqueue, dedupe key, atomic claim, retries, backoff, dead jobs, stale release, the worker. |
| `tests/test_voice_postcall.py` | 15 | The LangGraph workflow, qualification, refusals, handoff creation, the degradation path. |
| `tests/test_voice_api.py` | 79 | Authentication, call initiation, idempotency, the 409 conflict, the rate limit, the webhook endpoints, the handoffs, the ops endpoints, the business configuration, consent and DND. |
| `tests/test_voice_knowledge.py` | 8 | Knowledge base CRUD, business scoping isolation, batch creation, and keyword/tag relevance search. |
| `tests/test_voice_followup.py` | 8 | Follow-up question answering endpoint, conversation memory persistence, ambiguity clarification, missing knowledge fallback, opt-out enforcement, and Bolna tool definition. |
| `tests/test_voice_evaluation.py` | 1 | 48-question automated evaluation benchmark verifying retrieval relevance, factual accuracy, anti-hallucination, clarification, escalation, opt-out, and multi-turn continuity. |

The whole suite (856 tests, across all modules) runs with 100% pass rate.

### 9.3 Automated Evaluation Benchmark

An automated evaluation framework evaluates 48 realistic questions across 11 distinct categories:

```bash
pytest backend/tests/test_voice_evaluation.py -v -s
```

#### Benchmark Results (Pass Rate: 100.0%)

- **Total Questions Evaluated**: 48
- **Overall Pass Rate**: 100.0% (48/48)
- **Retrieval Relevance Rate**: 100.0% (target: >= 90%)
- **Factual Accuracy & Groundedness**: 100.0% (target: >= 90%)
- **Unsupported Claims / Hallucination Rate**: 0.0% (target: 0.0%)
- **Clarification Accuracy**: 100.0%
- **Escalation Accuracy**: 100.0%
- **Opt-Out Compliance**: 100.0%
- **Conversation Continuity**: 100.0%

### 9.2 Manual testing with curl

With `CALLORA_VOICE_ENABLE_OUTBOUND_CALLS=false` (the default), the whole
flow can be exercised without spending money **and without a Bolna
account** — a simulated call never reaches the provider, so the
`provider_ready` check is skipped:

```bash
export BASE=http://localhost:8001
export H="X-API-Key: dev-key-biz-001"
```

1. **Configure the business**

   ```bash
   curl -s -X PATCH $BASE/api/v1/voice/business -H "$H" \
        -H "Content-Type: application/json" \
        -d '{"calling_window_start":0,"calling_window_end":24,"require_consent":true}'
   ```

2. **Create a lead** (Module 1) and note its id.

3. **Record consent**

   ```bash
   curl -s -X PUT $BASE/api/v1/voice/leads/<lead_id>/consent -H "$H" \
        -H "Content-Type: application/json" \
        -d '{"status":"granted","channel":"voice","source":"manual-test"}'
   ```

4. **Request a call** — the response shows the decision and every check.

   ```bash
   curl -s -X POST $BASE/api/v1/voice/calls -H "$H" \
        -H "Content-Type: application/json" \
        -H "Idempotency-Key: manual-test-1" \
        -d '{"lead_id":"<lead_id>","dry_run":true}'
   ```

   Repeat the same request with the same key: the same call comes back and
   **no second call is created**. Change the body and reuse the key: `409`.

5. **Simulate a provider event** (what Bolna would send)

   ```bash
   curl -s -X POST $BASE/api/v1/voice/webhooks/bolna/execution \
        -H "Content-Type: application/json" \
        -d '{"id":"exec_test_1","status":"completed",
             "transcript":"Agent: Hello. Rahul: Hi, I am interested.",
             "conversation_duration":45,"total_cost":1.25}'
   ```

   Send it again → `{"status":"duplicate"}`. Send it with an earlier status
   → `{"status":"ignored_out_of_order"}`.

6. **Run the worker** (in another terminal) and read the outcome.

   ```bash
   cd backend && python -m app.modules.voice.jobs.worker
   curl -s $BASE/api/v1/voice/calls/<call_id>/outcome -H "$H"
   ```

7. **Check the operator views**

   ```bash
   curl -s $BASE/api/v1/voice/ops/stats -H "$H"
   curl -s $BASE/api/v1/voice/ops/audit -H "$H"
   curl -s $BASE/api/v1/voice/ops/compliance-decisions -H "$H"
   ```

### 9.3 Swagger UI

`http://localhost:8001/docs` documents every endpoint, the request bodies
and the response models. The two webhook endpoints are listed there too —
they are the quickest way to paste a Bolna payload while developing.

### 9.4 Postman / curl collection

Any HTTP client works. The two headers that matter are `X-API-Key` and
`Idempotency-Key`.

---

## 10. Operational runbook

### The worker

```bash
cd backend
python -m app.modules.voice.jobs.worker          # run forever
```

In Compose it runs as the `worker` service. The worker claims a job with an
atomic conditional `UPDATE`, so several workers can run at the same time
without processing the same job twice. Jobs left `running` by a crashed
worker are released automatically after 15 minutes.

### Recovering a dead job

```bash
curl -s -X POST $BASE/api/v1/voice/ops/jobs/<job_id>/retry -H "$H"
```

The retry is audited (`job.retried`).

### A missed webhook

```bash
curl -s -X POST $BASE/api/v1/voice/calls/<call_id>/refresh -H "$H"
```

This polls the provider once and updates the call. It never creates a call
and it does not re-run the compliance gate (no new call is placed).

### A call that is stuck in `scheduled`

A call that never moved past `scheduled` means the provider never reported
it — most often because the Bolna agent has no `webhook_url`. Reconcile the
whole business:

```bash
curl -s -X POST $BASE/api/v1/voice/ops/sync-calls -H "$H"
```

The sweep asks the provider about every call that is due, applies what the
provider says and reports what it did. A call that the provider still
reports as waiting more than `CALLORA_VOICE_SCHEDULED_CALL_TIMEOUT_SECONDS`
after it was due is closed as `failed` by the timeout policy (set
`CALLORA_VOICE_STUCK_CALL_TIMEOUT_ACTION=keep_polling` to keep polling
instead).

The worker does the same thing on its own, every
`CALLORA_VOICE_STATUS_SYNC_INTERVAL_SECONDS` — check that the worker is
running:

```bash
docker compose logs -f worker
```

### Finding out why a call was blocked

```bash
curl -s "$BASE/api/v1/voice/ops/compliance-decisions?lead_id=<lead_id>" -H "$H"
```

Each decision lists every check, its outcome and the reason code. The
`calls` table also carries a `blocked_reason` with the same reason codes.

### Rate limiting

The limiter is advisory and Redis-backed. When Redis is down the requests
pass through (and the degradation is logged). To change the limit, set
`CALLORA_VOICE_RATE_LIMIT_PER_MINUTE`.

---

## 11. Known limitations

* **Only Bolna is implemented.** Plivo and Sarvam are not wired up. The
  provider registry (`providers/registry.py`) is the seam: an adapter plus
  one registry entry is all a new provider needs.
* **No frontend.** Everything is API-only; Swagger UI is the UI.
* **The webhook signature scheme is optional.** Bolna does not document an
  HMAC scheme for execution webhooks; `CALLORA_BOLNA_WEBHOOK_SECRET` adds
  one, and without it the endpoint cannot verify the caller
  cryptographically.
* **The webhook URL lives on the Bolna agent, not in Callora.** An agent
  without one pushes nothing, and the call only moves through the
  reconciliation sweep — which polls the provider, but only every
  `CALLORA_VOICE_STATUS_SYNC_INTERVAL_SECONDS`, and only for the calls that
  are due.
* **The sweep can only see what the provider still knows.** A call whose
  execution Bolna has forgotten is closed as `failed`; a call the provider
  cannot answer for is left untouched and retried on the next sweep.
* **The external DND lookup is an interface, not an implementation.** The
  local registry is authoritative; plug in a national-registry adapter
  through `ComplianceGate(dnd_lookup=...)` to add a remote source. A
  configured lookup is mandatory and fails closed.
* **The post-call workflow is transcript-based.** It cannot qualify a call
  that produced no transcript; such a call is recorded as
  `no_conversation`.
* **The LLM analysis is best-effort.** A failing model produces no outcome
  rather than a wrong one, and the raw payload is kept for review.
* **No campaign builder.** A campaign is "a loop that calls
  `POST /api/v1/voice/calls` for a list of leads" — the quotas, the
  cooldown and the calling window do the throttling.
* **Recording retention is not enforced by the code.** Set a retention
  policy for `calls.transcript` / `calls.provider_payload` yourself.
