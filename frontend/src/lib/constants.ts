export const LANGUAGE_NAMES: Record<string, string> = {
  en: "English",
  hi: "Hindi",
  kn: "Kannada",
};

export const SUPPORTED_LANGUAGES = ["en", "hi", "kn"] as const;

export const CALL_STATUSES = [
  "pending",
  "scheduled",
  "queued",
  "initiated",
  "ringing",
  "in_progress",
  "disconnected",
  "completed",
  "no_answer",
  "busy",
  "failed",
  "canceled",
  "stopped",
  "error",
  "balance_low",
  "blocked",
  "simulated",
] as const;

/** Statuses after which a call can no longer change on its own. */
export const TERMINAL_CALL_STATUSES = new Set([
  "completed",
  "no_answer",
  "busy",
  "failed",
  "canceled",
  "stopped",
  "error",
  "balance_low",
]);

export const ACTIVE_CALL_STATUSES = new Set([
  "pending",
  "scheduled",
  "queued",
  "initiated",
  "ringing",
  "in_progress",
  "disconnected",
]);

export const HANDOFF_STATUSES = [
  "requested",
  "initiated",
  "completed",
  "failed",
  "cancelled",
  "not_supported",
] as const;

export const HANDOFF_KINDS = ["warm_transfer", "callback"] as const;

export const CONSENT_STATUSES = ["granted", "revoked", "expired"] as const;

export const CONSENT_CHANNELS = ["voice", "sms", "email", "whatsapp"] as const;

export const KNOWLEDGE_CATEGORIES = [
  "faq",
  "product_feature",
  "pricing",
  "eligibility",
  "policy",
  "objection_handling",
  "escalation_rule",
] as const;

export const JOB_STATUSES = [
  "pending",
  "running",
  "succeeded",
  "failed",
  "dead",
] as const;

/** The backend stores calling days as integers where Monday is 0. */
export const CALLING_DAYS = [
  { value: 0, label: "Monday" },
  { value: 1, label: "Tuesday" },
  { value: 2, label: "Wednesday" },
  { value: 3, label: "Thursday" },
  { value: 4, label: "Friday" },
  { value: 5, label: "Saturday" },
  { value: 6, label: "Sunday" },
] as const;

export const TIMEZONES = [
  "Asia/Kolkata",
  "Asia/Dubai",
  "Asia/Singapore",
  "UTC",
] as const;

export const CALL_DISPOSITIONS = [
  "qualified",
  "not_qualified",
  "refused",
  "callback_requested",
  "no_conversation",
  "unknown",
] as const;
