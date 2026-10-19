# Callora Backend

## Module 1 — Lead & Customer Import

Accept · Map · Validate · Deduplicate · Store

Businesses import existing customer/lead data from heterogeneous sources
(CSV uploads, website/lead-generation forms, external applications).
Callora maps every incoming format onto **one canonical lead schema**,
which is what lets the platform support real estate, healthcare,
education, and B2B SaaS without per-industry database structures.

### Running

```bash
docker compose up --build        # starts PostgreSQL + backend (port 8001)
cd backend && alembic upgrade head && uvicorn app.main:app --reload
```

Database schema is managed by Alembic; the container applies migrations
on startup.

### API endpoints

All business routes are served **only** under `/api/v1`. Interactive
docs: `/docs`.

**Module 1 — Leads & Imports**

| Method | Endpoint                    | Purpose                                   |
|--------|-----------------------------|-------------------------------------------|
| POST   | `/api/v1/leads/import`      | Upload a CSV and run the import pipeline  |
| POST   | `/api/v1/leads`             | Create a lead from a JSON request         |
| GET    | `/api/v1/leads`             | List leads (`source`, `query`, `created_after/before`, `page`, `page_size`) |
| GET    | `/api/v1/leads/{lead_id}`   | Retrieve an individual lead               |
| GET    | `/api/v1/imports/{import_id}` | Import status and summary               |
| GET    | `/api/v1/imports/{import_id}/errors` | Row-level validation/duplicate errors |

**Module 2 — CRM Synchronization**

| Method | Endpoint                    | Purpose                                   |
|--------|-----------------------------|-------------------------------------------|
| GET    | `/api/v1/crm/connections`   | List the business's CRM connections       |
| POST   | `/api/v1/crm/sync`          | Manually synchronize one lead with the CRM |

**Agent Leads (downstream AI agents)**

| Method | Endpoint                    | Purpose                                   |
|--------|-----------------------------|-------------------------------------------|
| GET    | `/api/v1/agent/leads`       | List agent-ready leads (paginated, business-scoped) |
| GET    | `/api/v1/agent/leads/{lead_id}` | Retrieve an agent-ready lead          |
| GET    | `/api/v1/agent/leads/by-external/{external_crm_id}` | Find a lead by external CRM ID |

### Authentication

Every endpoint requires an `X-API-Key` header identifying the business.
Keys are configured with:

```bash
CALLORA_API_KEYS="biz_001:key1,biz_002:key2"
```

`business_id` is always derived from the authenticated context — it is
never trusted from uploaded or submitted data. Cross-tenant reads return
404.

### CSV import workflow

1. **Upload** a spreadsheet with any column names/ordering, optionally
   with a `mappings` JSON form field to confirm ambiguous columns:

   ```bash
   curl -X POST /api/v1/leads/import -H "X-API-Key: $KEY" \
        -F file=@leads.csv \
        -F 'mappings={"Source Channel": "source"}'
   ```

2. **Detect & map** — headers are matched against the canonical fields
   (`name`, `phone`, `email`, `source`, `language`, `deal_value`,
   `last_contact_at`, `external_id`) using synonyms plus fuzzy matching.
   Low-confidence suggestions are reported as `ambiguous` and withheld
   until the business confirms them. `First Name` + `Last Name` columns
   are combined into `name`.
3. **Validate** — required fields (name, phone, source), phone/email/
   date/number formats, and configurable file-size and row limits.
4. **Normalize** — consistent formats without guessing ambiguous values
   (e.g. a 10-digit mobile stays as-is; no country code is invented).
5. **Deduplicate** — against existing leads for the business *and*
   within the uploaded file, keyed on normalized phone.
6. **Import** — valid rows are persisted; small files run synchronously,
   files above `CALLORA_SYNC_PROCESSING_ROW_THRESHOLD` rows run in the
   background (`202 Accepted`, poll `/api/v1/imports/{id}`).
7. **Report** — `created` + `duplicates` + `rejected` always reconcile
   with `total_rows`; row-level errors are served from
   `/api/v1/imports/{id}/errors`.
8. **Events** — each committed lead atomically stages a `lead.created`
   outbox event (see below).
9. **Agent-ready data** — every imported lead is projected into
   `agent_leads`, so the Agent Leads endpoints expose it immediately.
10. **Automatic CRM sync** — after persistence, if the business has an
    active CRM connection the imported leads are synchronized in the
    background: external CRM IDs, sync status, per-lead results and
    idempotency records are persisted. CRM failures are recorded but
    never affect the import — the leads remain stored and agent-visible
    whether or not CRM synchronization succeeds.

Unmapped columns are captured into the lead's `custom_fields`
(configurable via `CALLORA_AUTO_CAPTURE_CUSTOM_FIELDS` and
`CALLORA_CUSTOM_FIELD_ALLOWLIST`), which is how industry-specific
attributes (RERA numbers, course names, patient ids, ...) survive
without schema changes. External system identifiers map to
`external_id` for Module 2 (CRM Synchronization).

