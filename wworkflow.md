# Callora — Workflow & Endpoint Testing Guide

How to test **all 15 business endpoints** and the full end-to-end workflow:

```
CSV upload → map & standardize → validate & deduplicate → persist leads
           → check CRM connection → synchronize conditionally
           → expose agent-ready leads
           → recovery agent detects neglected leads → recovery cases
```

Every expected response in this guide was produced by **actually running the
workflow** against a scratch database — not copied from design docs.
IDs (`imp_…`, `lead_…`, `CRM-…`) vary per run.

---

## 1. Quick start

### Option A — Docker Compose (recommended)

```bash
docker compose up --build          # backend on http://localhost:8001, Postgres on 5433
```

Migrations run automatically on startup (`alembic upgrade head`).

### Option B — Local (SQLite or Postgres)

```bash
cd backend
pip install -r requirements.txt
export CALLORA_DATABASE_URL="sqlite:///./callora.db"      # or a Postgres URL
export CALLORA_API_KEYS="biz_001:dev-key-biz-001,biz_002:dev-key-biz-002"
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

### Variables used below

```bash
export BASE=http://localhost:8001          # 8001 = Compose, 8000 = local uvicorn
export KEY=dev-key-biz-001                 # from CALLORA_API_KEYS
export H="X-API-Key: $KEY"
```

Interactive API docs: `$BASE/docs`

---

## 2. The 15-route allowlist

| # | Method | Route | Purpose |
|---|--------|-------|---------|
| 1 | POST | `/api/v1/leads/import` | Import and standardize CSV leads |
| 2 | POST | `/api/v1/leads` | Create a lead manually |
| 3 | GET | `/api/v1/leads` | List leads |
| 4 | GET | `/api/v1/leads/{lead_id}` | Retrieve a lead by ID |
| 5 | GET | `/api/v1/imports/{import_id}` | Import status and summary |
| 6 | GET | `/api/v1/imports/{import_id}/errors` | Import validation errors |
| 7 | GET | `/api/v1/crm/connections` | List CRM connections |
| 8 | POST | `/api/v1/crm/connections` | Create/update the external CRM connection |
| 9 | POST | `/api/v1/crm/sync` | Manual CRM synchronization |
| 10 | GET | `/api/v1/agent/leads` | List agent-ready leads |
| 11 | GET | `/api/v1/agent/leads/{lead_id}` | Retrieve an agent-ready lead |
| 12 | GET | `/api/v1/agent/leads/by-external/{external_crm_id}` | Find a lead by external CRM ID |
| 13 | POST | `/api/v1/agent/recovery/run` | Run the opportunity-detection sweep |
| 14 | GET | `/api/v1/agent/recovery/cases` | List recovery cases |
| 15 | GET | `/api/v1/agent/recovery/cases/{case_id}` | Retrieve one recovery case |

Infrastructure (kept, outside the allowlist): `GET /`, `GET /health`, `/docs`.

**The eligibility engine added no routes** — it runs *inside* the existing
`POST /api/v1/agent/recovery/run` sweep and is observable through the same
response fields and `GET /api/v1/agent/recovery/cases`. The allowlist is
still exactly 15 routes.

**Module 5 (Priority Scoring) also added no routes** — ranking runs
*inside* the same sweep, persists on the same `recovery_cases` rows, and
is exposed through backward-compatible extensions of the two recovery
endpoints (see Part D). The allowlist is still exactly 15 routes.

**All routes live under `/api/v1` exactly once.** There are no unversioned
aliases (`/leads`, `/imports/...`) and no `/leads/import/preview` endpoint.

---

## 3. Part A — End-to-end workflow WITHOUT a CRM connection

This is the default state: no CRM configured → synchronization is **skipped**,
and everything else still works.

### Step 0 — Health check

```bash
curl -s $BASE/health
# {"status":"healthy"}
```

### Step 1 — Import a CSV

`sample.csv` in the repo root is intentionally messy (duplicate row, bad
phone, negative budget, invalid email, leading blank line):

```bash
curl -s -X POST "$BASE/api/v1/leads/import" -H "$H" \
     -F "file=@sample.csv;type=text/csv"
```

**Verified response — HTTP 201:**

```json
{
  "import_id": "imp_25e0f5387bf84934",
  "status": "completed",
  "total_rows": 10,
  "created": 6,
  "duplicates": 1,
  "rejected": 3,
  "errors_url": "/api/v1/imports/imp_25e0f5387bf84934/errors"
}
```

Counts always reconcile: `total_rows = created + duplicates + rejected`.

> **Optional `mappings` field** — confirm ambiguous columns explicitly:
> ```bash
> curl -s -X POST "$BASE/api/v1/leads/import" -H "$H" \
>      -F "file=@sample.csv" \
>      -F 'mappings={"Customer Name":"name","Mobile Number":"phone","Lead Source":"source","Budget":"deal_value","Email":"email","Preferred Language":"language"}'
> ```
> In Swagger UI, leave the field empty if you don't need it — the empty
> sentinel and URL-encoded JSON are both handled.

> **Large files** (above `CALLORA_SYNC_PROCESSING_ROW_THRESHOLD`, default
> 1000 rows) return **202** with `status: "pending"` — poll Step 2.

### Step 2 — Import status

```bash
curl -s "$BASE/api/v1/imports/$IMPORT_ID" -H "$H"
```

**Verified response — HTTP 200** (abridged):

```json
{
  "import_id": "imp_25e0f5387bf84934",
  "status": "completed",
  "total_rows": 10, "created": 6, "duplicates": 1, "rejected": 3,
  "errors_url": "/api/v1/imports/imp_25e0f5387bf84934/errors",
  "source_type": "csv_upload",
  "original_filename": "sample.csv",
  "mapping": { "resolved": {"Customer Name": "name", "Mobile Number": "phone", "...": "..."} },
  "error_message": null
}
```

### Step 3 — Row-level errors

```bash
curl -s "$BASE/api/v1/imports/$IMPORT_ID/errors" -H "$H"
```

**Verified response — HTTP 200, `total: 4`:**

| row | error_code | field | message |
|-----|-----------|-------|---------|
| 7 | `duplicate_phone` | phone | this phone number appears more than once in the uploaded file |
| 9 | `validation_error` | phone | phone must contain only digits, spaces, dashes or a leading `+` |
| 10 | `validation_error` | deal_value | value must not be negative |
| 11 | `validation_error` | email | 'invalid-email' is not a valid email address |

Unmapped columns (`Property Type`, `City`) are preserved in each lead's
`custom_fields` — nothing is silently discarded.

### Step 4 — List & retrieve canonical leads

```bash
curl -s "$BASE/api/v1/leads?page=1&page_size=3" -H "$H"
curl -s "$BASE/api/v1/leads/$LEAD_ID" -H "$H"
```

**Verified response — HTTP 200:**

```json
// GET /api/v1/leads  (list envelope)
{ "items": [ { "id": "lead_…", "name": "Kavya Rao", "phone": "9876543215",
               "source": "instagram", "deal_value": "18000000.00",
               "custom_fields": {"property_type": "Villa", "city": "Mysuru"},
               "external_id": null, "created_at": "…" } ],
  "total": 6, "page": 1, "page_size": 3 }

