# Callora frontend

React + Vite + TypeScript frontend for the Callora outbound voice platform. It talks
only to the FastAPI backend and treats the backend as the source of truth for every
call, compliance decision, metric and audit record.

## Requirements

- Node.js 20 or newer
- A running Callora API (see `../backend`)

## Setup

```bash
cd frontend
npm install
cp .env.example .env      # optional in development
npm run dev
```

Open `http://localhost:5173`. Development proxies `/api/v1` to
`http://localhost:8000` by default.

The seeded development API key is `dev-key-biz-001`. Paste it into the login screen;
it is validated with `GET /voice/business` and then sent as `X-API-Key` on every
request.

### Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `VITE_API_BASE_URL` | `/api/v1` | Browser-visible API base. Use the Vite proxy locally or an absolute API origin in production. |
| `VITE_DEV_API_TARGET` | `http://localhost:8000` | Dev-server proxy target only; never shipped to the browser. |

Provider secrets, CRM keys and the Bolna webhook secret stay on the server. No secret
other than the user's workspace API key is present in the browser.

## Scripts

```bash
npm run dev        # Vite dev server
npm run typecheck  # TypeScript project build check
npm run test       # Vitest + Testing Library
npm run build      # Type-check and production bundle in dist/
npm run preview    # Serve the production bundle
```

## Product areas

| Area | What it does |
| --- | --- |
| Overview | Live workspace, call, consent, handoff, job and compliance summary with 30s refresh. |
| Agents | Shows the single voice business config and knowledge base. Agent Studio edits instructions, qualification questions and verified knowledge. |
| Leads | Search, create, inspect leads, maintain consent records, masked phone display. CSV import runs after explicit upload, sends the column mapping, polls import status and renders backend row errors. |
| Calls | Filterable call list, new-call dialog with backend compliance result shown verbatim, call details, transcript, recording, provider events and compliance decisions. |
| Handoffs | Queue filters, callback creation (never auto-dials), handoff status updates, pending workflow follow-up completion. |
| Campaigns | Explicit readiness page. See [Backend gaps](#backend-gaps). |
| Analytics | Funnel, call mix, volume, workflow runs, leakage, ROI, recommendations and operations snapshot computed from backend data. |
| Compliance & Audit | Compliance decisions and check details, audit trail, DND add/deactivate, background jobs with retry. |
| Settings | Workspace profile/languages, calling window and quotas, consent/DND policy, handoff defaults, CRM connection verification, workflow integration registration and API session details. |

## Backend-authoritative rules

- A blocked call is never rendered as a success. The API's 403 and compliance
  decision are surfaced unchanged.
- Agent activation and destructive actions require an explicit confirmation;
  activation requires typing `ACTIVATE`.
- Campaigns, multi-agent CRUD, team/roles, notifications, voice preview and
  provider-side agent tests are not implemented by the backend. The UI shows a
  dependency/unavailable state instead of fabricating records or success states.
- Analytics only renders metrics supplied by the API. Missing samples show
  "insufficient data" or "unavailable"; the UI does not substitute demo values.
- Lead phone numbers are masked in list views. Consent and DND values show unknown
  when enrichment fails rather than assuming compliance.

## Backend gaps

These capabilities have no endpoint in the current API contract and therefore have
no working controls in this UI:

- campaign creation, audience segments or scheduled launches;
- more than one voice agent per business (the backend exposes one voice business
  config and its knowledge base);
- member, invitation, role and notification-preference management;
- voice preview and provider-side test-agent calls;
- a public campaign/report export API.

The Campaigns page lists the required backend capabilities. This is intentional, so
the frontend never simulates a launch that cannot be executed or audited.

## Authentication and security notes

This MVP uses an API-key-first browser session:

1. `GET /voice/business` validates the key.
2. The key is stored in `localStorage` under `callora.apiKey` and attached as
   `X-API-Key`.
3. Any `401` clears the key and returns the user to `/login`.

`localStorage` is convenient for a single-tenant operator tool but is readable by
any script in the origin. For a public multi-user deployment, replace this with an
HttpOnly session or a backend-for-frontend that keeps provider credentials and the
workspace key off the client. The frontend never stores provider, CRM or webhook
secrets.

## Tests

Vitest covers the CSV parser/mapping, form schemas, transcript parser, typed
confirmation dialog, primary navigation, anonymous routing and the Campaigns
unavailable state. Run `npm run test`.

## Follow-up question integration

The live-call follow-up endpoint is owned by the backend. The frontend does not
change it. Diagnosis of the live follow-up failure is documented in the task report;
the likely integration points to verify are the Bolna tool URL/public reachability,
the `business_id`/`call_id` placeholder values in the Bolna custom task, webhook
signature configuration, and whether verified knowledge items exist for the business.
