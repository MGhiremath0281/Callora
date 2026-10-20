"""Module 3 — Recovery Agent (Opportunity Detection).

Detects neglected or lost leads from the agent-ready lead store and
produces recovery recommendations for downstream workflow stages.

Pipeline (ported from the validated ``notebooks/recovery_agent.ipynb``
prototype; LLM only where judgment is needed):

    fetch       — GET /api/v1/agent/leads with X-API-Key (client.py)
    guard       — intra-batch dedupe and the existing-case guard: a case
                  already past ``recommended`` (in recovery / recovered /
                  exhausted / archived) is never re-opened by a sweep
    detect      — pure rules: never-contacted, stalled, CRM drift (with
                  positive evidence), event delivery failure, already
                  recovered; active/converted leads are excluded
    eligibility — pure, deterministic business rules: lifecycle, consent,
                  contact validity and recovery evidence (eligibility.py);
                  consumes the detected signals so DB-confirmed evidence
                  (e.g. CRM drift) counts, and runs BEFORE scoring so an
                  ineligible opportunity can never become a candidate via
                  a recommendation
    assess      — deterministic recoverability score
    decide      — guardrails first (hard blocks), then Groq LLM chooses the
                  play and composes the message; rules-fallback without a key
    store       — recovery_cases rows, idempotent per (business, lead);
                  the hand-off surface for downstream components
"""
