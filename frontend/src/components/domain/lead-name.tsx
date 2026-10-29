import { useQuery } from "@tanstack/react-query";

import { getLead } from "@/services/callora";
import { Skeleton } from "@/components/ui/states";
import { cn } from "@/lib/utils";

/**
 * Resolves a lead name lazily. Call rows only carry `lead_id`; the lookup is
 * cached and deduplicated by React Query so a table does not fan out.
 */
export function LeadName({
  leadId,
  fallback,
  className,
  showId = false,
}: {
  leadId?: string | null;
  fallback?: string;
  className?: string;
  showId?: boolean;
}) {
  const { data, isPending } = useQuery({
    queryKey: ["lead", leadId],
    queryFn: () => getLead(leadId as string),
    enabled: Boolean(leadId),
    staleTime: 5 * 60_000,
    retry: false,
  });

  if (!leadId) {
    return <span className={cn("text-slate-400", className)}>{fallback || "—"}</span>;
  }

  if (isPending) {
    return <Skeleton className={cn("inline-block h-4 w-24 align-middle", className)} />;
  }

  return (
    <span className={className}>
      {data?.name || fallback || "Unknown lead"}
      {showId ? (
        <span className="ml-1.5 font-mono text-[11px] text-slate-400">{leadId}</span>
      ) : null}
    </span>
  );
}