// GET /api/v1/leads/{lead_id}  (single lead)
{ "id": "lead_9f615f4508a14b6c", "business_id": "biz_001", "name": "Kavya Rao",
  "phone": "9876543215", "email": "kavya.test@example.com", "source": "instagram",
  "language": "kannada", "deal_value": "18000000.00", "last_contact_at": null,
  "custom_fields": {"property_type": "Villa", "city": "Mysuru"},
  "external_id": null, "created_at": "2026-10-19T14:22:07.241958" }
```

Filters: `?source=`, `?query=` (name search), `?created_after=` / `?created_before=`,
`?page=`, `?page_size=`.

### Step 5 — Create a lead manually

```bash
curl -s -X POST "$BASE/api/v1/leads" -H "$H" -H "Content-Type: application/json" \
     -d '{"name":"Meera Iyer","phone":"9811112222","source":"website"}'
```

**Verified — HTTP 201:**

```json
{ "id": "lead_303a41f99bc049d6", "business_id": "biz_001", "name": "Meera Iyer",
  "phone": "9811112222", "email": null, "source": "website", "language": null,
  "deal_value": null, "last_contact_at": null, "custom_fields": {},
  "external_id": null, "created_at": "2026-10-19T14:22:07.343592Z" }
```

Duplicate phone — **HTTP 409:**

```json
{ "error": { "code": "conflict",
  "message": "a lead with phone '9811112222' already exists",
  "details": [ { "lead_id": "lead_303a41f99bc049d6", "reason": "duplicate_phone" } ] } }
```

Retries are safe: send an `Idempotency-Key` header to replay the original
result instead of creating a duplicate.

### Step 6 — Agent-ready leads (no CRM → still exposed!)

```bash
curl -s "$BASE/api/v1/agent/leads" -H "$H"
curl -s "$BASE/api/v1/agent/leads/$LEAD_ID" -H "$H"
curl -s "$BASE/api/v1/agent/leads/by-external/CRM-DOES-NOT-EXIST" -H "$H"
```

**Verified responses:**

```json
// GET /api/v1/agent/leads            HTTP 200 — every persisted lead appears
{ "items": [ … ], "business_id": "biz_001", "limit": 50, "offset": 0 }   // 6 items after import

// GET /api/v1/agent/leads/{lead_id}  HTTP 200
{ "lead_id": "lead_d6ae52af3b0243e3", "business_id": "biz_001",
  "name": "Amit Kumar", "phone": "9876543212", "source": "website",
  "status": "New", "external_crm_id": null,          // null = never synchronized
  "deal_value": 3500000.0, "custom_fields": { "property_type": "Plot", "city": "Hyderabad" } }

// GET /api/v1/agent/leads/by-external/…   HTTP 404 when no such CRM id exists
{ "error": { "code": "lead_not_found", "message": "Lead 'CRM-DOES-NOT-EXIST' was not found" } }
```

Interpretation of sync state — **stored values only, nothing invented**:

| Condition | What you see |
|---|---|
| No CRM / not yet synchronized | `external_crm_id: null` |
| Synchronized successfully | `external_crm_id` = real CRM record ID (also findable via `by-external`) |
| Failed attempt | recorded in `crm_sync_records` with `status:"failed"` + `error_message` — the lead stays fully visible, `external_crm_id` stays `null` (never fabricated) |

### Step 7 — CRM connections (none configured)

```bash
curl -s "$BASE/api/v1/crm/connections" -H "$H"
# HTTP 200: []
```

The import in Step 1 already returned 201 and all leads are agent-visible —
**sync is skipped, nothing is lost.**

---

## 4. Part B — End-to-end workflow WITH a CRM connection

### Step 1 — Run a fake external CRM

Save as `fake_crm.py` (stdlib only) and start it:

```bash
python3 fake_crm.py       # listens on 127.0.0.1:9000
```

```python
"""Minimal stand-in for the external CRM used by Callora's Module 2."""
import json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import itertools

counter = itertools.count(1)

class FakeCRM(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                        # GET /health -> connectivity check
        if self.path != "/health":
            return self._send({"error": "not found"}, 404)
        if self.headers.get("x-api-key") != "crm-secret-token":
            return self._send({"error": "unauthorized"}, 401)
        self._send({"status": "ok"})

    def do_POST(self):                       # POST /contacts  -> create
        n = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(n) or b"{}")
        if self.headers.get("x-api-key") != "crm-secret-token":
            return self._send({"error": "unauthorized"}, 401)
        if self.path == "/contacts":
            return self._send({"id": f"CRM-{next(counter):04d}",
                               "full_name": payload.get("full_name")})
        self._send({"error": "not found"}, 404)

    def do_PATCH(self):                      # PATCH /contacts/{id} -> update
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        if self.headers.get("x-api-key") != "crm-secret-token":
            return self._send({"error": "unauthorized"}, 401)
        self._send({"id": self.path.rsplit("/", 1)[-1]})

HTTPServer(("127.0.0.1", 9000), FakeCRM).serve_forever()
```

### Step 2 — Start the backend

```bash
cd backend
export CALLORA_DATABASE_URL="sqlite:///./callora.db"
export CALLORA_API_KEYS="biz_001:dev-key-biz-001,biz_002:dev-key-biz-002"
uvicorn app.main:app --port 8000
```

No CRM configuration is required here: the business supplies its external CRM
URL and API key through the connection endpoint below. (The optional
`CALLORA_CRM_BASE_URL` / `CALLORA_CRM_API_KEY` env vars only seed a default
connection for local convenience.)

> **Fake CRM on the Docker/WSL host (Docker → host):** connection URLs are
> SSRF-validated, so `http://host.docker.internal:9000` is rejected unless it
> is explicitly allowlisted. For local development start the backend with an
> exact `host:port` exception:
>
> ```bash
> export CALLORA_CRM_DEV_ALLOWED_DESTINATIONS=host.docker.internal:9000
> ```
>
> Only that pair is allowed — other ports and every other private, loopback,
> or link-local destination stay blocked. Leave the variable unset in
> production. (`CALLORA_CRM_ALLOW_PRIVATE_URLS=true` is the broader legacy
> local-testing switch and is not recommended.)

### Step 3 — Save and validate the connection

`crm_base_url` is the external CRM's base URL; `crm_x_api_key` is the
external CRM's own credential (never Callora's). Callora validates the
connection with a real `GET /health` call before saving:

