import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon?: LucideIcon;
  tone?: "default" | "accent" | "warning" | "danger" | "success";
}) {
  const iconStyles = {
    default: "bg-slate-100 text-slate-600",
    accent: "bg-accent-50 text-accent-600",
    warning: "bg-amber-50 text-amber-600",
    danger: "bg-rose-50 text-rose-600",
    success: "bg-emerald-50 text-emerald-600",
  }[tone];

  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-xs font-medium uppercase tracking-wide text-slate-500">
            {label}
          </p>
          <p className="mt-1.5 text-2xl font-semibold tabular-nums tracking-tight text-slate-900">
            {value}
          </p>
          {hint ? (
            <p className="mt-1 text-xs leading-relaxed text-slate-500">{hint}</p>
          ) : null}
        </div>
        {Icon ? (
          <span
            className={cn(
              "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
              iconStyles,
            )}
          >
            <Icon className="h-4.5 w-4.5" aria-hidden />
          </span>
        ) : null}
      </div>
    </Card>
  );
}

export function MetricRow({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-slate-100 py-2.5 last:border-0">
      <div>
        <p className="text-sm text-slate-600">{label}</p>
        {hint ? <p className="mt-0.5 text-xs text-slate-400">{hint}</p> : null}
      </div>
      <p className="shrink-0 text-sm font-semibold tabular-nums text-slate-900">{value}</p>
    </div>
  );
}
