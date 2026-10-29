# Callora API Documentation

**Audit Date:** 2026-10-29  
**App Version:** 0.5.0 (FastAPI)  
**Base App Title:** Callora Backend  
**Documentation Scope:** Implemented functionality only (planned/TODO items excluded)  
**Source of Truth:** Application source code under `backend/app/`, generated OpenAPI spec at `/tmp/callora_openapi.json`, endpoint inventory at `/tmp/ops.txt`, schema inventory at `/tmp/schemas.txt`.

> **Important Notes:** No frontend directory exists in this repository; examples are framework-agnostic with React + TypeScript patterns noted where helpful. No live server/database/external providers were exercised. Credentials/tokens/keys are not printed — use `<your-api-key>`, `<bolna-webhook-secret>`, etc. placeholders. No application code was modified to produce this document.

---

## Table of Contents

- [1. Overview & Architecture](#1-overview--architecture)
- [2. Base URLs, Environments & Config](#2-base-urls-environments--config)
- [3. Authentication](#3-authentication)
- [4. Conventions, Errors & Status Codes](#4-conventions-errors--status-codes)
- [5. Endpoint Inventory (Accounting)](#5-endpoint-inventory-accounting)
- [6. Detailed Module Reference](#6-detailed-module-reference)
  - [6.1 Core (Root/Health)](#61-core-roothealth)
  - [6.2 Leads (JSON)](#62-leads-json)
  - [6.3 Lead Imports (CSV)](#63-lead-imports-csv)
  - [6.4 CRM Connections](#64-crm-connections)
  - [6.5 CRM Synchronization](#65-crm-synchronization)
  - [6.6 Agent Leads (Agent-Facing)](#66-agent-leads-agent-facing)
  - [6.7 Recovery Agent (Detection/Sweep)](#67-recovery-agent-detectionsweep)
  - [6.8 Recovery Dashboard](#68-recovery-dashboard)
  - [6.9 Recommendation Agent](#69-recommendation-agent)
  - [6.10 Recovery Workflow](#610-recovery-workflow)
  - [6.11 Voice — Business](#611-voice--business)
  - [6.12 Voice — Leads, Consent, DND](#612-voice--leads-consent-dnd)
  - [6.13 Voice — Calls](#613-voice--calls)
  - [6.14 Voice — Handoffs/Callbacks](#614-voice--handoffscallbacks)
  - [6.15 Voice — Knowledge Base](#615-voice--knowledge-base)
  - [6.16 Voice — Operations](#616-voice--operations)
  - [6.17 Voice — Follow-Up Questions](#617-voice--follow-up-questions)
  - [6.18 Voice — Bolna Custom Functions (Tools)](#618-voice--bolna-custom-functions-tools)
  - [6.19 Voice — Webhooks (Bolna)](#619-voice--webhooks-bolna)
- [7. Schemas (Key Types)](#7-schemas-key-types)
- [8. Error & Status Reference](#8-error--status-reference)
- [9. Uploads, Webhooks & Integrations](#9-uploads-webhooks--integrations)
- [10. Frontend Integration Guidance](#10-frontend-integration-guidance)
- [11. Workflows](#11-workflows)
- [12. TypeScript Interfaces & Client Patterns](#12-typescript-interfaces--client-patterns)
- [13. DB Entities (Relevant)](#13-db-entities-relevant)
- [14. Environment Variables](#14-environment-variables)
- [15. Limitations, Discrepancies & Security Notes](#15-limitations-discrepancies--security-notes)
- [16. Verification Summary](#16-verification-summary)
- [17. Source References](#17-source-references)

---

## 1. Overview & Architecture

- **Framework:** FastAPI (app version `0.5.0`, title "Callora Backend")
- **API Prefix:** All business routes under `/api/v1` (see `backend/app/main.py:40,77-101`). Core endpoints `GET /` and `GET /health` are unprefixed.
- **Modules present (implemented):**
  - Module 1: Lead & Customer Import (JSON create/list/get, CSV import with mapping, status/errors)
  - Module 2: CRM Synchronization + CRM Connections
  - Agent Leads storage/query surface
  - Module 3: Recovery Agent (opportunity detection/sweep) + Recovery Dashboard
  - Recommendation Agent
  - Recovery Workflow execution layer (eligibility, runs, steps, bookings, reminders, outcomes/costs/revenue, analytics, rules, integrations)
  - Module 6: Outbound Voice Agent (Bolna) — business config, leads/consent/DND, calls, handoffs, ops, knowledge, follow-ups, tools, webhooks
- **Router registration order (affects shadowing):** `leads`, `imports`, `crm_sync`, `crm_connections`, `agent_leads`, `recovery_agent_api`, `recovery_dashboard_api`, then voice routers, then `recommendation_agent_api`, then `recovery_workflow_api` (lines 77-101). Because `recovery_agent_api` is registered before `recovery_dashboard_api` under the same prefix `/agent/recovery`, **dashboard routes shadow the recovery-agent routes of the same path** at runtime (see §15.2).

---

## 2. Base URLs, Environments & Config

- **Local dev (typical):** `http://localhost:8000` (FastAPI)
- **Prefixed:** `http://localhost:8000/api/v1/...`
- **Environment:** `CALLORA_ENVIRONMENT` (default `production`). Development unlocks only explicit envs: `local`, `development`, `dev`, `test`, `testing` (see `config.py:29-32,515-551`). Unknown/typo treated as production.
- **Config loading:** `.env` files loaded from repo root and `backend/` (later wins); `CALLORA_IGNORE_DOTENV=1` disables in tests. Settings via `pydantic-settings` with `env_prefix=CALLORA_`.
- **Logging:** configured via `setup_logging(settings.log_level)` at lifespan start.
- **DB:** `CALLORA_DATABASE_URL` (default `postgresql+psycopg2://callora:callora@localhost:5432/callora`).
- **Redis:** optional accelerator (not source of truth for compliance) — `CALLORA_REDIS_URL`, `CALLORA_REDIS_ENABLED`.

---

## 3. Authentication

### 3.1 Business API Key (majority of endpoints)
- **Header:** `X-API-Key` (case-insensitive in FastAPI header binding; OpenAPI shows both `X-API-Key` and `x-api-key` variants on some routes)
- **Resolution:** `app.auth.authenticate_business` reads from `settings.api_key_map` parsed from `CALLORA_API_KEYS` as comma-separated `business_id:api_key` pairs (default `biz_001:dev-key-biz-001`). Uses `hmac.compare_digest` for constant-time comparison.
- **Behavior:** missing -> `401 authentication_failed` ("an X-API-Key header is required"); unknown/invalid -> `401 authentication_failed` ("the provided API key is not valid"). Returns `business_id`.
- **Scope:** All endpoints under `/api/v1` except where explicitly noted as unauthenticated (see §6 and §15).

### 3.2 Voice Tools
- **Auth paths (voice tools):** `Authorization: Bearer <token>` OR `X-API-Key`. 
  - Accepts business API key from `CALLORA_API_KEYS`, OR matches `CALLORA_VOICE_TOOL_SECRET` (shared secret). 
  - If `CALLORA_VOICE_TOOL_SECRET` is empty, shared-secret access is disabled (401). 
- **Tool errors:** Mapped to flat envelope `{"success": false, "error": {"code": "...", "message": "..."}}` with **uppercased** codes; validation errors on tool routes use `request_validation_error` (uppercased in tool envelope). Unhandled tool errors return `500` with code `INTERNAL_ERROR`.

### 3.3 Voice Follow-Up Question (`POST /api/v1/voice/answer-question`)
- **Weaker compatibility auth (documented limitation):** Accepts business API keys. Also trusts provider-signature headers or a supplied `execution_id` plus `business_id` in some cases; if a key is supplied it must match the payload business. See §15.4.

### 3.4 Webhooks (Bolna)
- **Unauthenticated by `X-API-Key`.** Optional HMAC/shared-secret verification via `CALLORA_BOLNA_WEBHOOK_SECRET` (Bolna execution webhooks not documented with HMAC scheme; when unset signature verification not possible).

### 3.5 Unauthenticated inspection endpoints
- `GET /api/v1/voice/bolna/custom-functions` — no auth
- `GET /api/v1/voice/bolna/tool-definition` — no auth (returns OpenAI tool schema + Bolna custom task + webhook URL + API URL)

> **Security note:** Never print default keys in docs/examples. Use placeholders.

---

## 4. Conventions, Errors & Status Codes

### 4.1 Standard Error Envelope (most endpoints)
```json
{
  "error": {
    "code": "machine_readable_code",
    "message": "human readable summary",
    "details": [ /* optional */ ]
  }
}
```
- Validation: `422` with code `request_validation_error`, `details[]` has `location`, `message`, `type` (and `input`/`ctx` may appear).
- Unhandled exceptions: `500` with code `internal_error` (message "An unexpected error occurred"); if `CALLORA_DEBUG=true` exceptions are re-raised.
- See `backend/app/errors.py` for all `AppError` subclasses.

### 4.2 Tool Envelope (voice tools only)
```json
{"success": true, "message": "...", "data": {...}}  // or
{"success": false, "error": {"code": "UPPERCASED", "message": "..."}}
```
Tool success typically returns `200` unless a domain error occurs. Unhandled tool errors -> `500` `INTERNAL_ERROR`. Validation -> `422` tool envelope with code `REQUEST_VALIDATION_ERROR`.

### 4.3 Pagination Conventions (observed)
- Many list endpoints use `limit`/`offset` (defaults vary). Some return envelopes with `items`, `business_id`, `limit`, `offset` and sometimes `total`/`ranking`. 
- **Discrepancies (noted):** 
  - `GET /api/v1/voice/knowledge` returns `total` as page length (`len(serialized)`) not full count.
  - `GET /api/v1/voice/dnd` paginates in-memory and omits `total`.
  - `GET /api/v1/voice/leads` uses standard list envelope without `total`.
  - Workflow list envelopes generally omit `total` (reviews/appointments/followups/outcomes return `items,business_id,limit,offset`; reminders omit `limit/offset`).
  - `GET /api/v1/recommendations` returns `items,total,business_id` (no `limit/offset` echo).
  - Recovery Agent `/cases` (runtime, shadowed path) returns `items,business_id,limit,offset,ranking` with **no `total`**; Dashboard `/cases` returns `items,business_id,limit,offset,total,ranking`.
- Ordering: Leads ordered `created_at desc, id`. Import errors ordered by `row_number, id`. Appointments ordered newest `scheduled_start` first.

### 4.4 Dates/Times
- JSON datetimes typically ISO 8601 (UTC where noted). Some responses include `created_at/updated_at` as strings or null.

---

## 5. Endpoint Inventory (Accounting)

Based on analysis of `main.py`, `app/` structure, `/tmp/ops.txt` (extracted OpenAPI), `/tmp/schemas.txt`, and source:

### Counts (confirmed)
- **OpenAPI operations:** 114 total  
  - 112 business operations + `GET /` + `GET /health` = 114
- **Registered route handlers (source registrations):** 116 total  
  - 114 business handlers + `GET /` + `GET /health` = 116  
  - Includes **2 shadowed dashboard routes** (runtime shadowing by router order).  
  - `GET /api/v1/agent/recovery/cases` and `GET /api/v1/agent/recovery/cases/{case_id}` are registered by recovery-agent router first (lines 82) and shadowed by recovery-dashboard router registered later (line 83); dashboard defines them with explicit `operation_id`s (`dashboard_list_recovery_cases`, `dashboard_get_recovery_case`) so OpenAPI shows dashboard operation IDs overwriting in the merged spec, while recovery-agent handlers win at runtime.
- **Method totals (OpenAPI):** GET 55, POST 50, PUT 4, PATCH 3, DELETE 2 = 114
- **By tag (OpenAPI):** 2 core; 3 Agent Leads; 2 CRM Connections; 1 CRM Synchronization; 13 Recommendation Agent; 1 Recovery Agent; 7 Recovery Dashboard; 32 Recovery Workflow; 3 imports; 3 leads; 47 Voice

> See `test_auto_sync.py` expectation: 33 legacy + 47 voice + 32 workflow = 112 business operations (matches OpenAPI). No count discrepancy in tests.

### Path counts
- OpenAPI has 98 paths (duplicates merge). Source has 100 route registrations including duplicates.

---

## 6. Detailed Module Reference

### 6.1 Core (Root/Health)
| Method | Path | Auth | Purpose | Notes |
|--------|------|------|---------|-------|
| GET | `/` | None | Root welcome | Returns `{"message":"Welcome to Callora FastAPI Backend"}` |
| GET | `/health` | None | Health check | Returns `{"status":"healthy"}` |

### 6.2 Leads (JSON)
Router: `backend/app/api/leads.py` (prefix `/leads`, tag `leads`). Auth: `X-API-Key` (business-scoped).

| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| POST | `/api/v1/leads` | X-API-Key | Header `Idempotency-Key` (opt), body `LeadCreate` | 201 `LeadRead` | 401, 409 `conflict` (duplicate phone), 422, 500 | Duplicate phone -> 409 with details `[{"lead_id": "...", "reason": "duplicate_phone"}]`. Idempotency replay returns 200 + header `Idempotency-Replayed: true` and existing lead. `business_id` taken from auth (not from body). Validates/normalizes name/phone/source/language, limits custom fields to max 50, `deal_value` serialized as 2-decimal string. Creates outbox event `lead.created`, upserts into `agent_leads`. Publishes events after commit. |
| GET | `/api/v1/leads` | X-API-Key | Query: `source` (lowercased exact), `query` (case-insensitive name contains), `created_after`, `created_before` (datetime), `page`>=1 default 1, `page_size` 1..100 default 20 | 200 `LeadListResponse` | 401, 422 | Ordered `created_at desc, id`. Returns `{items, total, page, page_size}`. |
| GET | `/api/v1/leads/{lead_id}` | X-API-Key | Path `lead_id` | 200 `LeadRead` | 401, 404 `lead_not_found` (missing or cross-tenant) | Business-scoped; cross-tenant access returns 404 to prevent probing. |

**LeadCreate (key fields):** `name` (1-255), `phone` (1-32, normalized), `source` (1-100, lowercased), `email` (EmailStr opt), `language` (max 32, lowercased), `deal_value` (Decimal>=0 opt), `last_contact_at` (datetime opt), `custom_fields` (dict, max 50), `external_id` (max 128). `LeadRead.created_at` non-null; `deal_value` serialized as string "X.XX".

### 6.3 Lead Imports (CSV)
Router: `backend/app/api/imports.py` (prefix empty, tag `imports`) registered under `/api/v1` so paths are `/api/v1/leads/import`, `/api/v1/imports/...`. Auth required.

| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| POST | `/api/v1/leads/import` | X-API-Key | Header `Idempotency-Key` opt, multipart: `file` (CSV only), `mappings` (JSON string opt, URL-decoded) | 201 `ImportSummary` (sync) or 202 `ImportSummary` (async) | 401, 422 `malformed_file`, 500 `import_failed` | CSV filename must end with `.csv`. `mappings` parsed as dict mapping headers->canonical/`custom`/`ignore` or null; malformed JSON -> 422 `malformed_file`. Swagger sentinel `"string"` treated as empty. If `parsed.row_count > CALLORA_SYNC_PROCESSING_ROW_THRESHOLD` (default 1000) processed in background -> 202; else sync. Idempotency replay returns 200 + `Idempotency-Replayed: true` with batch status. POST response filtered to 7-field `ImportSummary`. On sync success, populates `agent_leads`, triggers CRM sync in background if leads created, publishes events. On sync processing failure -> 500 `import_failed` with details `import_id`/`status_url`. |
| GET | `/api/v1/imports/{import_id}` | X-API-Key | Path `import_id` | 200 `ImportStatusResponse` | 401, 404 `import_not_found` | Extends `ImportSummary` with `source_type`, `original_filename`, `mapping`, `error_message`, `created_at`, `started_at`, `completed_at`. |
| GET | `/api/v1/imports/{import_id}/errors` | X-API-Key | Path `import_id`, query `page`>=1 default 1, `page_size` 1..500 default 50 | 200 `ImportErrorListResponse` | 401, 404, 422 | Ordered by `row_number, id`. Returns `{import_id,total,page,page_size,items:[{row_number,field,error_code,message,raw_data}]}`. |

### 6.4 CRM Connections
Router: `backend/app/api/crm_connections.py` (prefix `/crm/connections`, tag `CRM Connections`).

| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| GET | `/api/v1/crm/connections` | X-API-Key | - | 200 `[CRMConnectionResponse]` | 401, 422 | Bare array. API key never returned; only `has_api_key`. |
| POST | `/api/v1/crm/connections` | X-API-Key | Body `CRMConnectionUpsertRequest` | 200 `CRMConnectionSaveResponse` | 401, 422, 502/503/504 | **Validates before save:** calls external CRM `GET /health` with supplied credentials. Failures: auth rejected -> 502 `crm_authentication_failed`; unreachable -> 503 `crm_unavailable`/504 `crm_timeout`; other -> 502 `crm_connection_validation_failed`. Nothing saved unless validation succeeds; verified only if check passed. Upsert by `business_id+connection_id` (default `connection_id="default"`). URL validated with SSRF guards (see `crm_sync/schemas.py`). |

**CRMConnectionUpsertRequest:** `connection_id` default "default" (1-100), `name` (1-255), `crm_base_url` (1-500, http(s), no embedded creds, SSRF validated), `crm_x_api_key` (1-500), `crm_timeout_seconds` 0-120 default 15. `crm_x_api_key` is external CRM credential (separate from Callora X-API-Key), stored server-side, never returned.

### 6.5 CRM Synchronization
Router: `backend/app/modules/crm_sync/router.py` (prefix `/crm/sync`, tag `CRM Synchronization`).

| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| POST | `/api/v1/crm/sync` | X-API-Key | Body `CRMSyncRequest` | 200 `CRMSyncResponse` | 401, 404 `lead_not_found`, 422, 409 `crm_idempotency_conflict`, 502/503/504 | Authenticates with Callora X-API-Key. Requires `request.lead.business_id == business_id` else 404 `lead_not_found`. Uses stored connection by `connection_id`. Always records honest status. Returns `sync_id`, `lead_id`, `business_id`, `status`, `external_crm_id` (opt), `message`. |

**CRMSyncRequest:** `connection_id` (string), `lead` (`LeadSyncInput`, `extra="forbid"`), `idempotency_key` opt. **LeadSyncInput:** `id,business_id,name,phone,source,created_at` required; others optional.

### 6.6 Agent Leads (Agent-Facing)
Router: `backend/app/api/agent_leads.py` (prefix `/agent/leads`, tag `Agent Leads`).

| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| GET | `/api/v1/agent/leads` | X-API-Key | Query `status` opt, `limit` 1-500 default 50, `offset`>=0 default 0 | 200 `{items, business_id, limit, offset}` | 401, 422 | Raw ORM-ish dicts/lists from agent storage. No `total`. |
| GET | `/api/v1/agent/leads/{lead_id}` | X-API-Key | Path `lead_id` | 200 record | 401, 404 `lead_not_found` (missing or cross-tenant) | Business-scoped. |
| GET | `/api/v1/agent/leads/by-external/{external_crm_id}` | X-API-Key | Path `external_crm_id` | 200 record | 401, 404 `lead_not_found` | Declared after `/{lead_id}` in source but resolves correctly due to literal segment matching. |

### 6.7 Recovery Agent (Detection/Sweep)
Router: `backend/app/modules/recovery_agent/router.py` (prefix `/agent/recovery`, tag `Recovery Agent`). **Registered BEFORE dashboard router** -> runtime shadowing affects same paths (see §6.8).

| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| POST | `/api/v1/agent/recovery/run` | X-API-Key (also passes raw key) | - | 200 result from `run_recovery_engine` | 401, 502 `recovery_leads_invalid_response`, 503 `recovery_leads_unavailable`, 504 `recovery_leads_timeout`, 422 | Fetches leads via `GET /api/v1/agent/leads` using caller's raw `X-API-Key`. Can use in-process ASGI transport if `CALLORA_RECOVERY_API_BASE_URL` unset, else network. Failures map to documented errors; sweep unaffected/retryable. |
| GET | `/api/v1/agent/recovery/cases` | X-API-Key | Query `status` opt, `limit` 1-500 default 50, `offset`>=0 default 0 | 200 `{items, business_id, limit, offset, ranking}` | 401, 422 | **Runtime:** uses recovery-agent handlers (shadowed). Items ranked by priority desc/recoverability desc/case id asc (Module 5). `ranking` summarizes ALL scored cases for business. **No `total`**. OpenAPI shows dashboard operation ID due to overwrite. Dashboard-only filters (`priority_level`, `query`, `needs_human_approval`) and dashboard `total` are **unreachable at runtime** on this path. |
| GET | `/api/v1/agent/recovery/cases/{case_id}` | X-API-Key | Path `case_id` | 200 case payload | 401, 404 `lead_not_found` | **Runtime shadowed** by dashboard; recovery-agent returns `_case_payload`. |

Case payload shape (recovery-agent): `case_id, lead_id, lead_name, business_id, score, priority_score, priority_rank, priority_level, score_breakdown, scored_at, signals, route, action, channel, message, rationale, engine, needs_human_approval, status, created_at, updated_at`.

### 6.8 Recovery Dashboard
Router: `backend/app/modules/recovery_dashboard/router.py` (prefix `/agent/recovery`, tag `Recovery Dashboard`). Registered AFTER recovery-agent router -> **shadows** `/cases` and `/cases/{case_id}` at runtime. Defines explicit `operation_id`s so OpenAPI documents dashboard versions.

| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| GET | `/api/v1/agent/recovery/dashboard/summary` | X-API-Key | - | 200 summary | 401, 422 | Shape: `totals` (`recovery_cases`, `eligible_leads`), `priority_counts` (`critical`/`high`/`medium`/`low`), `awaiting_outreach`, `outcomes` (`completed_calls`, `callback_requests`, `successful_recoveries`, `note`), `scoring` (`total_scored`, `ranking_order`). Outcomes derive only from persisted execution events. |
| GET | `/api/v1/agent/recovery/cases` | X-API-Key | Query `status`, `priority_level` (critical/high/medium/low else 422 `invalid_parameter`), `query`, `needs_human_approval` (bool), `limit` 1-500 default 50, `offset`>=0 default 0 | 200 `{items,business_id,limit,offset,total,ranking}` | 401, 422 `invalid_parameter` | **Documented in OpenAPI as dashboard** (operation_id `dashboard_list_recovery_cases`) but **unreachable at runtime** due to shadowing; recovery-agent handlers win. |
| GET | `/api/v1/agent/recovery/cases/{case_id}` | X-API-Key | Path `case_id` | 200 case dict | 401, 404 `lead_not_found` | **Runtime shadowed**; unreachable in practice. |
| GET | `/api/v1/agent/recovery/cases/{case_id}/score-breakdown` | X-API-Key | Path `case_id` | 200 breakdown | 401, 404 | Returns: `case_id, lead_id, priority_score, priority_level, scored_at, score_breakdown, explanation, ranking_reason` (explanation from breakdown if present). |
| GET | `/api/v1/agent/recovery/cases/{case_id}/outcomes` | X-API-Key | Path `case_id` | 200 outcomes | 401, 404 | Returns: `case_id, has_execution_data, counts, last_execution_at, note, events[]` where events have `id,event_type,channel,created_at,detail,engine`. |
| GET | `/api/v1/agent/recovery/cases/{case_id}/outreach-content` | X-API-Key | Path `case_id` | 200 content | 401, 404 | If cached -> `source:"cached"`. If absent -> **on-demand generation** (uses Groq integration with deterministic fallback). Returns: `case_id, source, generated_at, regenerated_at?, generation_count, engine, model, content`. |
| POST | `/api/v1/agent/recovery/cases/{case_id}/outreach-content/generate` | X-API-Key | Path `case_id` | 200 content | 401, 404, 409 `recovery_outreach_blocked` | Blocks if opted out/suppressed or no usable contact or lead recovered/closed-to-recovery. Returns generated content with `regenerated_at` and `regenerated` indicated in event detail. |

### 6.9 Recommendation Agent
Router: `backend/app/modules/recommendation_agent/router.py` (prefix `/recommendations`, tag `Recommendation Agent`). 13 operations.

| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| GET | `/api/v1/recommendations` | X-API-Key | Query `status`, `category`, `limit` 1-500 default 50, `offset`>=0 default 0 | 200 | 401, 422 | Returns `items, total, business_id` (no `limit/offset` echo). |
| GET | `/api/v1/recommendations/analytics` | X-API-Key | - | 200 analytics | 401, 422 | Includes counts, P1/P2/P3 counts, approved/implemented counts, `analyzed_calls`, `data_sufficiency`, `effectiveness_summary`. |
| GET | `/api/v1/recommendations/recovery` | X-API-Key | - | 200 | 401, 422 | **Bare array** of recovery opportunities. |
| GET | `/api/v1/recommendations/scripts/versions` | X-API-Key | - | 200 | 401, 422 | **Bare array** of script versions. |
| POST | `/api/v1/recommendations/generate` | X-API-Key | Body opt object `{}` (untyped) | 200 | 401, 422 | Accepts `time_window_days` 1..365, `force_refresh`. Many `schemas.py` models not used as response models. |
| GET | `/api/v1/recommendations/{recommendation_id}` | X-API-Key | Path `recommendation_id` | 200 detail | 401, 404, 422 | Includes `evidence`, `audit_history`, `script_suggestions`, `outcome`. |
| GET | `/api/v1/recommendations/{recommendation_id}/evidence` | X-API-Key | Path `recommendation_id` | 200 | 401, 422 | - |
| GET | `/api/v1/recommendations/{recommendation_id}/script-suggestions` | X-API-Key | Path `recommendation_id` | 200 | 401, 422 | - |
| POST | `/api/v1/recommendations/{recommendation_id}/evaluate` | X-API-Key | Body opt object | 200 | 401, 422 | Accepts `baseline_days`/`post_days` 1..365. |
| POST | `/api/v1/recommendations/{recommendation_id}/implement` | X-API-Key | Body opt object | 200 | 401, 422 | Accepts `actor`, `script_version_id`, `notes`. |
| POST | `/api/v1/recommendations/{recommendation_id}/script-suggestions/{suggestion_id}/approve` | X-API-Key | Body opt object | 200 | 401, 422 | **Approving creates an inactive version and does not activate it.** |
| POST | `/api/v1/recommendations/scripts/versions/{version_id}/activate` | X-API-Key | Body opt object | 200 | 401, 422 | - |
| PATCH | `/api/v1/recommendations/{recommendation_id}/status` | X-API-Key | Body object (required) | 200 | 401, 422 | Status transitions and script versions documented per implementation. |

### 6.10 Recovery Workflow
Router: `backend/app/modules/recovery_workflow/router.py` (prefix `/workflow`, tag `Recovery Workflow`). 32 operations. Uses source as contract for untyped payloads (no `schemas.py` relied on).

#### 6.10.1 Runs
| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| GET | `/api/v1/workflow/runs` | X-API-Key | Query `status`, `lead_id`, `limit` 1-200 default 50, `offset`>=0 default 0 | 200 | 401, 422 | List envelope typically omits `total`. |
| POST | `/api/v1/workflow/runs` | X-API-Key | Body object required | 201 | 401, 422 | **Deduplicates in-flight runs** for same `(business, lead, workflow_type)` unless `dry_run`. **Idempotency key replay returns existing run**. Approval policy reads rule `workflow.require_approval` (default enabled); **dry runs skip approval**. |
| GET | `/api/v1/workflow/runs/{run_id}` | X-API-Key | Path `run_id` | 200 | 401, 422 | - |
| POST | `/api/v1/workflow/runs/{run_id}/approve` | X-API-Key | Body opt object | 200 | 401, 422 | Transitions per state machine. |
| POST | `/api/v1/workflow/runs/{run_id}/cancel` | X-API-Key | Body opt object | 200 | 401, 422 | - |
| POST | `/api/v1/workflow/runs/{run_id}/execute` | X-API-Key | Body opt object | 200 | 401, 422 | Only from `approved`/`running`. Completed run returns unchanged. Each step appends one `WorkflowAction`. Blocked/failed step stops execution. Run ends `completed` even when a step is blocked with `result.blocked=true`. |
| POST | `/api/v1/workflow/runs/{run_id}/pause` | X-API-Key | Body opt object | 200 | 401, 422 | - |

**Run state machine:** `draft` -> `awaiting_approval`/`approved`/`cancelled`; `awaiting_approval` -> `approved`/`cancelled`; `approved` -> `running`/`cancelled`/`paused`; `running` -> `completed`/`failed`/`paused`/`cancelled`; `paused` -> `approved`/`cancelled`. Terminal states immutable.

#### 6.10.2 Eligibility
| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| POST | `/api/v1/workflow/eligibility` | X-API-Key | Body object required | 200 | 401, 422 | Returns `{business_id, lead_id, channel, eligible, reasons, checks, decision_id}`. `checks` = `[{name, outcome, reason_code?, reason, detail?}]`. `reasons` = `[{check, reason_code, reason}]`. |

#### 6.10.3 Appointments & Reminders
| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| GET | `/api/v1/workflow/appointments` | X-API-Key | Query `lead_id`, `status`, `limit` 1-200 default 50, `offset`>=0 default 0 | 200 | 401, 422 | Ordered newest `scheduled_start` first. Envelope `items,business_id,limit,offset`. |
| POST | `/api/v1/workflow/appointments` | X-API-Key | Body object required | 201 | 401, 422 | - |
| POST | `/api/v1/workflow/appointments/{appointment_id}/cancel` | X-API-Key | Path `appointment_id` | 200 | 401, 422 | - |
| GET | `/api/v1/workflow/reminders` | X-API-Key | Query `status`, `limit` 1-200 default 50, `offset`>=0 default 0 | 200 | 401, 422 | Envelope omits `limit/offset`. |
| POST | `/api/v1/workflow/reminders/dispatch` | X-API-Key | - | 200 | 401, 422 | **Cross-tenant mutation:** calls `dispatch_due_reminders(db, settings)` without business filter - processes all tenants' due reminders despite authenticated route. **Tenant-isolation discrepancy.** |

Reminders only scheduled for `confirmed` appointments using rule `reminders.enabled` and default offsets `[24, 1]` hours. `reminder_delivery_provider=none` reports `channel_not_configured`.

#### 6.10.4 Followups, Outcomes, Costs, Revenue
| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| GET | `/api/v1/workflow/followups` | X-API-Key | Query `status`, `lead_id`, `limit` 1-200 default 50, `offset`>=0 default 0 | 200 | 401, 422 | `items,business_id,limit,offset`. |
| POST | `/api/v1/workflow/followups/{task_id}/complete` | X-API-Key | Body opt object | 200 | 401, 422 | - |
| GET | `/api/v1/workflow/outcomes` | X-API-Key | Query `lead_id`, `outcome_type`, `limit` 1-200 default 50, `offset`>=0 default 0 | 200 | 401, 422 | - |
| POST | `/api/v1/workflow/outcomes` | X-API-Key | Body object required | 201 | 401, 422 | Requires `lead_id` + `outcome_type`. |
| POST | `/api/v1/workflow/costs` | X-API-Key | Body object required | 201 | 401, 422 | Requires `cost_type` + `amount`; amount cast to float. |
| POST | `/api/v1/workflow/revenue` | X-API-Key | Body object required | 201 | 401, 422 | Requires `amount`; amount cast to float. |

#### 6.10.5 Rules & Integrations
| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| GET | `/api/v1/workflow/rules` | X-API-Key | - | 200 | 401, 422 | - |
| PUT | `/api/v1/workflow/rules` | X-API-Key | Body object required | 200 | 401, 422 | Upsert requires `key`, `name`, `value`. |
| POST | `/api/v1/workflow/rules/evaluate` | X-API-Key | Body object required | 200 | 401, 422 | **Side-effect free**. |
| GET | `/api/v1/workflow/rules/{rule_key}/versions` | X-API-Key | Path `rule_key` | 200 | 401, 422 | - |
| GET | `/api/v1/workflow/integrations` | X-API-Key | - | 200 | 401, 422 | - |
| POST | `/api/v1/workflow/integrations` | X-API-Key | Body object required | 201 | 401, 422 | **Rejects config keys containing `secret`/`token`/`password`/`key`**. |
| POST | `/api/v1/workflow/integrations/{connection_id}/sync` | X-API-Key | Path `connection_id`, body opt object | 200 | 401, 422 | Validates `direction` in `push`/`pull`. Always records `failed` with `provider_not_configured` (no external adapter). |

#### 6.10.6 Analysis & Analytics
| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| POST | `/api/v1/workflow/analysis` | X-API-Key | Body object required | 200 | 401, 422 | Requires `call_id`. |
| POST | `/api/v1/workflow/analysis/bulk` | X-API-Key | Body opt object | 200 | 401, 422 | Accepts optional `limit` default 100. Returns `{analysed, skipped_no_transcript}`. |
| GET | `/api/v1/workflow/analytics/funnel` | X-API-Key | Query `days` 1-365 default 30 | 200 | 401, 422 | - |
| GET | `/api/v1/workflow/analytics/leakage` | X-API-Key | Query `days` 1-365 default 30 | 200 | 401, 422 | - |
| GET | `/api/v1/workflow/analytics/roi` | X-API-Key | Query `days` 1-365 default 90 | 200 | 401, 422 | - |
| POST | `/api/v1/workflow/analytics/record-call-costs` | X-API-Key | Query `days` 1-365 default 30 | 200 | 401, 422 | Returns `{"created": n}`. |

**Workflow details:** Step types `eligibility_check`, `voice_call`, `message`, `followup_task`, `book_appointment`. Message steps return `channel_not_configured`. Default steps: `recovery_call` = eligibility_check(voice), voice_call, followup_task(48h); `recovery_message` = eligibility_check(sms), message(sms), followup_task(72h); `recovery_booking` = eligibility_check(voice), book_appointment, followup_task(24h). Booking requires `params.scheduled_start`; missing -> failed step with `invalid_request`. Internal booking confirms internally; other providers record `failed`/`provider_not_configured`. Integration sync always `failed`/`provider_not_configured`. Analysis: rules engine `engine="rules"`, `model=None`; repeated unchanged transcript returns existing row. Single analysis shape `_analysis_dict` includes `id,business_id,call_id,lead_id,transcript_hash,analysis,objections,buying_signals,leakage_flags,sentiment,intent,engine,model,analyzed_at`.

### 6.11 Voice — Business
Router: `backend/app/modules/voice/api/businesses.py` (prefix `/voice`, tag `Voice — Business`). Auth required.

| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| GET | `/api/v1/voice/business` | X-API-Key | - | 200 `BusinessProfile` | 401, 422 | Config lazily created on first GET. Defaults from `CALLORA_VOICE_*` settings. |
| PATCH | `/api/v1/voice/business` | X-API-Key | Body `BusinessProfileUpdate` | 200 `BusinessProfile` | 401, 422 | Excludes unset and null fields. Changes audited with field names. |

**BusinessProfile (key):** `business_id, name, timezone, default_language, supported_languages, calling_window_start,end, calling_days, max_calls_per_lead_per_day, max_calls_per_business_per_day, max_concurrent_calls, require_consent, dnd_enabled, bolna_agent_id, agent_instructions, qualification_questions (array of objects), warm_transfer_enabled, warm_transfer_number, handoff_target_number, is_active, outbound_calls_enabled, provider_configured, call_quotas_disabled, created_at, updated_at`. **BusinessProfileUpdate:** partial; calling days 0-6 (Mon..Sun), sorted/deduped; languages lowercased/stripped; quotas 1-50/1-100000/1-1000.

### 6.12 Voice — Leads, Consent, DND
Router: `backend/app/modules/voice/api/leads.py` (prefix `/voice`, tag `Voice — Leads`).

| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| GET | `/api/v1/voice/leads` | X-API-Key | Query `limit` 1-500 default 50, `offset`>=0 default 0 | 200 | 401, 422 | Standard list envelope; **no `total`** (pagination discrepancy noted). |
| GET | `/api/v1/voice/leads/{lead_id}` | X-API-Key | Path `lead_id` | 200 | 401, 422 | Voice lead profile. |
| PUT | `/api/v1/voice/leads/{lead_id}/profile` | X-API-Key | Path `lead_id`, body `VoiceLeadProfileUpdate` | 200 | 401, 422 | Only `preferred_language, timezone, do_not_call, notes`. |
| GET | `/api/v1/voice/leads/{lead_id}/consent` | X-API-Key | Path `lead_id` | 200 `[ConsentRecordResponse]` | 401, 422 | **Bare array** of consent records. |
| PUT | `/api/v1/voice/leads/{lead_id}/consent` | X-API-Key | Path `lead_id`, body `ConsentRecordRequest` | 200 `ConsentRecordResponse` | 401, 422 | Requires valid `status` (granted/revoked/expired) and `channel` (voice/sms/email/whatsapp). Datetimes normalized to UTC. |
| GET | `/api/v1/voice/dnd` | X-API-Key | Query `limit` 1-500 default 50, `offset`>=0 default 0 | 200 | 401, 422 | **In-memory pagination** (`entries[offset:offset+limit]`), omits `total`. |
| POST | `/api/v1/voice/dnd` | X-API-Key | Body `DndEntryCreate` | 201 `DndEntryResponse` | 401, 422 | `scope` `business`/`global` (default business), phone 3-32. |
| DELETE | `/api/v1/voice/dnd/{entry_id}` | X-API-Key | Path `entry_id` | 200 | 401, 422 | Looks up only **active** entries from tenant list; returns **200 and the inactive entry** (not 204). |

### 6.13 Voice — Calls
Router: `backend/app/modules/voice/api/calls.py` (prefix `/voice`, tag `Voice — Calls`).

| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| GET | `/api/v1/voice/calls` | X-API-Key | Query `status`, `lead_id`, `limit` 1-500 default 50, `offset`>=0 default 0 | 200 `CallListResponse` | 401, 422 | `{items:[CallResponse], business_id, limit, offset}`. |
| POST | `/api/v1/voice/calls` | X-API-Key | Header `Idempotency-Key` opt, body `CallInitiationRequest` | 201 `CallInitiationResponse` | 401, 403 `compliance_blocked`, 409 `idempotency_conflict`, 422 `invalid_parameter`, 429, 503 | **Always persists compliance decision.** Blocked calls return 403 `compliance_blocked`. If `CALLORA_VOICE_ENABLE_OUTBOUND_CALLS=false` calls are `simulated`. Idempotency: key >128 chars -> 422 `invalid_parameter`; same key + different payload -> 409 `idempotency_conflict`. Rate limit Redis-advisory; unreachable Redis allows request. |
| GET | `/api/v1/voice/calls/{call_id}` | X-API-Key | Path `call_id` | 200 `CallResponse` | 401, 422 | - |
| POST | `/api/v1/voice/calls/{call_id}/cancel` | X-API-Key | Path `call_id` | 200 `CallStatusResponse` | 401, 422 `invalid_parameter` | Only allowed from `queued`, `scheduled`, or `pending`; otherwise 422. |
| GET | `/api/v1/voice/calls/{call_id}/events` | X-API-Key | Path `call_id`, query `limit` 1-500 default 100, `offset`>=0 default 0 | 200 `[CallEventResponse]` | 401, 422 | Bare array. |
| GET | `/api/v1/voice/calls/{call_id}/outcome` | X-API-Key | Path `call_id` | 200 `CallOutcomeResponse` | 401, 404 `call_outcome_not_found` | **GET only; POST does not exist.** 404 before post-call processing. |
| POST | `/api/v1/voice/calls/{call_id}/postcall` | X-API-Key | Path `call_id` | 202 | 401, 422 | Dedupes by `postcall_dedupe_key(call.id, status_key)` where non-terminal calls use `"manual"`. Returns existing job with `created:false`. |
| POST | `/api/v1/voice/calls/{call_id}/refresh` | X-API-Key | Path `call_id` | 200 `CallStatusResponse` | 401, 422 | If call has no `provider_call_id` returns `refreshed:false` with message, HTTP 200. |

**Call statuses (documented):** `blocked`, `simulated`, `pending`, `scheduled`, `queued`, `initiated`, `ringing`, `in_progress`, `disconnected`, `completed`, `no_answer`, `busy`, `failed`, `canceled`, `stopped`, `error`, `balance_low`; unknown provider status is `unknown` and never written to a call. **CallResponse.simulated** derived from `metadata["simulated"]`; `metadata` returned under `metadata`. **CallInitiationRequest:** `lead_id` 1-32, `language` max 16, `from_number` max 32, `agent_id` max 128, `extra_context` object, `dry_run` default false. **CallResponse:** includes provider fields, status/blocked/error, numbers, direction default outbound, timestamps, duration/cost/currency default INR, recording/transcript/summary, compliance id, `simulated`, `metadata`, timestamps. **CallOutcomeResponse:** summary/disposition/qualification/refusal/follow-up/handoff/language/model/engine/timestamps.

### 6.14 Voice — Handoffs/Callbacks
Router: `backend/app/modules/voice/api/handoffs.py` (prefix `/voice`, tag `Voice — Handoffs`).

| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| GET | `/api/v1/voice/handoffs` | X-API-Key | Query `status`, `kind`, `limit` 1-500 default 50, `offset`>=0 default 0 | 200 | 401, 422 | - |
| POST | `/api/v1/voice/handoffs` | X-API-Key | Body `CallbackRequest` | 201 `HandoffResponse` | 401, 404 `call_not_found`/`lead_not_found`, 422 | Requires `lead_id` or `call_id`. Invalid `call_id` -> 404 `call_not_found`; invalid `lead_id` -> 404 `lead_not_found`. |
| GET | `/api/v1/voice/handoffs/{handoff_id}` | X-API-Key | Path `handoff_id` | 200 `HandoffResponse` | 401, 422 | - |
| PATCH | `/api/v1/voice/handoffs/{handoff_id}` | X-API-Key | Path `handoff_id`, body `HandoffUpdate` | 200 `HandoffResponse` | 401, 422 `invalid_parameter` | Can set `outcome` and/or `status`. Status validation via `HandoffService.set_status`; invalid status -> 422. |

### 6.15 Voice — Knowledge Base
Router: `backend/app/modules/voice/api/knowledge.py` (prefix `/voice`, tag `Voice — Knowledge Base`).

| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| GET | `/api/v1/voice/knowledge` | X-API-Key | Query `category`, `is_active` (bool), `limit` 1-500 default 100, `offset`>=0 default 0 | 200 `KnowledgeItemListResponse` | 401, 422 | **`total` = page length** (`len(serialized)`) not full count (pagination discrepancy). |
| POST | `/api/v1/voice/knowledge` | X-API-Key | Body `KnowledgeItemCreate` | 201 `KnowledgeItemResponse` | 401, 422 | Defaults: category `faq`, source_reference `"Verified Business Knowledge"`, active true, priority 0. |
| POST | `/api/v1/voice/knowledge/batch` | X-API-Key | Body `KnowledgeItemBatchCreate` | 201 `KnowledgeItemListResponse` | 401, 422 | Returns list envelope with `total` equal to created count. |
| GET | `/api/v1/voice/knowledge/{item_id}` | X-API-Key | Path `item_id` | 200 `KnowledgeItemResponse` | 401, 422 | - |
| PUT | `/api/v1/voice/knowledge/{item_id}` | X-API-Key | Path `item_id`, body `KnowledgeItemUpdate` | 200 `KnowledgeItemResponse` | 401, 422 | All fields optional. |
| DELETE | `/api/v1/voice/knowledge/{item_id}` | X-API-Key | Path `item_id` | 204 | 401, 422 | Returns 204. |

### 6.16 Voice — Operations
Router: `backend/app/modules/voice/api/ops.py` (prefix `/voice`, tag `Voice — Operations`).

| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| GET | `/api/v1/voice/ops/stats` | X-API-Key | - | 200 `VoiceStatsResponse` | 401, 422 | Includes `calls_total`, `calls_by_status`, `calls_today`, `blocked_today`, `handoffs_open`, consent counts, DND/job counts, `total_cost` (default 0.0), `currency` (default INR). |
| GET | `/api/v1/voice/ops/jobs` | X-API-Key | Query `status`, `type`, `limit` 1-500 default 50, `offset`>=0 default 0 | 200 | 401, 422 | - |
| GET | `/api/v1/voice/ops/jobs/{job_id}` | X-API-Key | Path `job_id` | 200 `JobResponse` | 401, 422 | Jobs with `business_id=None` (global jobs) accessible by any authenticated tenant (tenancy edge case). |
| POST | `/api/v1/voice/ops/jobs/{job_id}/retry` | X-API-Key | Path `job_id` | 200 `JobResponse` | 401, 422 | Same tenancy edge case for global jobs. |
| GET | `/api/v1/voice/ops/audit` | X-API-Key | Query `action`, `entity_id`, `limit` 1-500 default 50, `offset`>=0 default 0 | 200 | 401, 422 | List envelope. |
| GET | `/api/v1/voice/ops/compliance-decisions` | X-API-Key | Query `lead_id`, `limit` 1-500 default 50, `offset`>=0 default 0 | 200 | 401, 422 | List envelope. |
| POST | `/api/v1/voice/ops/sync-calls` | X-API-Key | - | 200 `StatusSyncResponse` | 401, 422 | Returns `business_id`, `checked`, `applied`, `unchanged`, `skipped`, `failed`, `outcomes[]` with detailed outcomes (applied/unchanged/out_of_order/unknown_provider_status/provider_unreachable/provider_error/execution_missing/timeout). |

### 6.17 Voice — Follow-Up Questions
Router: `backend/app/modules/voice/api/followup.py` (prefix `/voice`, tag `Voice — Follow-Up Questions`).

| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| POST | `/api/v1/voice/answer-question` | Mixed (see §3.3) | Headers `x-api-key` opt, `authorization` opt; body `FollowUpQuestionRequest` | 200 `FollowUpQuestionResponse` | 400, 401, 403, 404, 422 | **Weaker auth:** business API keys accepted; also trusts provider-signature headers or `execution_id`+`business_id` even without key (if key supplied must match). Resolves business from `call_id` or provider `execution_id`. Returns grounded answers with handoff flags. `FollowUpQuestionResponse` auto-populates `response`/`text` from `answer`. |

**FollowUpQuestionRequest:** requires one of `question`, `query`, or `text`. Max lengths: `business_id` 64, `call_id`/`execution_id` 64/128, `question/query/text` 1000. `FollowUpQuestionResponse`: `answer` required; `response`,`text` null; `source_references` array; handoff flags/type/id; `action` default `answer`; `confidence` default 1.0; `qualification_continuation`, `memory_state`.

### 6.18 Voice — Bolna Custom Functions (Tools)
Router: `backend/app/modules/voice/api/tools.py` (prefix `/voice/tools`, tag `Voice — Bolna Custom Functions`). Auth: Bearer token OR `X-API-Key` (business key or `CALLORA_VOICE_TOOL_SECRET`); empty secret disables. All return **flat tool envelope** (see §4.2). 6 tools.

| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| POST | `/api/v1/voice/tools/lead-details` | Tool auth | Body `LeadDetailsRequest` | 200 `{"success":true,"message":"...","data":{...}}` | 401, 422 (tool envelope), 500 `INTERNAL_ERROR` | Returns lead/profile/qualification data. |
| POST | `/api/v1/voice/tools/qualify-lead` | Tool auth | Body `QualifyLeadRequest` | 200 tool envelope | 401, 422, 500 | Evaluates answers against rules. |
| POST | `/api/v1/voice/tools/save-outcome` | Tool auth | Body `SaveOutcomeRequest` | 200 tool envelope | 401, 422, 500 | Persists outcome; maps to lead-status vocabulary. **Idempotency key** derived as `execution_id` or `call_id` or `lead:{lead_id}:{outcome}`; replay returns success with `replayed:true`. Inside save-outcome, compliance failure returns `scheduled:false` with error but still saves outcome. |
| POST | `/api/v1/voice/tools/create-opportunity` | Tool auth | Body `CreateOpportunityRequest` | 200 tool envelope | 401, 422, 500 | Refuses `force=true` unless authenticated with a **business API key**. Service returns `QUALIFICATION_NOT_MET` in nested error envelope when rules fail. |
| POST | `/api/v1/voice/tools/schedule-callback` | Tool auth | Body `ScheduleCallbackRequest` | 200 tool envelope | 401, 422, 500 | Rejects unresolvable timezone, past times (>1 min), times beyond `CALLORA_VOICE_CALLBACK_MAX_DAYS_AHEAD`. Naive times interpreted in supplied timezone, stored UTC. Compliance blocks raise errors; callback compliance blocks handled. Success returns `scheduled:true`, `callback_id`, `status`, `requested_at` (UTC ISO), `timezone`, `dispatch` (`pending_manual` or `queued`/`already_queued` with `job_id`). |
| POST | `/api/v1/voice/tools/crm-sync` | Tool auth | Body `CrmSyncToolRequest` | 200 tool envelope | 401, 422, 500 | Translates missing/inactive CRM connection into `CrmNotConfiguredError`. Preserves CRM module errors in tool envelope. Returns `lead_id`, `sync_id`, `status`, `external_crm_id`, `connection_id`. |

**ToolModel** uses `extra="ignore"`. **Tool outcome:** controlled set mapped to lead-status vocabulary. **Other tool success payloads:** lead-details returns lead/profile/qualification data; save-outcome returns `lead_id`, `outcome`, `lead_status`, `lead_status_applied`, optional `qualification`/`callback`; qualify returns `lead_id`, `qualification`, optional `lead_status`; opportunity returns `lead_id`, `opportunity_id`, `status`, `created`, optional `lead_status`; callback returns `scheduled`, `callback_id`, `status`, `requested_at`, `timezone`, `dispatch`.

### 6.19 Voice — Webhooks (Bolna)
Router: `backend/app/modules/voice/api/webhooks.py` (prefix `/voice/webhooks/bolna`, tag `Voice — Webhooks`). **No tenant API key.** Optional HMAC via `CALLORA_BOLNA_WEBHOOK_SECRET`. Duplicates/out-of-order events return 2xx.

| Method | Path | Auth | Body/Params | Success | Errors | Notes |
|--------|------|------|-------------|---------|--------|-------|
| POST | `/api/v1/voice/webhooks/bolna/execution` | None (webhook) | Provider payload | 200 | 401 `webhook_signature_invalid` (if signature fails), 400 `webhook_payload_invalid` (unparseable) | Processes execution events. |
| POST | `/api/v1/voice/webhooks/bolna/pre-transfer` | None | Provider payload | 200 | 401, 400 | - |
| POST | `/api/v1/voice/webhooks/bolna/followup` | None | Provider payload | 200 | 401, 400 | **Returns provider-shaped spoken answer dict** (not `WebhookResult`), with `status`, `call_id`, `execution_id`, `signature_valid`, `response`/`answer`/`text`, `source_references`, handoff fields, `action`, `confidence`, `qualification_continuation`, `memory_state`. |
| GET | `/api/v1/voice/bolna/custom-functions` | None | - | 200 object | - | Six `custom_task` definitions: `get_lead_details`, `save_call_outcome`, `qualify_lead`, `create_opportunity`, `schedule_callback`, `sync_lead_to_crm`. Includes placeholder bearer token, paths, `configured`. |
| GET | `/api/v1/voice/bolna/tool-definition` | None | - | 200 `BolnaToolDefinitionResponse` | - | OpenAI tool schema, Bolna custom task, webhook URL, API URL. |

**Webhook result statuses:** `processed`, `duplicate`, `unmatched`, `ignored_out_of_order`.

---

## 7. Schemas (Key Types)

Key schemas from OpenAPI/source (see `/tmp/schemas.txt` for full list):

| Schema | Notes |
|--------|-------|
| `LeadCreate` | name/phone/source required; business_id absent (from auth). Normalized/validated. |
| `LeadRead` | id,business_id,name,phone,source,created_at required; `deal_value` serialized as string "X.XX". |
| `LeadListResponse` | items, total, page, page_size |
| `ImportSummary` | import_id,status,total_rows,created,duplicates,rejected,errors_url (7 fields) |
| `ImportStatusResponse` | extends ImportSummary + source_type, original_filename, mapping, error_message, timestamps |
| `ImportErrorListResponse` | import_id,total,page,page_size,items[] |
| `CRMConnectionUpsertRequest` | name, crm_base_url, crm_x_api_key required; connection_id default "default" |
| `CRMConnectionResponse` | has_api_key only, never api key |
| `CRMConnectionSaveResponse` | connection, verified, message |
| `CRMSyncRequest` | connection_id, lead, idempotency_key opt |
| `CRMSyncResponse` | sync_id,lead_id,business_id,status,external_crm_id,message |
| `BusinessProfile` | full voice config; includes `outbound_calls_enabled`, `provider_configured`, `call_quotas_disabled` |
| `BusinessProfileUpdate` | partial updates with validation |
| `CallInitiationRequest` | lead_id required, many opts |
| `CallInitiationResponse` | call, compliance_decision_id, compliance_decision, compliance_checks, simulated, message |
| `CallResponse` | full call state; `simulated` derived from metadata; `metadata` object |
| `CallOutcomeResponse` | post-call intelligence |
| `CallListResponse` | items, business_id, limit, offset |
| `ConsentRecordRequest/Response` | consent tracking |
| `DndEntryCreate/Response` | DND registry |
| `HandoffResponse/Update` | handoffs/callbacks |
| `KnowledgeItemCreate/Update/Response/ListResponse` | knowledge base |
| `FollowUpQuestionRequest/Response` | follow-ups; response auto-populated |
| `JobResponse` | background jobs |
| `AuditLogResponse` | audit logs |
| `ComplianceDecisionResponse` | compliance decisions |
| `VoiceStatsResponse` | stats with totals/costs |
| `BolnaToolDefinitionResponse` | tool definition |
| `HTTPValidationError` | standard FastAPI validation |
| Tool schemas (`LeadDetailsRequest`, `QualifyLeadRequest`, `SaveOutcomeRequest`, `CreateOpportunityRequest`, `ScheduleCallbackRequest`, `CrmSyncToolRequest`) use `ToolModel` with `extra="ignore"` in general; voice API models use `extra="forbid"` where noted.

---

## 8. Error & Status Reference

### Common Codes (standard envelope)
| Code | Status | When |
|------|--------|------|
| `authentication_failed` | 401 | Missing/invalid X-API-Key |
| `lead_not_found` | 404 | Lead missing or cross-tenant (also used by some voice ops) |
| `import_not_found` | 404 | Import batch not found or cross-tenant |
| `call_not_found` | 404 | Handoff creation with invalid call_id |
| `call_outcome_not_found` | 404 | GET outcome before processing exists? |
| `crm_connection_not_found` | 404 | Connection not found/inactive |
| `payload_too_large` | 413 | - |
| `row_limit_exceeded` | 413 | - |
| `unsupported_media_type` | 415 | - |
| `conflict` | 409 | Duplicate phone on POST /leads |
| `crm_idempotency_conflict` | 409 | Same idempotency key different payload (CRM) |
| `idempotency_conflict` | 409 | Voice call initiation same key different payload |
| `recovery_outreach_blocked` | 409 | Outreach blocked for case |
| `malformed_file` | 422 | Bad CSV/mappings JSON or format |
| `request_validation_error` | 422 | Pydantic validation failure |
| `invalid_parameter` | 422 | Domain validation (e.g. priority_level, handoff status, call cancel invalid state, idempotency key >128) |
| `crm_connection_inactive` | 400 | - |
| `import_failed` | 500 | Sync import processing failure |
| `internal_error` | 500 | Unhandled exception |
| `crm_authentication_failed` | 502 | CRM auth rejected on validation/sync |
| `crm_connection_validation_failed` | 502 | CRM validation failed (other) |
| `crm_sync_failed` | 502 | CRM sync failure |
| `recovery_leads_invalid_response` | 502 | Agent-leads API bad payload |
| `crm_unavailable` | 503 | CRM unreachable |
| `recovery_leads_unavailable` | 503 | Agent-leads API unreachable |
| `crm_timeout` | 504 | CRM timeout |
| `recovery_leads_timeout` | 504 | Agent-leads API timeout |

### Voice-specific
| Code/Status | Notes |
|-------------|-------|
| 403 `compliance_blocked` | POST /api/v1/voice/calls blocked by compliance; decision persisted |
| 429 | Rate limited (call initiation) |
| 503 | Rate limit/store issues or provider issues as applicable |
| Tool errors | Flat envelope with uppercased codes (e.g. `REQUEST_VALIDATION_ERROR`, `INTERNAL_ERROR`, `QUALIFICATION_NOT_MET` in nested error context) |

### Webhooks
| Code/Status | Notes |
|-------------|-------|
| 401 `webhook_signature_invalid` | Signature verification failed |
| 400 `webhook_payload_invalid` | Unparseable body |
| 2xx | Duplicates/out-of-order return 2xx (processed as duplicate/ignored_out_of_order) |

---

## 9. Uploads, Webhooks & Integrations

### Uploads
- **CSV import:** `multipart/form-data` with `file` (must be `.csv`) and optional `mappings` (JSON string). Max upload size controlled by `CALLORA_MAX_UPLOAD_SIZE_MB` (default 10, 1-100). Max rows `CALLORA_MAX_ROWS_PER_IMPORT` (default 50000). Threshold for async `CALLORA_SYNC_PROCESSING_ROW_THRESHOLD` (default 1000). Malformed mappings -> 422 `malformed_file`. Parse errors use `MalformedFileError`. Sync processing failure -> 500 `import_failed` with details.
- **Custom fields:** up to `CALLORA_MAX_CUSTOM_FIELDS` (default 50). Policy controlled by `CALLORA_AUTO_CAPTURE_CUSTOM_FIELDS` and `CALLORA_CUSTOM_FIELD_ALLOWLIST`.

### Webhooks
- **Bolna webhooks:** unauthenticated, optional HMAC via `CALLORA_BOLNA_WEBHOOK_SECRET`. Endpoints under `/api/v1/voice/webhooks/bolna/{execution,pre-transfer,followup}`. Followup returns spoken-answer dict. Duplicates/out-of-order -> 2xx with statuses `duplicate`/`ignored_out_of_order`.

### Integrations
- **CRM:** External CRM validated via `GET /health` on connection save; URLs SSRF-guarded. API keys stored server-side. Sync always records status.
- **Voice Provider (Bolna):** Configured via `CALLORA_BOLNA_*` settings. `CALLORA_VOICE_ENABLE_OUTBOUND_CALLS` kill switch (default false) - when false calls are simulated after compliance gate.
- **Workflow Integrations:** Created via workflow API; config keys containing `secret/token/password/key` rejected. Direction must be `push`/`pull`. Sync records `failed` with `provider_not_configured` (no external adapter). 
- **Reminder dispatch:** `POST /api/v1/workflow/reminders/dispatch` processes **all tenants** (no business filter) - see §15.3.
- **Agent Leads client:** Recovery agent can fetch via in-process ASGI or network base URL.

---

## 10. Frontend Integration Guidance

> **Note:** No frontend directory exists in the repository. Integrate framework-agnostically; React + TypeScript examples below are illustrative patterns matching the API.

### General
- **Base URL:** `/api/v1` (or full origin). Core at `/` and `/health`.
- **Auth:** Send `X-API-Key: <your-api-key>` on authenticated endpoints. Never expose keys in client-side code; use backend proxy or server-side calls.
- **Content-Type:** `application/json` for JSON; `multipart/form-data` for CSV upload.
- **Errors:** Handle standard envelope `{"error":{"code","message","details"}}` and tool envelope for voice tools.
- **Idempotency:** Use `Idempotency-Key` header for `POST /api/v1/leads` and `POST /api/v1/leads/import` and `POST /api/v1/voice/calls` where supported.

### Key Flows
1. **CSV Import:** POST multipart to `/api/v1/leads/import` with `file` and optional `mappings`. If 202, poll `GET /api/v1/imports/{import_id}` until status complete/failed; fetch errors via `/imports/{import_id}/errors`.
2. **Create Lead (JSON):** POST `/api/v1/leads` with `LeadCreate`; handle 409 duplicate.
3. **CRM Setup:** POST `/api/v1/crm/connections` to validate+save external CRM; then POST `/api/v1/crm/sync`.
4. **Recovery Dashboard:** Note runtime uses recovery-agent handlers for `/cases` (no total, different envelope) vs dashboard shape - see §15.2.
5. **Voice Calls:** POST `/api/v1/voice/calls` (compliance-gated). Poll GET call or use webhooks. Check outcome via GET `/api/v1/voice/calls/{call_id}/outcome`.
6. **Workflow:** Create run via POST `/api/v1/workflow/runs`, approve if needed, execute. Check eligibility first.

### Pagination
- Use `limit/offset` where provided. Be aware of discrepancies (some lists omit `total`).

---

## 11. Workflows

### 11.1 Lead Import (CSV)
1. Upload CSV to `POST /api/v1/leads/import` with optional mappings
2. Small files processed sync (201), large -> background (202)
3. Leads persisted, `agent_leads` upserted, outbox events enqueued
4. Automatic CRM sync triggered in background if connection exists
5. Poll `GET /api/v1/imports/{import_id}` for status; get errors if any

### 11.2 Voice Call Initiation
1. Ensure business configured (`GET /api/v1/voice/business`), lead exists
2. `POST /api/v1/voice/calls` with `lead_id` (compliance gate runs; decision persisted)
3. If blocked -> 403 `compliance_blocked`. If `VOICE_ENABLE_OUTBOUND_CALLS=false` -> simulated
4. Provider integration via Bolna (if enabled); webhooks update status
5. Post-call processing may queue job; outcome available via GET outcome

### 11.3 Recovery Sweep
1. `POST /api/v1/agent/recovery/run` (uses caller's API key to fetch agent leads)
2. Engine detects opportunities, creates/updates `recovery_cases` with scoring/priority
3. View via recovery-agent list (runtime) or dashboard summary
4. Generate outreach content as needed

### 11.4 Workflow Run
1. Create run `POST /api/v1/workflow/runs` (may deduplicate if in-flight; approval policy applies; dry runs skip approval)
2. If needs approval -> `awaiting_approval`, approve via `POST /api/v1/workflow/runs/{run_id}/approve`
3. Execute via `POST /api/v1/workflow/runs/{run_id}/execute` (from approved/running)
4. Steps execute in order; each appends action; blocked/failed stops; run completes even if blocked with `blocked=true`
5. Track appointments/followups/reminders/outcomes

---

## 12. TypeScript Interfaces & Client Patterns

### Auth & Base
```typescript
const API_BASE = '/api/v1';
const apiKey = '<your-api-key>';

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'X-API-Key': apiKey,
      'Content-Type': 'application/json',
      ...(init.headers || {}),
    },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw err;
  }
  return res.json();
}
```

### Key Types (illustrative)
```typescript
interface LeadRead {
  id: string;
  business_id: string;
  name: string;
  phone: string;
  email?: string | null;
  source: string;
  language?: string | null;
  deal_value?: string | null; // "X.XX"
  last_contact_at?: string | null;
  custom_fields: Record<string, any>;
  external_id?: string | null;
  created_at: string;
}

interface ImportSummary {
  import_id: string;
  status: string;
  total_rows: number;
  created: number;
  duplicates: number;
  rejected: number;
  errors_url: string;
}

interface CallResponse {
  id: string;
  business_id: string;
  lead_id: string;
  provider: string;
  provider_call_id?: string | null;
  status: string;
  blocked_reason?: string | null;
  error_message?: string | null;
  to_number?: string | null;
  from_number?: string | null;
  language?: string | null;
  direction: string;
  simulated: boolean;
  metadata: Record<string, any>;
  // ... timestamps/costs/etc
}

interface ToolResponse<T = any> {
  success: boolean;
  message?: string;
  data?: T;
  error?: { code: string; message: string };
}
```

### CSV Upload
```typescript
async function importCsv(file: File, mappings?: object) {
  const form = new FormData();
  form.append('file', file);
  if (mappings) form.append('mappings', JSON.stringify(mappings));
  const res = await fetch(`${API_BASE}/leads/import`, {
    method: 'POST',
    headers: { 'X-API-Key': apiKey },
    body: form,
  });
  return res.json();
}
```

---

## 13. DB Entities (Relevant)

From `backend/app/models.py` and usage (high-level):
- `Lead` (business-scoped leads)
- `ImportBatch`, `ImportRowError`
- `IdempotencyKey`
- `CRMConnection`
- `AgentLead` (agent storage)
- `RecoveryCase` (with priority/scoring fields)
- `RecoveryOutreachContent`, `RecoveryOutreachEvent`
- Voice models: `VoiceBusinessProfile`, `VoiceCall`, `VoiceCallEvent`, `VoiceCallOutcome`, `VoiceConsentRecord`, `VoiceDndEntry`, `VoiceHandoff`, `VoiceKnowledgeItem`, `VoiceJob`, `VoiceAuditLog`, `VoiceComplianceDecision`, `VoiceLeadProfile`
- Workflow models in `modules/recovery_workflow/models.py`: `WorkflowRule`, `WorkflowRun`, `WorkflowStep`, `WorkflowAction`, `WorkflowAppointment`, `WorkflowReminder`, `WorkflowFollowupTask`, `WorkflowOutcome`, `WorkflowCost`, `WorkflowRevenue`, `WorkflowIntegration`, `WorkflowAnalysis`

---

## 14. Environment Variables

Key settings (see `config.py` and `.env.example`):

### Core
- `CALLORA_DATABASE_URL` (default postgres)
- `CALLORA_API_KEYS` (default `biz_001:dev-key-biz-001`)
- `CALLORA_ENVIRONMENT` (default `production`)
- `CALLORA_DEBUG` (default false)
- `CALLORA_LOG_LEVEL` (default INFO)
- `CALLORA_IGNORE_DOTENV` (test use)

### Import
- `CALLORA_MAX_UPLOAD_SIZE_MB` (10, 1-100)
- `CALLORA_MAX_ROWS_PER_IMPORT` (50000)
- `CALLORA_SYNC_PROCESSING_ROW_THRESHOLD` (1000)
- `CALLORA_DEFAULT_LEAD_SOURCE` (import)
- `CALLORA_AUTO_CAPTURE_CUSTOM_FIELDS` (true)
- `CALLORA_MAX_CUSTOM_FIELDS` (50)

### CRM
- `CALLORA_CRM_BASE_URL`, `CALLORA_CRM_API_KEY` (deprecated/legacy), `CALLORA_CRM_TIMEOUT_SECONDS` (15), `CALLORA_CRM_BUSINESS_NAME`, `CALLORA_CRM_ALLOW_PRIVATE_URLS` (false), `CALLORA_CRM_DEV_ALLOWED_DESTINATIONS`

### Recovery Agent/Dashboard
- `CALLORA_RECOVERY_SLA_MINUTES` (120), `CALLORA_RECOVERY_STALL_DAYS` (7), `CALLORA_RECOVERY_SCORE_THRESHOLD` (0.40), `CALLORA_RECOVERY_MAX_ATTEMPTS` (3), `CALLORA_RECOVERY_ENFORCE_QUIET_HOURS` (false), quiet hours, `CALLORA_RECOVERY_API_BASE_URL`, `CALLORA_RECOVERY_FETCH_PAGE_SIZE`, timeouts, opt-out/ignored/recovered statuses
- Priority: weights and thresholds, `CALLORA_PRIORITY_INTENT_FLAGS`
- Outreach: `CALLORA_AGENT_DISPLAY_NAME`, `CALLORA_DEFAULT_OUTREACH_LANGUAGE` (english)
- LLM: `GROQ_API_KEY` / `CALLORA_GROQ_API_KEY`, `CALLORA_GROQ_MODEL` (openai/gpt-oss-120b), `CALLORA_LLM_TEMPERATURE` (0.2)

### Voice (Bolna)
- `CALLORA_VOICE_ENABLE_OUTBOUND_CALLS` (false) - kill switch
- `CALLORA_BOLNA_API_KEY`, `CALLORA_BOLNA_BASE_URL`, `CALLORA_BOLNA_TIMEOUT_SECONDS`, `CALLORA_BOLNA_WEBHOOK_SECRET`, `CALLORA_BOLNA_DEFAULT_AGENT_ID` (deprecated/ignored), `CALLORA_BOLNA_DEFAULT_FROM_NUMBER` (deprecated/ignored)
- Redis: `CALLORA_REDIS_URL`, `CALLORA_REDIS_ENABLED`, `CALLORA_REDIS_KEY_PREFIX`, `CALLORA_VOICE_RATE_LIMIT_PER_MINUTE` (60), `CALLORA_VOICE_CACHE_TTL_SECONDS` (300)
- Calling policy: timezone/window/days, quotas, `CALLORA_DISABLE_CALL_QUOTAS` (dev only + dev env), concurrency, cooldowns
- Voice tools: `CALLORA_VOICE_TOOL_SECRET` (empty disables), rate limits, callback settings, qualification
- Post-call: `CALLORA_VOICE_LLM_PROVIDER` (groq/langchain/null), model/temperature/timeouts, job retries
- Status sync: intervals/timeouts/action

### Workflow
- `CALLORA_WORKFLOW_MAX_ACTIVE_RUNS` (20)
- `CALLORA_WORKFLOW_MESSAGE_COOLDOWN_SECONDS` (3600)
- `CALLORA_WORKFLOW_ALLOW_UNAPPROVED_INTERNAL` (false)
- `CALLORA_REMINDER_DELIVERY_PROVIDER` (none)
- `CALLORA_BOOKING_PROVIDER` (internal)
- Analytics: `CALLORA_ANALYTICS_MIN_SAMPLE_PER_WINDOW` (5), `CALLORA_ANALYTICS_MIN_CALLS_FOR_LEAKAGE` (10)

---

## 15. Limitations, Discrepancies & Security Notes

### 15.1 Router Shadowing (Critical)
- **Issue:** `recovery_agent_api` registered at line 82, `recovery_dashboard_api` at line 83 under same prefix `/api/v1/agent/recovery`. Dashboard routes for `/cases` and `/cases/{case_id}` defined after are shadowed by recovery-agent routes.
- **Impact:** Runtime behavior differs from OpenAPI. OpenAPI shows dashboard operation IDs (`dashboard_list_recovery_cases`, `dashboard_get_recovery_case`) and dashboard schemas/filters/`total`, but **recovery-agent handlers win at runtime** returning recovery-agent envelopes (`items,business_id,limit,offset,ranking`, no `total`) and ignoring dashboard-only filters (`priority_level`, `query`, `needs_human_approval`).
- **Documented:** Both are registered; counts reflect this (116 handlers including 2 shadowed).

### 15.2 Pagination/Envelope Inconsistencies
- `GET /api/v1/voice/knowledge` returns `total` as page length only
- `GET /api/v1/voice/dnd` paginates in-memory, omits `total`
- `GET /api/v1/voice/leads` has no `total`
- Workflow lists generally omit `total`; reminders omit limit/offset
- `GET /api/v1/recommendations` returns `items,total,business_id` (no limit/offset echo)
- Recovery Agent `/cases` (runtime) has no `total` vs Dashboard expects `total`
- `GET /api/v1/recommendations/recovery` returns bare array
- `GET /api/v1/recommendations/scripts/versions` returns bare array
- `GET /api/v1/voice/leads/{lead_id}/consent` returns bare array

### 15.3 Tenant Isolation Issues
- **`POST /api/v1/workflow/reminders/dispatch`** calls `dispatch_due_reminders(db, settings)` with **no business filter** despite authenticated route - processes all tenants' due reminders. **Flagged as tenant-isolation discrepancy.**
- **Voice ops jobs:** Jobs with `business_id=None` (global jobs) accessible by any authenticated tenant.

### 15.4 Security Limitations
- **Voice follow-up auth (`POST /api/v1/voice/answer-question`):** Weaker compatibility auth accepts business API keys but also trusts provider-signature headers or supplied `execution_id` + `business_id` to authorize even without a key (if key supplied must match). Flag as security limitation.
- **Voice tools:** Shared secret optional; empty disables. Business API keys also accepted.
- **Webhooks:** Unauthenticated by API key; rely on optional HMAC/signature handling.

### 15.5 Other Notes
- **No frontend:** Absent from repo; examples framework-agnostic.
- **CRM sync:** Always records honest status; never fabricates.
- **Voice calls:** Always persists compliance decision; blocked return 403.
- **Outreach generation:** On-demand on GET if absent; blocked in certain cases.
- **Integration sync:** Always `failed`/`provider_not_configured` (no external adapter).
- **Appointment booking:** Only `internal` provider confirms internally; others record failed/not configured.
- **Workflow execute:** Run ends completed even if step blocked with `blocked=true`.
- **Recommendation script approval:** Creates inactive version, does not activate.
- **DND DELETE:** Returns 200 with inactive entry (not 204).
- **Call outcome POST:** Does not exist; GET only.
- **Postcall dedup:** Uses `postcall_dedupe_key(call.id, status_key)` with `status_key="manual"` for non-terminal calls.
- **Tool errors:** Mapped to flat envelope with uppercased codes; unhandled return 500 `INTERNAL_ERROR`.
- **No live verification:** All based on source/OpenAPI inspection.

---

## 16. Verification Summary

- **Project structure inspected:** Backend modules, routers, schemas, auth, errors, config, models reviewed.
- **OpenAPI generated:** `/tmp/callora_openapi.json` (98 paths, 114 operations). Method totals GET 55/POST 50/PUT 4/PATCH 3/DELETE 2. Tags as listed.
- **Inventories extracted:** `/tmp/ops.txt` (all operations), `/tmp/schemas.txt` (52 schemas).
- **Router order confirmed:** Shadowing of recovery cases routes verified in `main.py`.
- **Auth patterns confirmed:** Business X-API-Key, voice tool auth variants, webhook unauthenticated with optional HMAC, follow-up weaker auth.
- **Key behaviors verified from source:** Idempotency, compliance persistence, async/sync import, CRM validation-before-save, workflow state machine, tool envelopes, pagination discrepancies.
- **Counts reconciled:** 116 registered handlers / 114 OpenAPI operations (including 2 shadowed). Test expectation matches OpenAPI.
- **No application code changes made.** `API_DOC.md` created at project root.

---

## 17. Source References

- `backend/app/main.py` - App setup, router registration, shadowing, root/health
- `backend/app/config.py` - All settings and defaults
- `backend/app/auth.py` - Business auth
- `backend/app/errors.py` - Error classes and handlers (including tool envelope mapping)
- `backend/app/api/leads.py`, `backend/app/api/imports.py`, `backend/app/api/agent_leads.py`, `backend/app/api/crm_connections.py`
- `backend/app/modules/lead_import/schemas.py` - Lead/import schemas
- `backend/app/modules/crm_sync/*` - CRM schemas/router/service
- `backend/app/modules/recovery_agent/router.py` - Recovery agent routes
- `backend/app/modules/recovery_dashboard/router.py` - Dashboard routes (shadowing)
- `backend/app/modules/recommendation_agent/router.py` - Recommendations
- `backend/app/modules/recovery_workflow/router.py`, `service.py`, `analytics.py`, `analysis.py`, `eligibility.py`, `appointments.py` - Workflow
- `backend/app/modules/voice/*` - All voice APIs, schemas, tools, webhooks
- Generated: `/tmp/callora_openapi.json`, `/tmp/ops.txt`, `/tmp/schemas.txt`
- Supporting: `README.md`, `docker-compose.yml`, `.env.example`, `wworkflow.md`

---

**Final Note:** This document reflects implemented behavior as of the audit date. For the most current behavior, consult the source code. Any discrepancies between OpenAPI and runtime are explicitly noted above.