Retrying an import never creates duplicate records: the database-level
uniqueness constraint on `(business_id, phone)` is the final backstop,
and `Idempotency-Key` headers replay prior import/create results.

### Events (transactional outbox)

`lead.created` events are written to the `outbox_events` table in the
same transaction as the lead itself:

```json
{"event": "lead.created", "lead_id": "lead_001", "business_id": "biz_001"}
```

The event is a notification, not the record — downstream modules
retrieve the canonical lead via the API or a shared service. When
`CALLORA_EVENT_WEBHOOK_URL` is set, pending events are POSTed there with
retry/backoff (`CALLORA_EVENT_PUBLISH_MAX_ATTEMPTS`); otherwise modules
2 and 3 read the outbox directly.

### Error responses

All errors share one envelope:

```json
{"error": {"code": "invalid_phone", "message": "...", "details": [...]}}
```

| Code                        | HTTP | Meaning                                  |
|-----------------------------|------|------------------------------------------|
| `authentication_failed`     | 401  | Missing or invalid API key               |
| `lead_not_found` / `import_not_found` | 404 | Missing or cross-tenant resource |
| `conflict`                  | 409  | Duplicate lead (same phone in business)  |
| `request_validation_error`  | 422  | Malformed JSON payload                   |
| `malformed_file`            | 422  | Empty/unparseable CSV, bad `mappings`    |
| `payload_too_large`         | 413  | File exceeds `CALLORA_MAX_UPLOAD_SIZE_MB`|
| `row_limit_exceeded`        | 413  | Rows exceed `CALLORA_MAX_ROWS_PER_IMPORT`|
| `import_failed`             | 500  | Import crashed; the failed batch report remains available at `/api/v1/imports/{id}` |
| `internal_error`            | 500  | Unexpected failure                       |

### Configuration (all `CALLORA_`-prefixed)

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql+psycopg2://callora:callora@localhost:5432/callora` | PostgreSQL connection |
| `API_KEYS` | `biz_001:dev-key-biz-001` | `business_id:api_key` pairs |
| `MAX_UPLOAD_SIZE_MB` | `10` | CSV size limit |
| `MAX_ROWS_PER_IMPORT` | `50000` | Row-count limit |
| `SYNC_PROCESSING_ROW_THRESHOLD` | `1000` | Above this, imports run in the background |
| `DEFAULT_LEAD_SOURCE` | `import` | Fallback when no source column exists |
| `AUTO_CAPTURE_CUSTOM_FIELDS` | `true` | Keep unmapped columns in `custom_fields` |
| `CUSTOM_FIELD_ALLOWLIST` | *(empty)* | Optional comma-separated allowlist |
| `EVENT_WEBHOOK_URL` | *(unset)* | Optional `lead.created` delivery URL |
| `EVENT_PUBLISH_MAX_ATTEMPTS` | `5` | Delivery attempts before `failed` |
| `LOG_LEVEL` / `DEBUG` | `INFO` / `false` | Structured JSON logs; `debug` re-raises masked 500s |

### Project structure

```
backend/app/
├── main.py                 # FastAPI app, router registration
├── config.py               # CALLORA_-prefixed settings
├── models.py               # SQLAlchemy models (leads, imports, outbox, ...)
├── auth.py / errors.py / logging_config.py
├── db/                     # engine, sessions, declarative base
├── api/                    # HTTP layer only (leads.py, imports.py)
└── modules/
    ├── lead_import/        # Module 1: schemas, service, validators,
    │                       #          normalizer, mapping, dedup, events
    └── crm_sync/           # Module 2: external CRM synchronization
```

Every lead creation mechanism (CSV upload, JSON API) resolves to one
standardized output schema — `LeadRead` in
`app/modules/lead_import/schemas.py`:

```json
{
  "id": "lead_6d2c8499a1d44681",
  "business_id": "biz_001",
  "name": "Rahul Sharma",
  "phone": "9876543210",
  "email": "rahul.test@example.com",
  "source": "99acres",
  "language": "english",
  "deal_value": "7500000.00",
  "last_contact_at": null,
  "custom_fields": {"property_type": "Apartment", "city": "Bengaluru"},
  "external_id": null,
  "created_at": "2026-10-18T18:17:27.814533"
}
```

`phone` is always a string and `deal_value` always a decimal string with
two fractional digits. This is the contract downstream modules consume.

### Tests

```bash
cd backend
pip install -r requirements.txt
pytest tests -q        # 168 tests: unit, API, integration, auto-CRM-sync, route registration, failure handling, schema verification
```

Tests run on an isolated SQLite database per test (models use portable
types); production runs PostgreSQL.
