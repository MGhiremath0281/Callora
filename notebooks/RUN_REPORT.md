# Recovery Agent — End-to-End Test Report

| | |
|---|---|
| **Run date** | 2026-10-19 |
| **Branch / commit** | `feature/recovery-agent-notebook` |
| **Notebook** | [`notebooks/recovery_agent.ipynb`](./recovery_agent.ipynb) |
| **LLM** | Groq `openai/gpt-oss-120b` (live API, key from git-ignored `.env`) |
| **Result** | ✅ **EXECUTED OK — 24 cells, 0 errors, 10.4 s** |
| **Mode** | Live LLM for judgment steps; pure rules for detect/score/execute |

> The notebook is **standalone** — it imports nothing from `backend/app/`, so it runs
> independently of any unmerged branch. Re-run it top-to-bottom to reproduce this report.

---

## Verdict: all checks passed

| # | Check | Evidence |
|---|-------|----------|
| 1 | Full pipeline executes end-to-end | 24/24 cells, 0 errors, 10.4 s |
| 2 | **Groq LLM actually used** for decisions | `engine=groq:openai/gpt-oss-120b` on all 4 strategist plans |
| 3 | Rules-only fallback works without a key | verified in a prior run: every plan `engine=rules-fallback`, pipeline still completed |
| 4 | **Guardrails hard-block before the LLM** | opt-out lead (lead_p5) → `route=block`, never sent to LLM, archived |
| 5 | Guardrails re-checked at execution | `blocked at execution: lead opted out of outreach` |
| 6 | Healthy leads correctly ignored | `No signal (correctly ignored): ['lead_p3', 'lead_p6']` |
| 7 | **Language respected** | Hindi lead received a Hindi WhatsApp message (see §5) |
| 8 | Scoring is deterministic & ordered | R=1.000 → 0.470, threshold 0.40 |
| 9 | State machine integrity | all transitions passed `assert` checks — 0 illegal transitions |
| 10 | Counts reconcile | 5 cases = 2 recovered + 2 exhausted + 1 archived; 5 audit entries for 5 actions |
| 11 | Idempotency | every dispatch carries a `case:attempts:action` idempotency key |
| 12 | Key never committed | `.env` matched by `.gitignore` line 4 (`git check-ignore` verified) |

---

## Stage 1 — DETECT (pure rules, no LLM)

7 signals across 5 of 7 leads; 2 healthy leads correctly ignored (one still within SLA,
one contacted 1 h ago):

```
DETECTED 7 signals

  lead_p1    never_contacted          [high  ] no contact 2880 min after creation (SLA 120 min)
  lead_p2    stalled_follow_up        [medium] last touch 10 days ago
  lead_p4    never_contacted          [high  ] no contact 300 min after creation (SLA 120 min)
  lead_p4    crm_sync_drift           [medium] lead not present in external CRM
  lead_p5    never_contacted          [high  ] no contact 4320 min after creation (SLA 120 min)
  lead_p7    never_contacted          [high  ] no contact 360 min after creation (SLA 120 min)
  lead_p7    event_delivery_failed    [high  ] lead.created outbox event exhausted retries

No signal (correctly ignored): ['lead_p3', 'lead_p6']
```

Signals implemented: `never_contacted` (S1), `stalled_follow_up` (S2),
`crm_sync_drift` (S6, Module 2), `event_delivery_failed` (S7, outbox).

## Stage 2 — ASSESS (pure math, no LLM)

Recoverability score `R = 0.35·VALUE + 0.25·RECENCY + 0.20·REACHABILITY + 0.15·PRIOR + 0.05·DRIFT − PENALTY`:

```
TRIAGE: 5 candidates (threshold 0.4)

  lead_p4    R=1.000  Sana Khan       ₹  12,000,000  [never_contacted, crm_sync_drift]
  lead_p1    R=0.862  Rahul Sharma    ₹   7,500,000  [never_contacted]
  lead_p7    R=0.825  Arjun Desai     ₹   5,000,000  [never_contacted, event_delivery_failed]
  lead_p2    R=0.682  Meera Iyer      ₹   4,500,000  [stalled_follow_up]
  lead_p5    R=0.470  Vikram Rao      ₹   2,000,000  [never_contacted]
```

## Stage 3 — DECIDE (⭐ Groq LLM)

Routing: 4 candidates → LLM (judgment + message composition), 1 → **blocked** (opt-out, rules only — the LLM never sees it).

```
lead_p4  route=llm  engine=groq:openai/gpt-oss-120b  action=re_contact_lead  approve=False
         channel=whatsapp message: नमस्ते सना जी, मैं कॉलोरा से हूँ। आपके प्रॉपर्टी की जानकारी के लिए कृपया हमसे संपर्क करें। धन्यवाद।
lead_p1  route=llm  engine=groq:openai/gpt-oss-120b  action=re_contact_lead  approve=False
         channel=email message: Hi Rahul, we noticed you showed interest via 99acres. We'd love to help you find your perfect property. Please let us know a convenient time to discuss.
lead_p7  route=llm  engine=groq:openai/gpt-oss-120b  action=re_contact_lead  approve=False
         channel=sms message: Hi Arjun, we noticed you visited our website and would love to help with your needs. Please let us know a convenient time to chat.
lead_p2  route=llm  engine=groq:openai/gpt-oss-120b  action=re_contact_lead  approve=False
         channel=whatsapp message: Hi Meera, we noticed we haven't heard from you in a while. Let us know if you have any questions or need assistance with your inquiry.
lead_p5  route=block engine=rules-fallback           action=archive          approve=False
```

