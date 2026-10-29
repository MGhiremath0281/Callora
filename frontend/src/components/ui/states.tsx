import type { ReactNode } from "react";
import {
  AlertTriangle,
  Inbox,
  Loader2,
  RefreshCw,
  ShieldAlert,
  WifiOff,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

export function Skeleton({ className }: { className?: string }) {
  return <span className={cn("inline-block animate-pulse rounded-md bg-slate-200/70", className)} />;
}

export function LoadingBlock({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 px-5 py-14 text-sm text-slate-500">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
      {label}…
    </div>
  );
}

export function TableSkeleton({ rows = 6, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <div className="divide-y divide-slate-100">
      {Array.from({ length: rows }).map((_, row) => (
        <div key={row} className="flex items-center gap-4 px-4 py-3.5">
          {Array.from({ length: columns }).map((__, col) => (
            <Skeleton
              key={col}
              className={cn("h-4", col === 0 ? "w-40" : col === 1 ? "w-24" : "w-28")}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon,
  className,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center px-6 py-14 text-center",
        className,
      )}
    >
      <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-slate-100 text-slate-500">
        {icon || <Inbox className="h-5 w-5" aria-hidden />}
      </div>
      <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
      {description ? (
        <p className="mt-1 max-w-md text-sm text-slate-500">{description}</p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function ErrorState({
  error,
  title = "Could not load this view",
  onRetry,
  className,
}: {
  error: unknown;
  title?: string;
  onRetry?: () => void;
  className?: string;
}) {
  const apiError = error instanceof ApiError ? error : null;
  const message =
    error instanceof Error ? error.message : "An unexpected error occurred.";
  const isNetwork = apiError?.isNetwork;

  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center px-6 py-12 text-center",
        className,
      )}
    >
      <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-rose-50 text-rose-600">
        {isNetwork ? (
          <WifiOff className="h-5 w-5" aria-hidden />
        ) : (
          <AlertTriangle className="h-5 w-5" aria-hidden />
        )}
      </div>
      <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
      <p className="mt-1 max-w-lg text-sm text-slate-500">{message}</p>
      {apiError?.details?.length ? (
        <p className="mt-2 text-xs text-slate-400">
          The backend rejected the request ({apiError.code}).
        </p>
      ) : null}
      {onRetry ? (
        <Button className="mt-4" variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw className="h-3.5 w-3.5" aria-hidden />
          Try again
        </Button>
      ) : null}
    </div>
  );
}

export function InlineAlert({
  tone = "info",
  title,
  children,
  action,
}: {
  tone?: "info" | "warning" | "danger" | "success";
  title?: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  const styles = {
    info: "border-sky-200 bg-sky-50 text-sky-900",
    warning: "border-amber-200 bg-amber-50 text-amber-900",
    danger: "border-rose-200 bg-rose-50 text-rose-900",
    success: "border-emerald-200 bg-emerald-50 text-emerald-900",
  }[tone];

  return (
    <div className={cn("rounded-lg border px-4 py-3 text-sm", styles)}>
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          {title ? <p className="font-semibold">{title}</p> : null}
          <div className={cn(title ? "mt-0.5" : "", "leading-relaxed")}>{children}</div>
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
    </div>
  );
}

export function UnavailableNotice({
  title,
  description,
  dependencies,
}: {
  title: string;
  description: string;
  dependencies: string[];
}) {
  return (
    <Card className="border-dashed">
      <div className="flex flex-col items-center px-6 py-14 text-center">
        <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-amber-50 text-amber-600">
          <ShieldAlert className="h-5 w-5" aria-hidden />
        </div>
        <h2 className="text-base font-semibold text-slate-900">{title}</h2>
        <p className="mt-1 max-w-xl text-sm leading-relaxed text-slate-500">
          {description}
        </p>
        <div className="mt-5 w-full max-w-xl rounded-lg border border-slate-200 bg-slate-50 p-4 text-left">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Required backend capability
          </p>
          <ul className="mt-2 space-y-1">
            {dependencies.map((dependency) => (
              <li key={dependency} className="font-mono text-xs text-slate-600">
                {dependency}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Card>
  );
}