```bash
curl -s -X POST "$BASE/api/v1/crm/connections" -H "$H" \
     -H "Content-Type: application/json" \
     -d '{
       "connection_id": "urban-1",
       "name": "UrbanNest CRM",
       "crm_base_url": "http://127.0.0.1:9000",
       "crm_x_api_key": "crm-secret-token",
       "crm_timeout_seconds": 15
     }'
```

**Verified — HTTP 200** (the key is never echoed back):

```json
{ "connection": { "connection_id": "urban-1", "name": "UrbanNest CRM",
                  "is_active": true, "crm_base_url": "http://127.0.0.1:9000",
                  "has_api_key": true, "crm_timeout_seconds": 15.0,
                  "created_at": "…", "updated_at": "…" },
  "verified": true,
  "message": "Connection validated against the external CRM (GET /health) and saved." }
```

Validation failures save nothing and never report `verified`:

| Failure | HTTP | `error.code` |
|---|---|---|
| CRM rejects the API key (401/403) | 502 | `crm_authentication_failed` |
| CRM unreachable / connection refused | 503 | `crm_unavailable` |
| CRM timed out | 504 | `crm_timeout` |
| Wrong endpoint / unknown contract | 502 | `crm_connection_validation_failed` |

`POST` with the same `connection_id` updates that row in place (no duplicates).
URLs are SSRF-guarded: only absolute `http(s)` URLs are accepted, and
loopback/private/link-local/metadata destinations are rejected with `422`.

### Step 4 — Verify the connection

```bash
curl -s "$BASE/api/v1/crm/connections" -H "$H"
```

**Verified — HTTP 200:**

```json
[ { "connection_id": "urban-1", "name": "UrbanNest CRM",
    "is_active": true, "crm_base_url": "http://127.0.0.1:9000",
    "has_api_key": true, "crm_timeout_seconds": 15.0,
    "created_at": "…", "updated_at": "…" } ]
```

The API key is **never** included in this response.

### Step 5 — Import → automatic synchronization

```bash
curl -s -X POST "$BASE/api/v1/leads/import" -H "$H" -F "file=@sample.csv"
```

**Verified — HTTP 201**, then (give the background sync a moment — it runs
*after* the response, outside the import transaction):

```bash
# agent endpoints now carry the real CRM IDs
curl -s "$BASE/api/v1/agent/leads/by-external/CRM-0001" -H "$H"     # HTTP 200
```

**Verified server-side state after the import:**

| Check | Result |
|---|---|
| Import response | `201`, `created: 6` (sync never changes the import result) |
| Leads with real `external_id` | **6 of 6** (`CRM-0001` … `CRM-0006`) |
| `agent_leads` rows with `external_crm_id` | **6** |
| `crm_sync_records` by status | `created: 6, updated: 0, failed: 0` |
| `by-external/CRM-0001` | HTTP 200, correct lead |

Only the 6 created leads are synchronized — duplicates and rejected rows are
never sent to the CRM. Contacts are created (`POST /contacts`) on first sync
and updated (`PATCH /contacts/{id}`) on later syncs via the stored
`external_id` mapping, so retries don't create duplicate contacts.

### Step 6 — Manual synchronization (still available)

```bash
curl -s -X POST "$BASE/api/v1/crm/sync" -H "$H" -H "Content-Type: application/json" \
     -d '{
       "connection_id": "default",
       "idempotency_key": "manual-key-1",
       "lead": {
         "id": "LEAD_ID_FROM_STEP_4",
         "business_id": "biz_001",
         "name": "Rahul Sharma",
         "phone": "9876543210",
         "source": "99acres",
         "created_at": "2026-10-19T14:22:07.241958"
       }
     }'
```

**Verified responses:**

```json
// First call (lead already synced by auto-sync -> update path)   HTTP 200
{ "sync_id": "cb7b243b-3e93-4bbc-a251-629a82b4a650",
  "lead_id": "lead_aaf4f51d53764b25", "business_id": "biz_001",
  "status": "updated", "external_crm_id": "CRM-0005",
  "message": "Lead updated in the external CRM." }

// Replay: same key + same payload                                 HTTP 200
{ "sync_id": "cb7b243b-3e93-4bbc-a251-629a82b4a650", ... identical result ... }

// Same key + DIFFERENT payload                                    HTTP 409
{ "error": { "code": "crm_idempotency_conflict",
  "message": "Idempotency key 'manual-key-1' was already used with a different payload" } }
```

`status` is `created` for a new contact, `updated` when a stored external ID
exists. A failure can never be reported as success.

### Step 7 — Failure cases (leads must never be lost)

Point a connection at a dead CRM and sync:

```bash
# insert a connection with crm_base_url http://127.0.0.1:9 (nothing listening),
# then POST /api/v1/crm/sync with connection_id "dead-crm"
```

**Verified:**

```json
// HTTP 502
{ "error": { "code": "crm_sync_failed",
  "message": "CRM unavailable: Could not reach external CRM: All connection attempts failed",
  "details": [ { "connection_id": "dead-crm" } ] } }
```

| Follow-up check | Verified result |
|---|---|
| `GET /api/v1/leads/{id}` for that lead | `200`, lead intact, `external_id: null` |
| `GET /api/v1/agent/leads/{id}` | `200`, visible, `external_crm_id: null` |
| `crm_sync_records` | one row with `status:"failed"` + `error_message` |
| Import with a broken CRM | still `201`; leads persisted regardless |

Error code reference for Module 2:

| Situation | HTTP | `error.code` |
|---|---|---|
| Unknown/inactive connection | 404 | `crm_connection_not_found` |
| Connection disabled | 400 | `crm_connection_inactive` |
| CRM rejected credentials | 502 | `crm_sync_failed` (message: *CRM authentication failed*) |
| CRM timed out | 502 | `crm_sync_failed` (message: *CRM request timed out*) |
| CRM unreachable / 5xx | 502 | `crm_sync_failed` |
| Idempotency key reuse w/ different payload | 409 | `crm_idempotency_conflict` |
| Lead belongs to another business | 404 | `lead_not_found` |

---

## 5. Security & isolation checks

```bash
# No API key -> 401
curl -s -o /dev/null -w "%{http_code}\n" "$BASE/api/v1/leads"
# 401  {"error":{"code":"authentication_failed",...}}

# Wrong key -> 401
curl -s -o /dev/null -w "%{http_code}\n" "$BASE/api/v1/leads" -H "X-API-Key: wrong"
# 401

# Another business cannot read your lead -> 404
curl -s "$BASE/api/v1/leads/$LEAD_ID" -H "X-API-Key: dev-key-biz-002"
# 404  {"error":{"code":"lead_not_found",...}}

# Another business sees an empty agent list -> []
curl -s "$BASE/api/v1/agent/leads" -H "X-API-Key: dev-key-biz-002"
# {"items":[],"business_id":"biz_002","limit":50,"offset":0}

# Another business sees no CRM connections -> []
curl -s "$BASE/api/v1/crm/connections" -H "X-API-Key: dev-key-biz-002"
# []
```