**Highlights:**
- **Sana Khan's lead carries `language: hindi` → the model composed a Hindi message and picked WhatsApp** — exactly the designed language/channel behavior
- The model chose channels per lead (whatsapp / email / sms) instead of the rules' default
- Opt-out lead: `route=block` → the LLM was never invoked (cost & compliance)
- Output is strict JSON, validated against an action enum; invalid output falls back to rules (with a diagnostic)

## Stage 4 — ACT (deterministic executor, no LLM)

```
5 actions dispatched

  lead_p4  re_contact_lead  -> sent     (whatsapp -> नमस्ते सना जी, ...)
  lead_p1  re_contact_lead  -> sent     (email -> Hi Rahul, ...)
  lead_p7  re_contact_lead  -> sent     (sms -> Hi Arjun, ...)
  lead_p2  re_contact_lead  -> sent     (whatsapp -> Hi Meera, ...)
  lead_p5  archive          -> archived (blocked at execution: lead opted out of outreach)
```

Note the **second** guardrail hit: even though the plan said `archive`, the executor
re-ran `hard_block()` — defense in depth. All 5 actions recorded in the audit trail.

## Stage 5 — Case state machine

```
(2 simulated lead replies applied)
  case_1   lead_p4    R=1.000 RECOVERED    DETECTED -> CANDIDATE -> IN_RECOVERY -> RECOVERED
  case_2   lead_p1    R=0.862 RECOVERED    DETECTED -> CANDIDATE -> IN_RECOVERY -> RECOVERED
  case_3   lead_p7    R=0.825 EXHAUSTED    DETECTED -> CANDIDATE -> IN_RECOVERY -> EXHAUSTED
  case_4   lead_p2    R=0.682 EXHAUSTED    DETECTED -> CANDIDATE -> IN_RECOVERY -> EXHAUSTED
  case_5   lead_p5    R=0.470 ARCHIVED     DETECTED -> CANDIDATE -> ARCHIVED
```

Every transition passed the state machine's `assert` (illegal transitions raise).

## Stage 6 — MEASURE

```
  cases_opened             5
  closed                   5
  recovered                2
  recovery_rate            0.4
  messages_sent            4
  cost                     2.0        (Rs.0.5 per message)
  value_recovered          Rs.19,500,000
  plays_used               {'re_contact_lead': 4, 'archive': 1}
  plays_that_recovered     {'re_contact_lead': 2}
  audit_entries            5
```

**4 messages → Rs.19.5M of deal value re-engaged (Rs.2.0 cost).**

## Stage 7 — AUDITOR (⭐ Groq LLM, suggestions are human-reviewed)

```
  * Set a minimum of 2 contact attempts per open case before archiving
      why: Only 4 re_contact_lead plays were used across 5 cases, and only half resulted in recovery...
      impact: Raising the recovery rate from ~40% toward 60% without significantly raising cost
  * Introduce a 24-hour SLA for initial response to new cases
      why: Prompt engagement is critical; delayed responses can reduce the chance of recovery...
  * Adjust default communication channel to prioritize SMS for high-value leads (value > Rs.5M)
      why: SMS has higher open rates than email; targeting high-value leads can boost recovery odds
```

---

## Final run summary

```
======================================================================
RECOVERY AGENT - END-TO-END RUN COMPLETE
======================================================================
 - Detected 7 signals across 5 leads
 - Triaged to 5 recovery candidates (R >= 0.4)
 - Strategist produced 5 plans (engine: groq:openai/gpt-oss-120b)
 - Executor dispatched 5 actions, 4 messages (0 deduped)
 - Cases closed: 2 recovered / 5 total -> recovery rate 40%
 - Value re-engaged: Rs.19,500,000 at est. cost Rs.2.0
 - Audit trail: 5 entries (every action replayable)
```

## Architecture recap — where the LLM is (and isn't)

| Step | Engine | Why |
|------|--------|-----|
| Detect signals | pure rules | cheap, complete, auditable |
| Score recoverability | pure math | deterministic |
| **Choose play + compose message** | **Groq LLM** | judgment, tone, language |
| Execute tools | deterministic | guardrails must not be approximated |
| **Audit / recommendations** | **Groq LLM** | synthesis; human approves changes |

## How to reproduce

```bash
git clone -b feature/recovery-agent-notebook https://github.com/MGhiremath0281/Callora.git
cd Callora
echo 'GROQ_API_KEY=gsk_...' > .env        # git-ignored, never committed
pip install groq jupyter
jupyter notebook notebooks/recovery_agent.ipynb   # Kernel → Restart & Run All
```

Without a key the notebook still runs end-to-end in **rules-only fallback** mode
(plans show `engine=rules-fallback`).

### Known environment note
Groq model access is per-account: `llama-3.3-70b-versatile` and `llama-3.1-8b-instant`
both return **404 on this key** (verified via `GET /v1/models`). The default is
`openai/gpt-oss-120b`; switch anytime with `GROQ_MODEL=<id>` in `.env` — no code changes.

### Next step (after the main refactor PR merges)
Port cells → `backend/app/modules/recovery_agent/` (mapping table in notebook §10):
FastAPI routers, PostgreSQL case tables, `X-API-Key` tenancy, outbox events, and
scheduled sweeps — all already part of the design doc.
