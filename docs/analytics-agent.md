# Module 7 — Analytics Agent

The **Analytics Agent** analyzes Callora's call transcripts, lead funnel, recovery outcomes, follow-ups, and call costs to identify sales leakage and measure business performance.

---

## 1. Capabilities & Scope

| Component | What it does | Key Metrics / Insights |
|---|---|---|
| **Conversation Analytics** | Evaluates customer transcripts, post-call outcomes, and memory | Customer intent, sentiment classification (positive/neutral/negative score), detected objections, grouped refusal reasons |
| **Sales Leakage Detection** | Traces leads through 5 funnel stages and calculates dropped opportunities | Stage drop-off rate, stage conversion rate, potential deal value, lost revenue per stage, grouped loss reasons |
| **Recovery Analytics** | Measures performance across neglected lead recovery cases | Recovered cases, in-recovery count, recovery rate (%), conversion rate (%), priority level breakdown |
| **Performance Analytics** | Tracks calling efficiency and follow-up fulfillment | Connection rate (%), qualification rate (%), callbacks requested vs completed, warm transfers completed, average duration |
| **Cost & Revenue Analysis** | Measures outreach spend against recorded recovered revenue | Total call spend, recorded revenue recovered, cost per recovered opportunity (CAC), cost per qualified lead, ROI multiple |
| **Interactive Dashboard** | Operator-facing visual glassmorphism dashboard | Responsive KPI cards, funnel drop-off visualization, horizontal loss-reason bars, sentiment breakdown, call telemetry table |

---

## 2. API Endpoints

All endpoints are available at root `/analytics/...` and versioned `/api/v1/analytics/...`. Authentication via `X-API-Key` is supported, with graceful fallback to `business_id` query parameter.

| Method | Path | Description |
|---|---|---|
| `GET` | `/analytics/summary` | High-level summary metrics across funnel, recovery, performance, and financials |
| `GET` | `/analytics/leakage` | Stage-by-stage sales leakage analysis, grouped loss reasons, and AI recommendations |
| `GET` | `/analytics/conversations` | Per-call insights, customer intent, objections, sentiment breakdown, and grouped refusals |
| `GET` | `/analytics/dashboard` | Interactive web dashboard UI with real-time KPI cards and visual charts |
| `GET` | `/dashboard` | Top-level dashboard alias |

---

## 3. Sales Funnel Stages & Leakage Analysis

```
[1. Lead Ingestion] ────► [2. Outreach Attempted] ────► [3. Call Connected]
         │                          │                         │
         ▼ Dropped                  ▼ Dropped                 ▼ Dropped
 (DND / Uncontacted)       (No Answer / Busy)        (Objections / Disqualified)
                                                              │
                                                              ▼
[6. Closed Won / Recovered] ◄──── [5. Handoff Engaged] ◄──── [4. Sales Qualified]
         ▲                                  ▲                         ▲
         │ Dropped                          │ Dropped                 │ Dropped
 (Exhausted Attempts)              (Declined Transfer)       (Disqualified)
```

For each stage:
- **Entered & Converted**: Absolute counts and conversion percentages.
- **Dropped & Drop-off Rate**: Percentage of leads lost at this step.
- **Potential vs Lost Deal Value**: Real pipeline value at risk based on lead deal sizes.
- **Top Loss Reasons**: Grouped reasons attributing why prospects dropped out.

---

## 4. Conversation Intelligence & Refusal Categorization

The Analytics Agent standardizes refusal reasons into canonical business buckets:
1. **Pricing & Budget**: Deals stalled on price, subscription cost, or lack of financing.
2. **Timing & Postponement**: Leads occupied, driving, or deferring decision to next quarter.
3. **Competitor / Alternate Solution**: Prospective buyers using an existing tool or alternate provider.
4. **Not Interested / No Need**: Leads without active requirements.
5. **Contact Preference / DND**: Opt-outs and frequency preferences.
6. **Eligibility & Technical Fit**: Team size, prerequisites, or geographic limits.
7. **Other / General Refusal**: Unspecified declines.

---

## 5. Verification & Testing

Run unit and API tests with:
```bash
python -m pytest tests/test_analytics_agent.py -v
```

Tests cover:
- Summary metrics calculation with zero division safety.
- Stage-by-stage sales leakage calculations.
- Transcripts intent, sentiment, and objection extraction.
- Grouped refusal reasons and per-call insights.
- Pagination (`limit`, `offset`) and sentiment filtering.
- HTML dashboard response validation.
- Multi-tenant data isolation.