Credentials stay server-side: the external CRM API key never appears in any
response (only `has_api_key: true/false`), and Callora's own `X-API-Key` is
never forwarded to the external CRM.

---

## 6. Route registration checks

```bash
curl -s $BASE/openapi.json | python3 -c "
import json, sys
paths = json.load(sys.stdin)['paths']
biz = sorted((m.upper(), p) for p, i in paths.items() for m in i if p.startswith('/api/v1/'))
print('business operations:', len(biz))          # -> 15
print('double prefix:', [p for p in paths if '/api/v1/api/v1' in p])   # -> []
print('unversioned:', [p for p in paths if not p.startswith('/api/v1/') and p not in ('/', '/health')])
# -> []
"
```

Expected output:

```
business operations: 15
double prefix: []
unversioned: []
```

---

## 7. Automated test suite

```bash
cd backend
python3 -m pytest tests/ -q
```

**Expected: `427 passed`** (plus 1 upstream Starlette deprecation warning).

Coverage map:

| Requirement | Tests |
|---|---|
| CSV upload, mapping, validation, normalization, dedup, persistence | `test_api_import.py`, `test_mapping.py`, `test_validation.py`, `test_normalization.py`, `test_dedup.py`, `test_lead_schema.py` |
| All 6 Leads & Imports endpoints | `test_api_leads.py`, `test_api_import.py` |
| Automatic sync when CRM exists / skipped when not | `test_auto_sync.py` |
| CRM timeout, auth failure, external API errors | `test_crm_sync.py` |
| Partial failures across leads | `test_auto_sync.py::TestPartialFailures` |
| Duplicate prevention & retry/idempotency | `test_crm_sync.py`, `test_dedup.py`, `test_api_leads.py` |
| Persistence of external CRM IDs & sync results | `test_crm_sync.py`, `test_auto_sync.py` |
| All 3 Agent Leads endpoints | `test_agent_storage.py`, `test_api_leads.py` |
| Opportunity signals, scoring, guardrails, strategist fallback | `test_recovery_detect.py` (37 tests) |
| Recovery sweep, cases API, idempotent re-runs, status filter | `test_recovery_api.py` (14 tests) |
| Recovery resilience: timeouts, 5xx, malformed payloads, LLM outage | `test_recovery_resilience.py` (12 tests) |
| Deterministic eligibility gate: status, consent, contact validity, recovery evidence, previous outcome, operator-closed, CRM-drift evidence | `test_recovery_eligibility.py` (67 tests) |
| Eligibility counted in the run summary (`skipped_inactive` / `skipped_invalid`) | `test_recovery_eligibility.py::TestEligibilityCounts` |
| Duplicate-safe case persistence: stable `case_id`/`created_at`, no re-open, unique-constraint race | `test_recovery_eligibility.py::TestCasePersistence` |
| Priority scoring: normalization, boundaries, level thresholds, factor math, missing data, determinism | `test_priority_scoring.py` (pure-engine tests) |
| Priority ranking end-to-end: ordering, tie-breaking, de-ranking ineligible leads, recalculation, pagination, isolation, backward compatibility | `test_priority_scoring.py` (API tests) |
| CRM connection configuration & validation | `test_crm_connections.py` |
| CRM Connections listing endpoint | `test_crm_sync.py::TestCRMConnectionsEndpoint` |
| Manual CRM synchronization | `test_crm_sync.py` |
| Business-level access isolation | all of the above (cross-tenant 404/401/empty) |
| Route registration, no duplicate `/api/v1` | `test_auto_sync.py::TestRouteRegistration` |
| Startup & failure handling | `test_failure_handling.py` + lifespan-based fixtures |

---

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `401 authentication_failed` | Missing or wrong `X-API-Key` header |
| `404 lead_not_found` on your own lead | Wrong business key (cross-tenant) or wrong ID |
| `409 conflict` on create | Phone already exists for this business — expected |
| `409 crm_idempotency_conflict` | Same `Idempotency-Key` reused with a changed payload — use a new key |
| `422` on import | Check the `mappings` value is a JSON object of header → field |
| Agent list is empty | Leads exist? They're projected on import/create — check you're using the same API key |
| `external_crm_id` is `null` | No CRM configured, sync not yet run, or sync failed (check `crm_sync_records` / server logs) |
| Auto-sync "didn't happen" | It runs as a background task right after the response — wait a moment, then re-check; verify `CALLORA_CRM_BASE_URL` and that the connection `is_active` |
| `502 crm_sync_failed` | Fake CRM not running, wrong URL, or bad credentials — the lead is unaffected |
| Port confusion | Compose serves `8001`, local uvicorn defaults to `8000` |
| Lead missing from sweep candidates | It failed the eligibility gate — check `skipped_inactive` (active/converted/recovered/operator-closed) vs `skipped_invalid` (opted out, no usable phone, no recovery evidence) in the run response |

---

## 4B. Part C — Module 3: Recovery Agent (Opportunity Detection)

The recovery agent fetches agent-ready leads through **GET
`/api/v1/agent/leads`** (X-API-Key, pagination), evaluates them against
recovery criteria, and persists **recovery cases** — the hand-off
surface for downstream workflow steps.

Detection is pure rules; only the play/message choice uses the LLM.

| Signal | Criterion |
|---|---|
| `never_contacted` | `last_contact_at` null and age > `CALLORA_RECOVERY_SLA_MINUTES` (default 120) |
| `stalled_follow_up` | last touch older than `CALLORA_RECOVERY_STALL_DAYS` (default 7) |
| `crm_sync_drift` | `external_crm_id` null **and** the business has an active CRM connection |
| `event_delivery_failed` | the lead's `lead.created` outbox event is in terminal `failed` state |

Rules of the engine:

- Leads whose `status` is active or converted (`Contacted`, `Qualified`,
  `Converted`, `Closed`, …) are **excluded** — only `New`-style leads
  become candidates.
- A null `external_crm_id` without a configured CRM is the normal state,
  **not** drift.
- Scores: `R = 0.35·VALUE + 0.25·RECENCY + 0.20·REACHABILITY +
  0.15·PRIOR + 0.05·DRIFT − PENALTY`; candidates need `R ≥
  CALLORA_RECOVERY_SCORE_THRESHOLD` (default 0.40).
- Guardrails run before any LLM call: opted-out leads and leads with no
  reachable channel are hard-blocked (`route=block`, plan `archive`).
- Without `GROQ_API_KEY` (or if the LLM errors / returns
  invalid JSON) plans come from the deterministic rules planner —
  `engine` shows which one was used.

### Eligibility gate (runs first, no LLM)

Before any signal, score or recommendation is produced, every lead passes
a deterministic eligibility gate (`recovery_agent/eligibility.py`). It is
pure (no database, no external calls) so the same inputs always yield the
same decision, and it never treats missing/unknown data as eligibility:

