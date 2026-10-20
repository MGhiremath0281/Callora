"""Module 3 — Recovery Agent (Opportunity Detection).

Detects neglected or lost leads from the agent-ready lead store and
produces recovery recommendations for downstream workflow stages.

Pipeline (ported from the validated ``notebooks/recovery_agent.ipynb``
prototype; LLM only where judgment is needed):

    fetch   — GET /api/v1/agent/leads with X-API-Key (client.py)
    detect  — pure rules: never-contacted, stalled, CRM drift, event
              delivery failure; active/converted leads are excluded
    assess  — deterministic recoverability score
    decide  — guardrails first (hard blocks), then Groq LLM chooses the
              play and composes the message; rules-fallback without a key
    store   — recovery_cases rows, idempotent per (business, lead);
              the hand-off surface for downstream components
"""
