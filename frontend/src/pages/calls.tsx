import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronLeft,
  ChevronRight,
  PhoneCall,
  PhoneOutgoing,
  RefreshCw,
  Search,
} from "lucide-react";

import { StartCallDialog } from "@/components/domain/start-call-dialog";
import { LeadName } from "@/components/domain/lead-name";
import { LeadPicker } from "@/components/domain/lead-picker";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/form";
import { PageHeader } from "@/components/ui/page-header";
import {
  EmptyState,
  ErrorState,
  InlineAlert,
  TableSkeleton,
} from "@/components/ui/states";
import {
  Table,
  TableWrap,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import {
  ACTIVE_CALL_STATUSES,
  CALL_STATUSES,
  LANGUAGE_NAMES,
} from "@/lib/constants";
import {
  formatCurrency,
  formatDateTime,
  formatDuration,
  titleCase,
} from "@/lib/utils";
import { listCalls, syncCallStatuses } from "@/services/callora";

const PAGE_SIZE = 25;

export default function CallsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [status, setStatus] = useState("");
  const [leadId, setLeadId] = useState(searchParams.get("lead") || "");
  const [offset, setOffset] = useState(0);
  const [startOpen, setStartOpen] = useState(searchParams.get("new") === "1");

  useEffect(() => {
    if (searchParams.get("new") === "1") setStartOpen(true);
  }, [searchParams]);

  const callsQuery = useQuery({
    queryKey: ["calls", status, leadId, offset],
    queryFn: () =>
      listCalls({
        status: status || undefined,
        lead_id: leadId || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
    placeholderData: (previous) => previous,
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      return items.some((call) => ACTIVE_CALL_STATUSES.has(call.status)) ? 15_000 : false;
    },
  });

  const syncMutation = useMutation({
    mutationFn: syncCallStatuses,
    onSuccess: () => {
      toast.success(
        "Call statuses synchronized",
        "The backend polled the provider and updated stored calls.",
      );
      void queryClient.invalidateQueries({ queryKey: ["calls"] });
      void queryClient.invalidateQueries({ queryKey: ["voice-stats"] });
      void queryClient.invalidateQueries({ queryKey: ["overview-calls"] });
    },
    onError: (error) => {
      toast.error(
        "Status synchronization failed",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const calls = callsQuery.data?.items ?? [];
  const hasNext = calls.length === PAGE_SIZE;
  const hasFilters = Boolean(status || leadId);
  const activeCount = useMemo(
    () => calls.filter((call) => ACTIVE_CALL_STATUSES.has(call.status)).length,
    [calls],
  );

  function closeStartDialog() {
    setStartOpen(false);
    const params = new URLSearchParams(searchParams);
    params.delete("new");
    setSearchParams(params, { replace: true });
  }

  return (
    <div>
      <PageHeader
        title="Calls"
        description="Every call record the backend holds. Status, duration, cost and compliance outcomes come from the voice provider webhooks and the compliance gate."
        actions={
          <>
            <Button
              variant="outline"
              onClick={() => syncMutation.mutate()}
              loading={syncMutation.isPending}
            >
              <RefreshCw className="h-4 w-4" aria-hidden /> Sync statuses
            </Button>
            <Button onClick={() => setStartOpen(true)}>
              <PhoneOutgoing className="h-4 w-4" aria-hidden /> New call
            </Button>
          </>
        }
        meta={
          <>
            <Badge tone="neutral">
              {calls.length
                ? `Showing ${offset + 1}–${offset + calls.length}`
                : "No calls on this page"}
            </Badge>
            {activeCount > 0 ? (
              <Badge tone="info">{activeCount} active · refreshing</Badge>
            ) : null}
            <span className="text-xs text-slate-400">
              The API does not return a total count; pagination advances by page size.
            </span>
          </>
        }
      />

      <Card className="mb-4">
        <div className="grid gap-3 p-4 lg:grid-cols-[220px_1fr_auto] lg:items-end">
          <div>
            <label htmlFor="call-status-filter" className="label">
              Status
            </label>
            <Select
              id="call-status-filter"
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setOffset(0);
              }}
            >
              <option value="">All statuses</option>
              {CALL_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {titleCase(value)}
                </option>
              ))}
            </Select>
          </div>
          <LeadPicker
            label="Filter by lead"
            value={leadId}
            onChange={(lead) => {
              setLeadId(lead?.id || "");
              setOffset(0);
            }}
          />
          <div className="flex gap-2 pb-1">
            {hasFilters ? (
              <Button
                variant="ghost"
                onClick={() => {
                  setStatus("");
                  setLeadId("");
                  setOffset(0);
                  navigate("/calls", { replace: true });
                }}
              >
                Clear filters
              </Button>
            ) : null}
            <Button variant="outline" onClick={() => void callsQuery.refetch()}>
              <RefreshCw className="h-4 w-4" aria-hidden /> Refresh
            </Button>
          </div>
        </div>
      </Card>

      {callsQuery.error ? (
        <Card>
          <ErrorState
            error={callsQuery.error}
            title="Calls are unavailable"
            onRetry={() => void callsQuery.refetch()}
          />
        </Card>
      ) : (
        <Card>
          {callsQuery.isPending ? (
            <TableSkeleton rows={8} columns={6} />
          ) : calls.length === 0 ? (
            hasFilters ? (
              <EmptyState
                icon={<Search className="h-5 w-5" aria-hidden />}
                title="No calls match"
                description="Change the status or lead filter. Call filters are applied by the backend."
                action={
                  <Button
                    variant="outline"
                    onClick={() => {
                      setStatus("");
                      setLeadId("");
                      setOffset(0);
                    }}
                  >
                    Clear filters
                  </Button>
                }
              />
            ) : (
              <EmptyState
                icon={<PhoneCall className="h-5 w-5" aria-hidden />}
                title="No calls yet"
                description="Start a compliant outbound call once a lead has consent evidence. The compliance gate will either allow or block the attempt."
                action={
                  <Button onClick={() => setStartOpen(true)}>
                    <PhoneOutgoing className="h-4 w-4" aria-hidden /> Start a call
                  </Button>
                }
              />
            )
          ) : (
            <>
              {activeCount > 0 ? (
                <div className="border-b border-slate-100 px-4 py-3">
                  <InlineAlert tone="info" title="Live calls are refreshing automatically">
                    {activeCount} call{activeCount === 1 ? "" : "s"} on this page are not
                    terminal yet. The list re-fetches every 15 seconds.
                  </InlineAlert>
                </div>
              ) : null}
              <TableWrap>
                <Table>
                  <THead>
                    <TR>
                      <TH>Lead</TH>
                      <TH>Call</TH>
                      <TH>Status</TH>
                      <TH>Language</TH>
                      <TH>Duration</TH>
                      <TH>Cost</TH>
                      <TH>Started</TH>
                      <TH className="text-right">Actions</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {calls.map((call) => (
                      <TR key={call.id}>
                        <TD>
                          <button
                            type="button"
                            className="text-left font-medium text-slate-900 hover:text-accent-700"
                            onClick={() => navigate(`/calls/${call.id}`)}
                          >
                            <LeadName leadId={call.lead_id} />
                          </button>
                          <p className="text-[11px] text-slate-400">{call.direction}</p>
                        </TD>
                        <TD>
                          <p className="font-mono text-[11px] text-slate-500">{call.id}</p>
                          <p className="text-[11px] text-slate-400">
                            {call.provider}
                            {call.simulated ? " · simulated" : ""}
                          </p>
                        </TD>
                        <TD>
                          <StatusBadge status={call.status} />
                          {call.blocked_reason ? (
                            <p className="mt-0.5 max-w-[200px] truncate text-[11px] text-rose-500">
                              {call.blocked_reason}
                            </p>
                          ) : null}
                        </TD>
                        <TD className="text-xs">
                          {call.language
                            ? LANGUAGE_NAMES[call.language] || call.language
                            : "—"}
                        </TD>
                        <TD className="text-xs">
                          {formatDuration(call.duration_seconds)}
                        </TD>
                        <TD className="text-xs">
                          {formatCurrency(call.cost, call.currency || "INR")}
                        </TD>
                        <TD className="whitespace-nowrap text-xs text-slate-500">
                          {formatDateTime(call.initiated_at || call.created_at)}
                        </TD>
                        <TD className="text-right">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => navigate(`/calls/${call.id}`)}
                          >
                            Open
                          </Button>
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </TableWrap>
              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-3">
                <p className="text-sm text-slate-500">
                  Page {Math.floor(offset / PAGE_SIZE) + 1} · {calls.length} record
                  {calls.length === 1 ? "" : "s"} shown
                </p>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                  >
                    <ChevronLeft className="h-3.5 w-3.5" /> Previous
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!hasNext}
                    onClick={() => setOffset(offset + PAGE_SIZE)}
                  >
                    Next <ChevronRight className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            </>
          )}
        </Card>
      )}

      <StartCallDialog
        open={startOpen}
        onClose={closeStartDialog}
        defaultLeadId={leadId || undefined}
        onCreated={() => {
          void queryClient.invalidateQueries({ queryKey: ["calls"] });
          void queryClient.invalidateQueries({ queryKey: ["leads-latest-calls"] });
        }}
      />
    </div>
  );
}