| Rule | Excludes when |
|---|---|
| Status | status is active/converted/closed → `skipped_inactive` |
| Previous outcome | status is `recovered` or `custom_fields.is_recovered` → `skipped_inactive` |
| Operator-closed | status is `dismissed`/`suppressed`/`resolved`/`completed`/`in_recovery` → ignored (case never re-opened) |
| Consent | a configured opt-out/suppression flag is set → `skipped_invalid` |
| Contact validity | no usable phone → `skipped_invalid` |
| Recovery evidence | no verifiable never-contacted/overdue evidence (unknown timestamps) → `skipped_invalid` |

`crm_sync_drift` is only emitted for a lead the CRM module has
**confirmed** synced (a terminal `created`/`updated` `crm_sync_records`
row) that has since lost its `external_crm_id`; an unknown CRM status is
never reported as drift. When no confirmation list exists the legacy
`crm_configured` gate applies.

### Testing the eligibility gate

The gate is pure, so it can be exercised directly without a database or
network — and it is also observable through the existing sweep route:

```bash
cd backend
# Pure gate: every rule in isolation + determinism + non-mutation
python3 -m pytest tests/test_recovery_eligibility.py -q

# End-to-end: the gate's effect on the run summary and on persisted cases
python3 -m pytest tests/test_recovery_eligibility.py::TestEligibilityCounts \
                 tests/test_recovery_eligibility.py::TestCRMDriftEndToEnd -q
```

Manual check against a running backend — seed one ineligible lead, then run
the sweep and read the summary buckets (no new routes or fields were added):

```bash
# ineligible (opted out via custom_fields) → skipped_invalid
curl -s -X POST "$BASE/api/v1/leads" -H "$H" -H 'Content-Type: application/json' \
  -d '{"full_name":"Opt Out","phone":"+919000000001","custom_fields":{"opt_out":true}}'
# ineligible (already Contacted) → skipped_inactive
curl -s -X POST "$BASE/api/v1/leads" -H "$H" -H 'Content-Type: application/json' \
  -d '{"full_name":"Contacted","phone":"+919000000002","status":"Contacted"}'

curl -s -X POST "$BASE/api/v1/agent/recovery/run" -H "$H"   # 200
```

Expected: the two leads above appear only in `skipped_invalid` /
`skipped_inactive` respectively, are absent from `candidates`, and get **no**
recovery case in `GET /api/v1/agent/recovery/cases`.

### Step 8 — Run the sweep

The demo below sets `CALLORA_RECOVERY_SLA_MINUTES=1` so freshly imported
leads breach the SLA immediately (default is 120 minutes; leads older
than that need no special setup).

```bash
curl -s -X POST "$BASE/api/v1/agent/recovery/run" -H "$H"
```

**Verified — HTTP 200** (after importing `sample.csv`, 6 leads):

```json
{ "business_id": "biz_001", "fetched": 6, "evaluated": 6,
  "skipped_inactive": 0, "skipped_invalid": 0,
  "signals_detected": 6, "candidates": 6, "cases_upserted": 6,
  "engine": "rules-fallback", "swept_at": "2026-10-20T06:35:37.933739+00:00" }
```

### Step 9 — Read the recovery cases

```bash
curl -s "$BASE/api/v1/agent/recovery/cases" -H "$H"
curl -s "$BASE/api/v1/agent/recovery/cases/$CASE_ID" -H "$H"
```

**Verified — HTTP 200** (abridged; ordered by Module 5 priority score
desc — recoverability `score` desc and `case_id` asc are the stable
tie-breaks; unscored rows sink last):

```json
{ "items": [
    { "case_id": "case_106d5fed06ee4886", "lead_id": "lead_80d41b65cab84d40",
      "lead_name": "Kavya Rao", "business_id": "biz_001", "score": 0.95,
      "priority_score": 80.0, "priority_rank": 1, "priority_level": "critical",
      "score_breakdown": { "factors": [ { "factor": "business_value", "points": 30.0,
                                          "max_points": 30.0,
                                          "evidence": "deal_value 18000000 = 100% of reference 10000000" },
                                        { "factor": "urgency", "points": 25.0, "max_points": 25.0,
                                          "evidence": "…" } ],
                           "explanation": "priority 80/100 (critical): business_value 30/30 (…); …",
                           "weights": { "business_value": 30.0, "urgency": 25.0, "…" : "…" } },
      "scored_at": "2026-10-20T06:35:37.933739",
      "signals": [ { "signal": "never_contacted", "severity": "high",
                     "detail": "no contact 2 min after creation (SLA 1 min)" } ],
      "route": "llm", "action": "re_contact_lead", "channel": "sms",
      "message": "Hi Kavya, this is regarding your enquiry (ref lead_80d41b65cab84d40). May we schedule a quick call today?",
      "rationale": "deterministic fallback policy", "engine": "rules-fallback",
      "needs_human_approval": false, "status": "recommended",
      "created_at": "2026-10-20T06:35:37.933739", "updated_at": "…" }, … ],
  "business_id": "biz_001", "limit": 50, "offset": 0,
  "ranking": { "total_scored": 6,
               "levels": { "critical": 1, "high": 2, "medium": 2, "low": 1 },
               "order": "priority_score desc, score desc, case_id asc",
               "scored_at": "2026-10-20T06:35:37.933739" } }
```

| Follow-up check | Verified result |
|---|---|
| Re-run the sweep | still 6 cases — upserts per (business, lead), never duplicates |
| Converted / Contacted leads | counted in `skipped_inactive`, no case created |
| Opted-out / no-phone leads | counted in `skipped_invalid`, no case created |
| Already `recovered` (status or `custom_fields.is_recovered`) | counted in `skipped_inactive`, no case created |
| `dismissed` / `completed` case re-run | lead ignored; existing case is never re-opened or re-created |
| `?status=recommended` filter | works (`?status=exhausted` → empty) |
| No `X-API-Key` | `401 authentication_failed` |
| `dev-key-biz-002` | empty case list; its own sweeps fetch 0 leads |
| Leads API down / timeout / bad payload | `503 recovery_leads_unavailable` / `504 recovery_leads_timeout` / `502 recovery_leads_invalid_response` — nothing partial is persisted |
| No Groq key | `engine: "rules-fallback"`, sweep completes |

### New configuration

| Variable | Default | Purpose |
|---|---|---|
| `CALLORA_RECOVERY_SLA_MINUTES` | `120` | never-contacted SLA |
| `CALLORA_RECOVERY_STALL_DAYS` | `7` | stalled-follow-up threshold |
| `CALLORA_RECOVERY_SCORE_THRESHOLD` | `0.40` | minimum recoverability score |
| `CALLORA_RECOVERY_OPT_OUT_FLAGS` | `opt_out,opted_out,do_not_contact,dnc,unsubscribed,suppressed` | `custom_fields` flags treated as opt-out / suppression (consent) |
| `CALLORA_RECOVERY_RECOVERED_STATUSES` | `recovered` | canonical `status` values meaning "already recovered" |
| `CALLORA_RECOVERY_IGNORED_STATUSES` | `dismissed,suppressed,resolved,completed,in_recovery` | statuses closed out of recovery — never re-opened by a sweep |
| `CALLORA_RECOVERY_API_BASE_URL` | unset | unset = fetch leads in-process via ASGI (same API contract); set to fetch over HTTP |
| `GROQ_API_KEY` | unset | enables the LLM strategist; unset = rules-fallback |
| `CALLORA_GROQ_MODEL` | `openai/gpt-oss-120b` | Groq model id |

