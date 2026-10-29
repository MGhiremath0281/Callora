import type { ReactNode } from "react";

import { safeJson } from "@/lib/utils";

export function JsonBlock({
  title = "Raw payload",
  value,
  children,
}: {
  title?: string;
  value: unknown;
  children?: ReactNode;
}) {
  return (
    <details className="group rounded-lg border border-slate-200 bg-slate-50/60">
      <summary className="cursor-pointer select-none px-3 py-2 text-xs font-medium text-slate-600 hover:text-slate-900">
        {title}
      </summary>
      <div className="border-t border-slate-200 p-3">
        {children}
        <pre className="max-h-72 overflow-auto rounded-md bg-slate-900 p-3 text-[11px] leading-relaxed text-slate-100">
          {safeJson(value)}
        </pre>
      </div>
    </details>
  );
}
