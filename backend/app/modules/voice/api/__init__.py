"""HTTP surface of the outbound voice module.

    businesses  the voice configuration of a business (the policy)
    leads       voice state, consent evidence, DND registry
    calls       call initiation and the call lifecycle
    handoffs    warm transfers and callback requests
    ops         jobs, audit trail, compliance decisions, statistics
    webhooks    the provider-facing endpoints (signature authenticated)

Every endpoint except the webhooks requires the ``X-API-Key`` header and
is scoped to the business that header resolves to.
"""

from app.modules.voice.api import businesses, calls, handoffs, leads, ops, webhooks  # noqa: F401

__all__ = [
    "businesses",
    "calls",
    "handoffs",
    "leads",
    "ops",
    "webhooks",
]