### Module 5 configuration (priority scoring)

| Variable | Default | Purpose |
|---|---|---|
| `CALLORA_PRIORITY_WEIGHT_BUSINESS_VALUE` | `30` | weight of the deal-value factor |
| `CALLORA_PRIORITY_WEIGHT_URGENCY` | `25` | weight of the SLA/stall-breach factor |
| `CALLORA_PRIORITY_WEIGHT_INTENT` | `20` | weight of the buying-intent factor |
| `CALLORA_PRIORITY_WEIGHT_RECOVERY_POTENTIAL` | `15` | weight of the signal-severity factor |
| `CALLORA_PRIORITY_WEIGHT_CONTACTABILITY` | `10` | weight of the contact-data factor |
| `CALLORA_PRIORITY_DEAL_VALUE_REFERENCE` | `10000000` | deal value that reaches full business-value points |
| `CALLORA_PRIORITY_URGENCY_FULL_BREACH_MINUTES` | `10080` | minutes beyond SLA that reach full urgency points |
| `CALLORA_PRIORITY_URGENCY_FULL_STALL_DAYS` | `30` | days beyond the stall window that reach full urgency points |
| `CALLORA_PRIORITY_INTENT_FLAGS` | *(empty)* | comma-separated `custom_fields` flags counted as intent evidence |
| `CALLORA_PRIORITY_LEVEL_CRITICAL_MIN` | `80` | score ≥ this → `critical` |
| `CALLORA_PRIORITY_LEVEL_HIGH_MIN` | `60` | score ≥ this → `high` |
| `CALLORA_PRIORITY_LEVEL_MEDIUM_MIN` | `40` | score ≥ this → `medium`, else `low` |

Weights are normalized (they need not sum to 100): each factor
contributes `weight × ratio`, and the total is
`100 × Σ(weight × ratio) / Σweight` — so re-weighting can never push a
score outside 0..100.

---

## 4D. Part D — Module 5: Priority Scoring

### Responsibilities

Module 5 **ranks the eligible recovery cases** Modules 3/4 already
produced. It answers one question — *which neglected lead should the
team touch first* — with a normalized **0–100 priority score**, a
**priority level** (`critical` / `high` / `medium` / `low`), a
**rank** and a per-factor **score breakdown** that explains every point.

Like the eligibility engine, it **added no routes and no tables**: it
runs *inside* the existing `POST /api/v1/agent/recovery/run` sweep,
persists on the existing `recovery_cases` rows (migration `0004` only
adds nullable columns), and is read through the existing
`GET /api/v1/agent/recovery/cases` endpoints with backward-compatible
extensions. The allowlist is still exactly 15 routes.

**Separation of concerns is strict:**

- Module 4 (eligibility) decides **whether** a lead may be recovered.
  Module 5 only **ranks** cases that passed the gate. A high priority
  score can never override an eligibility failure, an opt-out, invalid
  contact details or a required approval.
- Before ranking, the gate is **re-verified with Module 4's own
  function** against current lead data: a case whose lead opted out or
  was recovered *after* case creation is dropped from the ranked set
  (its stale priority fields are cleared) — it is never reintroduced.
- Cases that advanced into the ACT/HITL lifecycle
  (`in_recovery`/`recovered`/`exhausted`/`archived`) are never
  re-scored or re-ranked — same rule the sweep already applies.
- No LLM produces any number here. Every factor is deterministic math
  over stored fields; equal inputs always yield equal scores.

### The scoring formula and default weights

```
priority = 100 × Σ(weight_f × ratio_f) / Σ(weight_f)          (0..100)
```

| Factor | Default weight | Ratio (0..1) computed from | Missing data → |
|---|---|---|---|
| `business_value` | **30** | `deal_value` vs. `CALLORA_PRIORITY_DEAL_VALUE_REFERENCE` (default Rs.10M — the same reference the recoverability score uses), capped at 1.0 | 0 — a missing deal value is **never** read as value |
| `urgency` | **25** | verified time beyond the follow-up SLA (`never_contacted`) or stall window (`stalled_follow_up`), scaled to full points at `…FULL_BREACH_MINUTES` / `…FULL_STALL_DAYS`; the worse of the two counts | 0 — a signal with an unknown timestamp is not time evidence |
| `buying_intent` | **20** | 0.5 for a recorded prior contact (`last_contact_at`) + 0.5 for any configured `CALLORA_PRIORITY_INTENT_FLAGS` flag truthily set in `custom_fields` | 0 — intent is never assumed from missing data; default flags list is **empty** so nothing is invented |
| `recovery_potential` | **15** | severity of the signals already verified on the case (high 1.0 / medium 0.7 / low 0.4 — the same mapping the recoverability score uses; unknown severity counts as low) | 0 — no signals, no points |
| `contactability` | **10** | 0.6 usable phone + 0.2 usable email + 0.2 recorded language, from the canonical lead record | absent fields simply add nothing; blank values never count |

**Assumptions behind the defaults** (all configurable): deal size is
the strongest single reason to call one lead before another; how long
the SLA has been breached drives time-sensitivity; recorded engagement
predicts conversion; stronger verified signals mean a more actionable
opportunity; a reachable, language-known lead is worth more than an
unreachable one. Weights are normalized, so any re-weighting keeps
scores inside 0..100.

### Priority thresholds

`critical` ≥ 80 > `high` ≥ 60 > `medium` ≥ 40 > `low` — configurable
via `CALLORA_PRIORITY_LEVEL_*_MIN`.

### Missing-data handling

Missing deal value, intent, timestamps, or contact fields each earn
**zero points for that factor** and are named explicitly in the
factor's `evidence` string (e.g. `"deal_value missing — no points
awarded"`). Nothing is fabricated: no invented deal values, intent,
conversion probabilities or contact details — scores come only from
stored lead fields and the persisted, verified recovery signals.

### The score breakdown format

Persisted per case in `recovery_cases.score_breakdown` and returned by
the API:

