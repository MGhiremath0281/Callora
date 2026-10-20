"""Recovery Dashboard module — API surface and content-generation helpers.

The content-generation helpers (deterministic pack, Groq availability,
language resolution, agent identity, channel plan) live in
``app.modules.recovery_dashboard.config``; they are re-exported here for
backward compatibility with the original module layout. The router and
repository are imported as submodules.
"""

from app.modules.recovery_dashboard.config import (  # noqa: F401
    ALLOWED_DIRECT_CHANNELS,
    ALLOWED_EMAIL_CHANNELS,
    VALID_SCRIPT_TONES,
    OutreachTemplate,
    dashboard_groq_available,
    dashboard_model,
    deterministic_outreach_pack,
)
