# Callora

A configurable **outbound AI voice sales agent** for Indian businesses, built
on FastAPI, PostgreSQL, Redis, [Bolna](https://www.bolna.ai) and LangGraph.

Callora imports and standardises leads, decides who is worth calling, places
the call through a voice provider, records what happened, and turns the
conversation into structured intelligence (summary, qualification result,
refusal category, follow-up actions) plus a briefing for a human when the
lead has to be handed over.

Everything lives in the backend: the API, the compliance gate, the webhook
intake, the durable background job queue and the post-call AI workflow.

> **No real call is placed by default.**
> `CALLORA_VOICE_ENABLE_OUTBOUND_CALLS` is `false` out of the box, so every
> accepted call is recorded as `simulated` and no provider is contacted.
> Turning it on is a deliberate, documented step.

---

## Modules

| Module | What it does | Where |
|---|---|---|
| 1 — Lead import | CSV upload → map & standardize → validate & deduplicate → persist | `backend/app/modules/lead_import` |
| 2 — CRM sync | Conditional synchronization with an external CRM | `backend/app/modules/crm_sync` |
| 3 — Recovery agent | Detects neglected leads and creates recovery cases | `backend/app/modules/recovery_agent` |
| 4 — Eligibility engine | Rules that decide if a lead may be contacted (runs inside the sweep) | `backend/app/modules/recovery_agent` |
| 5 — Priority scoring | Ranks recovery cases 0–100 with a full factor breakdown | `backend/app/modules/recovery_agent` |
| — Recovery dashboard | Operator-facing views, AI outreach content, persisted outcomes | `backend/app/modules/recovery_dashboard` |
| **6 — Outbound voice agent** | **Call orchestration, compliance gate, webhooks, post-call AI, human handoff** | `backend/app/modules/voice` |

---

## Quick start

### Docker Compose (recommended)

```bash
docker compose up --build
```

- Backend: <http://localhost:8001> (interactive docs at `/docs`)
- PostgreSQL: `localhost:5433`
- Redis: `localhost:6380`

Migrations run automatically on startup (`alembic upgrade head`).

### Local

```bash
cd backend
pip install -r requirements.txt
export CALLORA_DATABASE_URL="sqlite:///./callora.db"     # or a Postgres URL
export CALLORA_API_KEYS="biz_001:dev-key-biz-001"
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

### Background worker (voice module)

The durable voice job queue is drained by a separate process:

```bash
cd backend
python -m app.modules.voice.jobs.worker
```

Redis is optional — set `CALLORA_REDIS_ENABLED=false` and every Redis-backed
feature (rate limiting, caching) degrades to a no-op instead of failing.

---

## Configuration

All variables use the `CALLORA_` prefix and are read by
`backend/app/config.py`. Everything has a working default; nothing is
required to boot the service.

| Variable | Default | Meaning |
|---|---|---|
| `CALLORA_DATABASE_URL` | `postgresql+psycopg2://callora:callora@localhost:5432/callora` | SQLAlchemy URL. |
| `CALLORA_API_KEYS` | `biz_001:dev-key-biz-001` | `business_id:api_key` pairs → resolves `X-API-Key`. |
| `CALLORA_LOG_LEVEL` | `INFO` | Root log level. |
| `CALLORA_VOICE_ENABLE_OUTBOUND_CALLS` | `false` | **Master switch** for real outbound calling. |
| `CALLORA_BOLNA_API_KEY` | *(unset)* | Bolna API key. Only needed for calls that are actually placed — a simulated call skips the check. |
| `CALLORA_REDIS_URL` | `redis://localhost:6379/0` | Connection URL for the accelerator. |
| `CALLORA_VOICE_LLM_PROVIDER` | `groq` | `groq`, `langchain`, or `null` (no model). |
| `GROQ_API_KEY` | *(unset)* | Key for the Groq client. |

The complete reference — every voice, Bolna, Redis and LLM variable — lives
in [`docs/voice-agent.md`](docs/voice-agent.md#3-configuration-reference).

### Where to put your secrets

Copy `.env.example` to **`.env` in the repository root** (next to
`docker-compose.yml`) and fill in the values:

```bash
cp .env.example .env     # Windows: copy .env.example .env
```

```ini
CALLORA_BOLNA_API_KEY=<your key>
CALLORA_BOLNA_DEFAULT_AGENT_ID=<agent id>
CALLORA_BOLNA_DEFAULT_FROM_NUMBER=+91XXXXXXXXXX
```

That one file is read by **Docker Compose and by the backend itself**,
wherever you start it from — the path is resolved from the package
location, not the working directory. Shell variables override the file,
and the test suite ignores it entirely so your secrets can never leak
into a test run. `.env` is gitignored; `.env.example` is not.

### Provider credentials

**Plivo is not implemented** — Bolna is the only voice provider, and the
provider registry is the seam for adding another.

A Bolna API key and a voice agent id are needed only for calls that are
*actually placed*:

| Mode | `CALLORA_VOICE_ENABLE_OUTBOUND_CALLS` | Bolna key | Result |
|---|---|---|---|
| Simulated | `false` | not needed | `201` — the call is recorded, no provider is contacted, ₹0 |
| Real | `true` | required | `201` — Bolna places the call and bills the minutes |

With the switch on and no credentials the request is rejected with
`403 compliance_blocked` / `provider_not_configured` — the gate never lets
an unconfigured deployment reach a provider. With the switch off (or
`dry_run: true`) the `provider_ready` check is reported as `skipped`, so
the whole flow can be exercised from Swagger or curl without a Bolna
account.

---

## Documentation

| Document | What it covers |
|---|---|
| [`docs/voice-agent.md`](docs/voice-agent.md) | **Module 6** — architecture, setup, the compliance gate, the full API reference, the post-call workflow, how to enable real calling, provider costs and risks, testing and the runbook. |
| [`wworkflow.md`](wworkflow.md) | End-to-end workflow and endpoint testing guide for Modules 1–5 plus the recovery dashboard, with real request/response transcripts. |
| `/openapi.json` (runtime) | The always-current machine-readable API contract. |

---

## Safety model

- **Fail closed.** Every mandatory pre-call check must pass. If a check cannot
  be completed, the call is **blocked** — never approved.
- **PostgreSQL is the source of truth.** Redis is an accelerator only; losing
  it slows the backend down but never turns a block into an approval.
- **Auditable.** Every compliance decision, call request and state change is
  written to an append-only audit trail.
- **Idempotent.** Call initiation accepts an `Idempotency-Key`; the same key
  with the same body returns the same call, a different body returns `409`.
- **Webhook-safe.** Events are deduplicated, applied in order, and never move
  a call backwards.

---

## Tests

```bash
cd backend
python -m pytest -q
ruff check app tests alembic
```

The suite is offline by construction — no test contacts Bolna, Redis or an
LLM. The provider and the model are replaced by test doubles.

---

## Stack

FastAPI · SQLAlchemy · Alembic · PostgreSQL · Redis · Pydantic v2 ·
LangGraph · Groq (optional) · Bolna (voice provider) · pytest · ruff