```json
{
  "factors": [
    { "factor": "business_value",   "points": 30.0, "max_points": 30.0,
      "evidence": "deal_value 12000000 = 100% of reference 10000000" },
    { "factor": "urgency",          "points": 25.0, "max_points": 25.0,
      "evidence": "never_contacted: 43080 min beyond 120 min SLA (full points at 10080 min)" },
    { "factor": "buying_intent",    "points": 0.0,  "max_points": 20.0,
      "evidence": "no prior contact recorded; no configured intent flag set" },
    { "factor": "recovery_potential","points": 15.0, "max_points": 15.0,
      "evidence": "signals: never_contacted (high)" },
    { "factor": "contactability",   "points": 10.0, "max_points": 10.0,
      "evidence": "phone available; email available; language hindi" }
  ],
  "explanation": "priority 80/100 (critical): business_value 30/30 (…); buying_intent 0/20 (…); …",
  "weights": { "business_value": 30.0, "urgency": 25.0, "buying_intent": 20.0,
               "recovery_potential": 15.0, "contactability": 10.0 }
}
```

Factor points always sum exactly to `priority_score` (per-factor
rounding to 2 decimals), so every score is auditable to the cent.

### How cases are ranked and persisted

- Scoring runs at the **end of every sweep**, inside the same
  transaction as the case upserts — cases and ranks commit (or roll
  back) together. Repeated runs are idempotent: unchanged inputs
  reproduce identical scores, ranks and breakdowns.
- Fresh lead data is read from `agent_leads` at scoring time, so a
  changed deal value or contact detail is **recalculated on the next
  sweep**.
- Ranking is **scoped per business** and ordered by
  `priority_score desc, score desc, case_id asc` — deterministic
  tie-breaking, stable pagination.
- New nullable columns on `recovery_cases` (migration
  `alembic/versions/0004_priority_scoring.py`):
  `priority_score` (Numeric 5,2), `priority_rank`, `priority_level`,
  `score_breakdown` (JSON), `scored_at`, plus index
  `ix_recovery_cases_business_priority`. The existing `score` column
  keeps its Module 3 meaning (0..1 recoverability) — unchanged.
- The sweep response gains a `priority` block:
  `{ "scored": N, "deranked_ineligible": N, "levels": {…},
     "order": "…", "scored_at": "…" }`.

### API changes and sample output

No new routes. `GET /api/v1/agent/recovery/cases` items gain additive
fields `priority_score`, `priority_rank`, `priority_level`,
`score_breakdown`, `scored_at` (all `null` for cases never ranked,
e.g. pre-migration rows before the next sweep), and the envelope gains
a `ranking` metadata block (see the verified Step 9 response above).
`GET /api/v1/agent/recovery/cases/{case_id}` carries the same item
fields. Every previously documented field and its meaning is preserved.

### Dashboard & Voice Calling consumption (future modules)

The dashboard can render directly from the cases API: total eligible
cases (`ranking.total_scored`), priority distribution
(`ranking.levels`), ranked leads (items ordered by `priority_rank`),
per-lead score + level + factor breakdown + `explanation`, the
existing recommended `action`/`channel`/`message` and the existing
`needs_human_approval` and `status` — no new aggregation endpoint
required. The future Voice Calling agent (Module 6) can pull the top
`critical`/`high` case and already receives the lead context, recovery
signals and approval requirements it needs in the same payload;
verified contact details (`phone`, `email`, `language`) come from the
canonical lead records (`GET /api/v1/agent/leads/{lead_id}`) — they
are **not** duplicated into recovery cases.

**Not implemented here (separate future responsibilities):** the
dashboard itself, placing calls, sending WhatsApp/SMS/email, and the
ACT stage workflow.

### Verify the implementation

```bash
cd backend

# Module 5 in isolation — pure engine + end-to-end ranking (50 tests)
python3 -m pytest tests/test_priority_scoring.py -q

# Regression: existing recovery detection / eligibility / resilience
python3 -m pytest tests/test_recovery_api.py tests/test_recovery_eligibility.py \
                 tests/test_recovery_detect.py tests/test_recovery_engine.py \
                 tests/test_recovery_resilience.py -q

# Full suite (expected: 427 passed) + lint
python3 -m pytest tests/ -q
python3 -m ruff check app tests alembic
```

Manual check against a running backend: import `sample.csv`, run the
sweep (`POST /api/v1/agent/recovery/run`), then read
`GET /api/v1/agent/recovery/cases` — items arrive in priority order
with full breakdowns, and the `ranking` block summarizes the run.


---

## 9. Part E — Recovery Dashboard (Modules 1-5 + AI Outreach)

The Recovery Dashboard is an operator-facing surface built on top of the existing recovery pipeline. **No Modules 1-5 behavior was changed**; the dashboard reuses their cases, priority scores, eligibility rules, and the existing Groq configuration. It adds no duplicate lead/recovery-case storage.

### New API routes (mounted under `/api/v1`)
| Method | Route | Purpose |
|---|--------|---------|
| GET | `/api/v1/agent/recovery/dashboard/summary` | Dashboard metrics from real data (totals, priority counts, awaiting outreach, outcomes only from persisted execution records) |
| GET | `/api/v1/agent/recovery/cases` | Ranked recovery cases (extended: `priority_level`, `query`, `needs_human_approval` filters; response includes `total` and `ranking`) |
| GET | `/api/v1/agent/recovery/cases/{case_id}` | Lead/case details |
| GET | `/api/v1/agent/recovery/cases/{case_id}/score-breakdown` | Priority score, level, factor breakdown, explanation, ranking reason |
| GET | `/api/v1/agent/recovery/cases/{case_id}/outreach-content` | Get or generate (on-demand) lead-specific AI/cached outreach pack |
| POST | `/api/v1/agent/recovery/cases/{case_id}/outreach-content/generate` | Regenerate content (guardrailed, uses existing Groq config, safe fallback) |
| GET | `/api/v1/agent/recovery/cases/{case_id}/outcomes` | Actual persisted outreach history/execution outcomes (never fabricated) |

All require `X-API-Key` and are business-scoped. Cross-tenant access returns `404` via `LeadNotFoundError`. 

### Priority-ranked table (fields returned)
`items[]` include: `case_id`, `lead_id`, `lead_name`, `business_id`, `score` (Module 3 recoverability 0-1), `priority_score` (0-100), `priority_rank`, `priority_level`, `score_breakdown`, `scored_at`, `signals`, `route`, `action`, `channel`, `message`, `rationale`, `engine`, `needs_human_approval`, `status`, `created_at`, `updated_at`. Filtering/search/pagination/sorting use the existing priority order.

### Lead details (verified only)
The dashboard reads verified contact/language/deal/source/status/custom_fields from `agent_leads` (no synthesis). A lead missing from `agent_leads` yields `None` for lead overview fields where appropriate.

