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
6. [The post-call workflow](#6-the-post-call-workflow)
7. [Enabling real calling](#7-enabling-real-calling)
8. [Provider costs and risks](#8-provider-costs-and-risks)
9. [Testing](#9-testing)
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

```bash
curl -s http://localhost:8001/health
# {"status":"healthy"}

curl -s -X POST http://localhost:8001/api/v1/voice/calls \
     -H "X-API-Key: dev-key-biz-001" \
     -H "Content-Type: application/json" \
     -d '{"lead_id":"lead_xxx","dry_run":true}'
# 201, "simulated": true — no provider was contacted
```

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

### Voice provider — Bolna

| Variable | Default | Meaning |
|---|---|---|
| `CALLORA_BOLNA_API_KEY` | *(unset)* | Bolna API key. Without it the `provider_ready` check fails and every call is blocked. |
| `CALLORA_BOLNA_BASE_URL` | `https://api.bolna.ai` | API base URL. |
| `CALLORA_BOLNA_TIMEOUT_SECONDS` | `20.0` | Per-request timeout for provider calls. |
| `CALLORA_BOLNA_WEBHOOK_SECRET` | *(unset)* | Enables HMAC-SHA256 verification of webhook signatures. See [Webhook signature](#54-webhook-signature). |
| `CALLORA_BOLNA_DEFAULT_AGENT_ID` | *(unset)* | Fallback Bolna agent when a business has none. |
| `CALLORA_BOLNA_DEFAULT_FROM_NUMBER` | *(unset)* | Fallback caller id in E.164. |
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
| `CALLORA_VOICE_MAX_CONCURRENT_CALLS` | `5` | Calls allowed in flight at the same time. |
| `CALLORA_VOICE_DUPLICATE_CALL_COOLDOWN_MINUTES` | `60` | Minimum gap between two calls to the same lead. `0` disables it. |
| `CALLORA_VOICE_SUPPORTED_LANGUAGES` | `en,hi,kn` | Languages the deployment is configured for. |
| `CALLORA_VOICE_RATE_LIMIT_PER_MINUTE` | `60` | Per-business call-initiation limit (Redis counter). |

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
| 6 | `duplicate_call` | Another call to this lead is in flight, or the lead was called less than the cooldown ago. |
| 7 | `quota` | The per-lead or per-business daily quota is exhausted. |
| 8 | `concurrency` | The business already has `max_concurrent_calls` calls in flight. |
| 9 | `provider_ready` | No agent id is configured, or the provider credentials are missing. |

**Fail closed.** Every check is wrapped: if it raises, the check is recorded
as `error` with the reason code `check_unavailable` and the call is
blocked. A check that cannot answer never answers "yes".

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
| `POST` | `/webhooks/bolna/execution` | **Provider webhook** — call lifecycle events. |
| `POST` | `/webhooks/bolna/pre-transfer` | **Provider webhook** — warm-transfer events. |

24 paths, 30 operations. The full, always-current contract is the OpenAPI
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
* An event that matches no known call is stored and acknowledged with
  `{"status": "unmatched"}`.
* A terminal status queues the post-call job (never runs the analysis
  inline).

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
prompt, the welcome message, the language). Note the **agent id** and the
**caller id** (E.164). Put the webhook URL of your deployment on the agent
(`https://<host>/api/v1/voice/webhooks/bolna/execution`).

### Step 2 — credentials and the compliance policy

```bash
export CALLORA_BOLNA_API_KEY="<your key>"
export CALLORA_BOLNA_DEFAULT_AGENT_ID="<agent id>"
export CALLORA_BOLNA_DEFAULT_FROM_NUMBER="+91XXXXXXXXXX"
export CALLORA_BOLNA_WEBHOOK_SECRET="<shared secret>"   # optional but recommended
```

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
| **Unwanted calls / spam complaints** | TRAI and telco penalties, blacklisting of the calling number. | Per-lead daily quota, per-business daily quota, concurrency cap, duplicate-call cooldown, `do_not_call` kill switch, DND registry. |
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
python -m pytest tests/test_voice_orchestrator.py -q # call initiation
python -m pytest tests/test_voice_webhooks.py -q     # webhook intake
python -m pytest tests/test_voice_jobs.py -q         # the durable queue
python -m pytest tests/test_voice_postcall.py -q     # the LangGraph workflow
python -m pytest tests/test_voice_api.py -q          # the HTTP layer
python -m pytest -q                                  # everything
```

| File | Tests | Covers |
|---|---|---|
| `tests/test_voice_compliance.py` | 34 | Every mandatory check in isolation, fail-closed behaviour, DND, consent, calling window, quotas, concurrency, duplicate-call rules. |
| `tests/test_voice_orchestrator.py` | 23 | Simulated / blocked / provider paths, the retry semantics, the audit trail, `refresh_status`. |
| `tests/test_voice_webhooks.py` | 23 | Applying events, dedupe, out-of-order delivery, the post-call job, signature verification, the pre-transfer event. |
| `tests/test_voice_jobs.py` | 23 | Enqueue, dedupe key, atomic claim, retries, backoff, dead jobs, stale release, the worker. |
| `tests/test_voice_postcall.py` | 15 | The LangGraph workflow, qualification, refusals, handoff creation, the degradation path. |
| `tests/test_voice_api.py` | 70 | Authentication, call initiation, idempotency, the 409 conflict, the rate limit, the webhook endpoints, the handoffs, the ops endpoints, the business configuration, consent and DND. |

### 9.2 Manual testing with curl

With `CALLORA_VOICE_ENABLE_OUTBOUND_CALLS=false` (the default), the whole
flow can be exercised without spending money:

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
