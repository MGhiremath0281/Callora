"""Outbound voice agent module — the Bolna-backed sales calling backend.

This package owns every concern that belongs to "place a call, prove it was
allowed to happen, and make the result useful":

    models       — businesses, consent, DND, calls, webhook events, outcomes,
                   handoffs, compliance decisions, audit log, background jobs
    repository   — all SQL access for the module (no SQL in routers)
    compliance   — the pre-call gate; every outbound call passes through it
    providers    — the voice-provider adapter (Bolna) behind a stable port
    orchestrator — call initiation and state transitions, provider-agnostic
    webhooks     — provider event intake: signature checks, dedup, ordering
    postcall     — the LangGraph workflow that turns a call into structured
                   intelligence (summary, qualification, refusal, briefing)
    handoff      — warm-transfer / callback-request workflow
    jobs         — durable PostgreSQL job queue and the background worker
    api          — the HTTP surface (businesses, leads, calls, handoffs, ops)

Design rules that the whole module obeys:

* No outbound call can skip the compliance gate. The orchestrator is the
  only code path that reaches a provider, and it always evaluates the gate
  first.
* Mandatory compliance checks fail closed. If a check cannot be completed
  the call is blocked, never approved.
* PostgreSQL is the source of truth. Redis only accelerates lookups and
  holds short-lived state; losing it never turns a block into an approval.
* Provider-specific behaviour lives behind ``providers.base.VoiceProvider``.
  Swapping Bolna for another provider must not touch the orchestrator.
"""

from app.modules.voice import constants  # noqa: F401  (re-export for convenience)

__all__ = ["constants"]