### AI-generated call scripts and messages (Groq)
- **Reuse**: `app.modules.recovery_dashboard.outreach` uses `app.config.Settings` (`GROQ_API_KEY` / `CALLORA_GROQ_MODEL`) and the same `groq` package as the strategist. No new provider, no hardcoded keys.
- **Generation**: `GET .../outreach-content` generates and caches in `recovery_outreach_content` on first read; `POST .../generate` regenerates with guardrails and increments `generation_count`/records `regenerated_at`. Results are persisted per `(business_id, case_id)`.
- **Content**: voice-call script (opening lines, conversation flow, responses for interested/busy/callback/not_interested/no_answer, do-not-do), WhatsApp/SMS, email (subject/body), recovery rationale, context used, preferred-language content, quick copy, `channel_plan` (preferred vs. alternative channels, `delivery_status: draft_only`). All derived from verified case+lead data only.
- **Factual grounding**: the prompt separates `<verified_lead_fields>` from `<missing_or_unverified_fields>` and gates claims on `<contact_evidence>` and `<crm_evidence>` — contacted/not-contacted and CRM-drift claims are only allowed when a backend-verified signal/timestamp supports them; missing information is phrased neutrally or omitted. Lead-facing output is additionally scanned for fabricated delivery claims ("we have sent", "call was placed"), prior-conversation claims ("as we discussed"), and property/offer claims (ready/curated/available/discounted/guaranteed) — skipped only when the verified source data explicitly carries the term. The verified case `rationale`/`context_used` always win over model-written text (model text is kept separately as `model_rationale`/extra context keys, never in place of verified values).
- **Language quality**: lead language is resolved via an alias table (`hi`→`hindi`, etc.); missing/unsupported values fall back to `CALLORA_DEFAULT_OUTREACH_LANGUAGE` (default `english`) — Hindi is never hardcoded. Hindi drafts are natural conversational Hindi in Devanagari with gender-neutral phrasing (honorific "you" for the lead, impersonal/passive forms for the agent — no gendered first-person verbs, no Latin transliterations). Names, phone numbers and reference IDs are copied verbatim.
- **Agent identity**: drafts sign with `CALLORA_AGENT_DISPLAY_NAME` (gender-neutral label; default "Callora Team"). `[Agent Name]`/`{{agent_name}}`-style placeholders emitted by the model are substituted before validation; packs with unresolved placeholders are rejected.
- **Validation**: the structured response is validated against the full schema (required opening line, all five scripted responses, usable channel bodies, correct types) — not just "is a dict". Quick-copy is re-derived from the final drafts so it can never drift from the messages it mirrors.
- **Safety**: prompts treat lead/case blocks as DATA (not instructions), forbid fabricating prior conversations/visits/quotes/preferences/buying intent/offers/discounts; on any Groq error/timeout/malformed response/grounding violation the deterministic template is returned whole (`engine: rules-fallback`) — failed model output is never blended or "repaired" into lead-specific content. Content is labeled "draft for agent review only" and explicitly states no call/message was placed.
- **Guardrails**: regeneration blocks opted-out/suppressed leads, hard-blocked contacts, or leads already recovered/closed to recovery (`409 recovery_outreach_blocked`). 
- **History**: `content_generated` events written to `recovery_outreach_events` (append-only). Execution events (`call_completed`, `callback_requested`, `message_sent`, `outreach_failed`) are **only** reported if they exist in persisted records — never fabricated.

### New configuration
Uses existing variables: `GROQ_API_KEY` / `CALLORA_GROQ_API_KEY`, `CALLORA_GROQ_MODEL` (default `openai/gpt-oss-120b`). Optional new variables (safe defaults, no required env changes): `CALLORA_AGENT_DISPLAY_NAME` (outreach signer; neutral "Callora Team" when unset) and `CALLORA_DEFAULT_OUTREACH_LANGUAGE` (fallback for missing/unsupported lead language; default `english`).

### Database
New tables (migration `0005_recovery_outreach`): `recovery_outreach_content` (unique per business/case, caches pack + generation metadata), `recovery_outreach_events` (append-only history). No changes to existing tables.

### Startup/run
Migrations run automatically on startup in compose (`alembic upgrade head`). For local: `cd backend && alembic upgrade head && uvicorn app.main:app --reload`. Groq key optional (rules-fallback).

### Tests added/updated
- Route registration updated for dashboard routes.
- `tests/test_outreach_generation.py`: offline (Groq mocked, no key/network) regression coverage for natural gender-neutral Hindi + language aliases/fallbacks, agent identity & placeholder substitution, property/prior-conversation/delivery claim rejection, verified-vs-missing field separation, CRM-evidence gating, preferred/alternative channel plan, quick-copy/draft consistency, structured-output validation and malformed-response fallback, no-send/no-call guarantees, and backward compatibility of the generate/cache/regenerate responses. Existing recovery/priority tests unchanged behavior.

### Remaining limitations
- No actual telephony or WhatsApp/SMS/email sending is implemented. Only content generation and persisted history are tracked.
- Outcomes dashboard shows zeros unless real execution events are recorded by a future ACT stage.
- "Lead details" rely on `agent_leads` projection; if a lead projection is missing, only case data is shown (never synthesized).
- Groq output is best-effort and always falls back to deterministic content on failure (including grounding-violation rejection).
- The automated grounding scan is pattern-based and English-focused; for other languages the prompt rules are the primary control. Only `hindi`/`english` deterministic templates are supported — other lead languages fall back to `CALLORA_DEFAULT_OUTREACH_LANGUAGE`.


### Full API route listing (current)
```
GET  /api/v1/agent/leads
GET  /api/v1/agent/leads/by-external/{external_crm_id}
GET  /api/v1/agent/leads/{lead_id}
GET  /api/v1/agent/recovery/cases
GET  /api/v1/agent/recovery/cases/{case_id}
GET  /api/v1/agent/recovery/cases/{case_id}/outcomes
GET  /api/v1/agent/recovery/cases/{case_id}/outreach-content
POST /api/v1/agent/recovery/cases/{case_id}/outreach-content/generate
GET  /api/v1/agent/recovery/cases/{case_id}/score-breakdown
GET  /api/v1/agent/recovery/dashboard/summary
POST /api/v1/agent/recovery/run
GET  /api/v1/crm/connections
POST /api/v1/crm/connections
POST /api/v1/crm/sync
GET  /api/v1/imports/{import_id}
GET  /api/v1/imports/{import_id}/errors
GET  /api/v1/leads
POST /api/v1/leads
POST /api/v1/leads/import
GET  /api/v1/leads/{lead_id}
```

### Summary
- Recovery dashboard routes added: summary, cases (filtered/paginated/searchable), case get, score-breakdown, outreach-content (get/generate), outcomes. All business-scoped, use existing Groq config, no live calls required in tests, no fabrication, no telephony.
- Database: `recovery_outreach_content`, `recovery_outreach_events` (migration `0005_recovery_outreach`).
- Outreach generation hardened: verified-vs-missing field separation, CRM/contact evidence gating, grounding-violation rejection, full schema validation, quick-copy alignment, configurable agent identity, alias-aware language resolution with configurable fallback, natural gender-neutral Devanagari Hindi templates, preferred-vs-alternative channel plan.
- 490 tests passed (including `tests/test_outreach_generation.py`); ruff clean.
