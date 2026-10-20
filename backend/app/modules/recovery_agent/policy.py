"""Configured business-rule inputs for the deterministic eligibility gate.

The eligibility rules in :mod:`app.modules.recovery_agent.eligibility` are
*always* pure — they never touch ``Settings`` or a database session.
This module is the single place that turns the configured business rules
(``CALLORA_RECOVERY_*``) into the immutable value objects those rules
consume, with conservative defaults when configuration is unavailable.

Defaults reflect the existing business behaviour: a phone is the only
contact channel the engine can actually use, and unknown/missing contact
information must never be read as eligibility evidence.
"""

from dataclasses import dataclass
from typing import FrozenSet, Optional

from app.config import Settings, get_settings
from app.modules.recovery_agent.detect import EXCLUDED_STATUSES


# Extra statuses that are not "actively handled" but must still be
# treated as non-open opportunities by the eligibility gate.
_EXTRA_EXCLUDED_STATUSES: FrozenSet[str] = frozenset(
    {"dismissed", "suppressed", "resolved", "completed", "recovered", "in_recovery"}
)

# Default opt-out flags (mirrors ``Settings.recovery_opt_out_flags``) so
# the pure functions behave identically without a Settings object.
_DEFAULT_OPT_OUT_FLAGS: FrozenSet[str] = frozenset(
    {"opt_out", "opted_out", "do_not_contact", "dnc", "unsubscribed", "suppressed"}
)


@dataclass(frozen=True)
class EligibilityConfig:
    """Business rules the eligibility check is evaluated against.

    ``contact_channels`` is ordered by preference and only contains
    channels the engine can actually *use* — anything not listed is not
    a substitute for real contact information, so a lead missing every
    listed channel is excluded (rather than scored optimistically).
    """

    allowed_statuses: FrozenSet[str] = EXCLUDED_STATUSES | _EXTRA_EXCLUDED_STATUSES
    contact_channels: tuple = ("phone",)
    opt_out_flags: FrozenSet[str] = _DEFAULT_OPT_OUT_FLAGS
    recovered_statuses: FrozenSet[str] = frozenset({"recovered"})
    ignored_statuses: FrozenSet[str] = frozenset(
        {"dismissed", "suppressed", "resolved", "completed", "in_recovery"}
    )
    require_reachability: bool = True


def _is_eligibility_config(obj: object) -> bool:
    return isinstance(obj, EligibilityConfig)


def eligibility_config(settings: Optional[object] = None) -> EligibilityConfig:
    """Build the eligibility config from application settings.

    Accepts :class:`app.config.Settings` or an existing
    :class:`EligibilityConfig` (idempotent) so callers do not need to
    know which type they hold. Falls back to :func:`get_settings`
    (and, failing that, the dataclass defaults) when ``None``.
    """
    if _is_eligibility_config(settings):
        return settings  # type: ignore[return-value]

    if settings is None:
        try:
            settings = get_settings()
        except Exception:  # noqa: BLE001 - configuration must never crash the gate
            settings = None

    if settings is None:
        return EligibilityConfig()

    # Settings-like object
    opt_out_flags = getattr(settings, "recovery_opt_out_flag_set", None)
    if opt_out_flags is None:
        # Fallback to direct attributes / defaults
        opt_out_flags = _DEFAULT_OPT_OUT_FLAGS
    else:
        opt_out_flags = frozenset(opt_out_flags) or _DEFAULT_OPT_OUT_FLAGS

    recovered = getattr(settings, "recovery_recovered_status_set", None)
    if recovered is None:
        recovered = frozenset({"recovered"})
    else:
        recovered = frozenset(recovered) or frozenset({"recovered"})

    ignored = getattr(settings, "recovery_ignored_status_set", None)
    if ignored is None:
        ignored = EligibilityConfig().ignored_statuses
    else:
        ignored = frozenset(ignored) or EligibilityConfig().ignored_statuses

    return EligibilityConfig(
        opt_out_flags=opt_out_flags,
        recovered_statuses=recovered,
        ignored_statuses=ignored,
    )


def recovered_statuses(settings: Optional[object] = None) -> FrozenSet[str]:
    """The configured set of canonical statuses meaning "already recovered".""" 
    if _is_eligibility_config(settings):
        return settings.recovered_statuses  # type: ignore[return-value]
    return eligibility_config(settings).recovered_statuses
