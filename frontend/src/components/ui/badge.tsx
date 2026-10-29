import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type Tone =
  | "neutral"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "accent"
  | "violet";

const tones: Record<Tone, string> = {
  neutral: "bg-slate-100 text-slate-700 ring-slate-200",
  success: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  warning: "bg-amber-50 text-amber-700 ring-amber-200",
  danger: "bg-rose-50 text-rose-700 ring-rose-200",
  info: "bg-sky-50 text-sky-700 ring-sky-200",
  accent: "bg-accent-50 text-accent-700 ring-accent-200",
  violet: "bg-violet-50 text-violet-700 ring-violet-200",
};

export function Badge({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: Tone;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

const statusTones: Record<string, Tone> = {
  // calls
  completed: "success",
  in_progress: "info",
  connected: "info",
  initiated: "info",
  ringing: "info",
  queued: "warning",
  scheduled: "warning",
  pending: "warning",
  disconnected: "warning",
  no_answer: "neutral",
  busy: "neutral",
  canceled: "neutral",
  stopped: "neutral",
  failed: "danger",
  error: "danger",
  blocked: "danger",
  balance_low: "danger",
  simulated: "violet",
  // handoffs
  requested: "warning",
  cancelled: "neutral",
  not_supported: "neutral",
  // consent
  granted: "success",
  revoked: "danger",
  expired: "warning",
  missing: "neutral",
  unknown: "neutral",
  // jobs
  running: "info",
  succeeded: "success",
  dead: "danger",
  // workflow runs
  awaiting_approval: "warning",
  approved: "info",
  paused: "neutral",
  draft: "neutral",
  ready: "info",
  active: "success",
  disabled: "neutral",
  // integrations
  configured: "info",
  not_configured: "neutral",
  error_state: "danger",
};

export function StatusBadge({ status }: { status?: string | null }) {
  if (!status) return <Badge tone="neutral">—</Badge>;
  return <Badge tone={statusTones[status] || "neutral"}>{status.replace(/_/g, " ")}</Badge>;
}

const decisionTones: Record<string, Tone> = {
  allowed: "success",
  allow: "success",
  passed: "success",
  blocked: "danger",
  block: "danger",
  failed: "danger",
  skipped: "neutral",
  error: "danger",
};

export function DecisionBadge({ decision }: { decision?: string | null }) {
  if (!decision) return <Badge tone="neutral">—</Badge>;
  return (
    <Badge tone={decisionTones[decision] || "neutral"}>{decision.replace(/_/g, " ")}</Badge>
  );
}
