import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  ClipboardList,
  Clock,
  PhoneCall,
  Plus,
  RefreshCw,
  Search,
  ShieldAlert,
  UserRound,
} from "lucide-react";

import { LeadName } from "@/components/domain/lead-name";
import { LeadPicker } from "@/components/domain/lead-picker";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ButtonLink } from "@/components/ui/button-link";
import { Card, CardBody, CardHeader, CardFooter } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
import { PageHeader } from "@/components/ui/page-header";
import {
  EmptyState,
  ErrorState,
  InlineAlert,
  Skeleton,
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
import { Tabs } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import {
  HANDOFF_KINDS,
  HANDOFF_STATUSES,
} from "@/lib/constants";
import { cn, formatDateTime, maskPhone, titleCase } from "@/lib/utils";
import {
  completeFollowup,
  listFollowups,
  listHandoffs,
  requestCallback,
  updateHandoff,
} from "@/services/callora";
import type { Handoff, WorkflowFollowup } from "@/types/api";

const PAGE_SIZE = 20;

export default function HandoffsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState(
    searchParams.get("tab") === "followups" ? "followups" : "handoffs",
  );
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const [offset, setOffset] = useState(0);
  const [createOpen, setCreateOpen] = useState(searchParams.get("new") === "1");
  const [prefillLead, setPrefillLead] = useState(searchParams.get("lead") || "");
  const [prefillCall, setPrefillCall] = useState(searchParams.get("call") || "");
  const [selected, setSelected] = useState<Handoff | null>(null);

  useEffect(() => {
    if (searchParams.get("new") === "1") {
      setPrefillLead(searchParams.get("lead") || "");
      setPrefillCall(searchParams.get("call") || "");
      setCreateOpen(true);
    }
  }, [searchParams]);

  const handoffsQuery = useQuery({
    queryKey: ["handoffs", status, kind, offset],
    queryFn: () =>
      listHandoffs({
        status: status || undefined,
        kind: kind || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
    placeholderData: (previous) => previous,
    refetchInterval: 30_000,
  });

  const followupsQuery = useQuery({
    queryKey: ["followups", "pending", offset],
    queryFn: () =>
      listFollowups({
        status: "pending",
        limit: PAGE_SIZE,
        offset,
      }),
    placeholderData: (previous) => previous,
    refetchInterval: 30_000,
  });

  const invalidateAll = () => {
    void queryClient.invalidateQueries({ queryKey: ["handoffs"] });
    void queryClient.invalidateQueries({ queryKey: ["followups"] });
    void queryClient.invalidateQueries({ queryKey: ["voice-stats"] });
    void queryClient.invalidateQueries({ queryKey: ["overview-handoffs"] });
  };

  const callbackMutation = useMutation({
    mutationFn: (values: CallbackForm) =>
      requestCallback({
        lead_id: values.leadId,
        call_id: prefillCall || null,
        reason: values.reason,
        briefing: values.briefing || null,
        target_number: values.target || null,
        requested_by: "dashboard",
      }),
    onSuccess: () => {
      toast.success(
        "Callback queued",
        "The human handoff is now in the queue with its briefing.",
      );
      setCreateOpen(false);
      invalidateAll();
      clearNewParams();
    },
    onError: (error) => {
      toast.error(
        "Callback was not queued",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const updateMutation = useMutation({
    mutationFn: (values: UpdateForm) =>
      updateHandoff(selected?.id as string, {
        status: values.status,
        outcome: values.outcome || null,
        note: values.note || null,
      }),
    onSuccess: () => {
      toast.success("Handoff updated", "The change is reflected in the audit trail.");
      setSelected(null);
      invalidateAll();
    },
    onError: (error) => {
      toast.error(
        "Handoff was not updated",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const followupMutation = useMutation({
    mutationFn: (values: CompleteForm & { taskId: string }) =>
      completeFollowup(values.taskId, {
        outcome: values.outcome,
        notes: values.notes || undefined,
      }),
    onSuccess: () => {
      toast.success("Follow-up completed");
      setCompleting(null);
      invalidateAll();
    },
    onError: (error) => {
      toast.error(
        "Follow-up was not completed",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const [completing, setCompleting] = useState<WorkflowFollowup | null>(null);

  const handoffs = handoffsQuery.data?.items ?? [];
  const followups = followupsQuery.data?.items ?? [];
  const now = Date.now();

  const statusCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const handoff of handoffs) {
      counts.set(handoff.status, (counts.get(handoff.status) || 0) + 1);
    }
    return counts;
  }, [handoffs]);

  const openCount =
    (statusCounts.get("requested") || 0) + (statusCounts.get("initiated") || 0);

  function clearNewParams() {
    const params = new URLSearchParams(searchParams);
    params.delete("new");
    params.delete("call");
    params.delete("lead");
    setSearchParams(params, { replace: true });
  }

  function closeCreate() {
    setCreateOpen(false);
    clearNewParams();
  }

  return (
    <div>
      <PageHeader
        title="Handoffs & follow-ups"
        description="Human callbacks and workflow-generated follow-up tasks. Callora records briefing, status and outcome, and never claims a transfer connected unless the backend reports it."
        actions={
          <>
            <Button variant="outline" onClick={() => void handoffsQuery.refetch()}>
              <RefreshCw className="h-4 w-4" aria-hidden /> Refresh
            </Button>
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4" aria-hidden /> New callback
            </Button>
          </>
        }
        meta={
          <>
            <Badge tone="neutral">{handoffs.length} handoffs loaded</Badge>
            {openCount > 0 ? (
              <Badge tone="warning">{openCount} open</Badge>
            ) : (
              <Badge tone="success">No open handoffs on this page</Badge>
            )}
            <span className="text-xs text-slate-400">
              Statuses are updated by the backend and by operators. Refreshes every 30s.
            </span>
          </>
        }
      />

      <Tabs
        items={[
          { id: "handoffs", label: "Handoff queue", count: handoffs.length },
          { id: "followups", label: "Follow-up tasks", count: followups.length },
        ]}
        value={tab}
        onChange={(next) => {
          setTab(next);
          setOffset(0);
        }}
        className="mb-4"
      />

      {tab === "handoffs" ? (
        <>
          <Card className="mb-4">
            <div className="grid gap-3 p-4 sm:grid-cols-[200px_200px_auto] sm:items-end">
              <div>
                <label htmlFor="handoff-status" className="label">
                  Status
                </label>
                <Select
                  id="handoff-status"
                  value={status}
                  onChange={(event) => {
                    setStatus(event.target.value);
                    setOffset(0);
                  }}
                >
                  <option value="">All statuses</option>
                  {HANDOFF_STATUSES.map((value) => (
                    <option key={value} value={value}>
                      {titleCase(value)}
                    </option>
                  ))}
                </Select>
              </div>
              <div>
                <label htmlFor="handoff-kind" className="label">
                  Type
                </label>
                <Select
                  id="handoff-kind"
                  value={kind}
                  onChange={(event) => {
                    setKind(event.target.value);
                    setOffset(0);
                  }}
                >
                  <option value="">All types</option>
                  {HANDOFF_KINDS.map((value) => (
                    <option key={value} value={value}>
                      {titleCase(value)}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="flex gap-2 pb-1">
                {status || kind ? (
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setStatus("");
                      setKind("");
                      setOffset(0);
                    }}
                  >
                    Clear filters
                  </Button>
                ) : null}
                <Button
                  variant="outline"
                  onClick={() => void handoffsQuery.refetch()}
                >
                  <RefreshCw className="h-4 w-4" aria-hidden /> Refresh
                </Button>
              </div>
            </div>
          </Card>

          <Card>
            {handoffsQuery.isPending ? (
              <TableSkeleton rows={6} columns={6} />
            ) : handoffsQuery.error ? (
              <ErrorState
                error={handoffsQuery.error}
                title="Handoff queue unavailable"
                onRetry={() => void handoffsQuery.refetch()}
              />
            ) : handoffs.length === 0 ? (
              <EmptyState
                icon={<CalendarClock className="h-5 w-5" aria-hidden />}
                title={status || kind ? "No handoffs match" : "No handoffs yet"}
                description={
                  status || kind
                    ? "Change the status or type filter."
                    : "Callbacks requested by the agent, or by an operator, appear here. Requesting a callback never dials a number automatically."
                }
                action={
                  status || kind ? (
                    <Button
                      variant="outline"
                      onClick={() => {
                        setStatus("");
                        setKind("");
                      }}
                    >
                      Clear filters
                    </Button>
                  ) : (
                    <Button onClick={() => setCreateOpen(true)}>
                      <Plus className="h-4 w-4" aria-hidden /> New callback
                    </Button>
                  )
                }
              />
            ) : (
              <>
                <TableWrap>
                  <Table>
                    <THead>
                      <TR>
                        <TH>Lead</TH>
                        <TH>Type</TH>
                        <TH>Status</TH>
                        <TH>Reason</TH>
                        <TH>Target</TH>
                        <TH>Requested by</TH>
                        <TH>Created</TH>
                        <TH className="text-right">Actions</TH>
                      </TR>
                    </THead>
                    <TBody>
                      {handoffs.map((handoff) => (
                        <TR key={handoff.id}>
                          <TD>
                            <p className="font-medium text-slate-900">
                              {handoff.lead_name || (
                                <LeadName leadId={handoff.lead_id} />
                              )}
                            </p>
                            <p className="font-mono text-[11px] text-slate-400">
                              {handoff.lead_id || "No lead"}
                            </p>
                          </TD>
                          <TD className="text-xs capitalize">
                            {handoff.kind.replace(/_/g, " ")}
                          </TD>
                          <TD>
                            <StatusBadge status={handoff.status} />
                          </TD>
                          <TD className="max-w-[220px]">
                            <p className="truncate text-xs text-slate-600">
                              {handoff.reason || "No reason supplied"}
                            </p>
                            {handoff.outcome ? (
                              <p className="mt-0.5 truncate text-[11px] text-slate-400">
                                Outcome: {handoff.outcome}
                              </p>
                            ) : null}
                          </TD>
                          <TD className="font-mono text-xs">
                            {handoff.target_number
                              ? maskPhone(handoff.target_number)
                              : "Business default"}
                          </TD>
                          <TD className="text-xs">{handoff.requested_by}</TD>
                          <TD className="whitespace-nowrap text-xs text-slate-500">
                            {formatDateTime(handoff.created_at)}
                          </TD>
                          <TD className="text-right">
                            <div className="flex items-center justify-end gap-1">
                              {handoff.call_id ? (
                                <ButtonLink
                                  to={`/calls/${handoff.call_id}`}
                                  variant="ghost"
                                  size="sm"
                                >
                                  Call
                                </ButtonLink>
                              ) : null}
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => setSelected(handoff)}
                              >
                                Update
                              </Button>
                            </div>
                          </TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </TableWrap>
                <OffsetPager
                  offset={offset}
                  count={handoffs.length}
                  pageSize={PAGE_SIZE}
                  onChange={setOffset}
                />
              </>
            )}
          </Card>
        </>
      ) : null}

      {tab === "followups" ? (
        <>
          <div className="mb-4">
            <InlineAlert tone="info" title="Recovery workflow tasks">
              These tasks are created by workflow rules after calls are analysed. Completing
              a task records the operator outcome in the workflow; it does not place a call
              or send a message by itself.
            </InlineAlert>
          </div>
          <Card>
            {followupsQuery.isPending ? (
              <TableSkeleton rows={6} columns={6} />
            ) : followupsQuery.error ? (
              <ErrorState
                error={followupsQuery.error}
                title="Follow-up tasks unavailable"
                onRetry={() => void followupsQuery.refetch()}
              />
            ) : followups.length === 0 ? (
              <EmptyState
                icon={<ClipboardList className="h-5 w-5" aria-hidden />}
                title="No pending follow-up tasks"
                description="Tasks appear when a recovery workflow rule creates one after a call. If no rules are active, there will be nothing here."
              />
            ) : (
              <>
                <TableWrap>
                  <Table>
                    <THead>
                      <TR>
                        <TH>Lead</TH>
                        <TH>Channel</TH>
                        <TH>Status</TH>
                        <TH>Priority</TH>
                        <TH>Due</TH>
                        <TH>Reason</TH>
                        <TH className="text-right">Actions</TH>
                      </TR>
                    </THead>
                    <TBody>
                      {followups.map((task) => {
                        const overdue =
                          task.due_at &&
                          new Date(task.due_at).getTime() < now &&
                          task.status === "pending";
                        return (
                          <TR key={task.id}>
                            <TD>
                              <p className="font-medium text-slate-900">
                                <LeadName leadId={task.lead_id} />
                              </p>
                              <p className="font-mono text-[11px] text-slate-400">
                                {task.id}
                              </p>
                            </TD>
                            <TD className="text-xs capitalize">
                              {task.channel.replace(/_/g, " ")}
                            </TD>
                            <TD>
                              <StatusBadge status={task.status} />
                            </TD>
                            <TD className="text-xs capitalize">
                              {task.priority || "—"}
                            </TD>
                            <TD className="whitespace-nowrap text-xs">
                              <span
                                className={cn(
                                  overdue && "font-medium text-rose-600",
                                )}
                              >
                                {formatDateTime(task.due_at)}
                              </span>
                              {overdue ? (
                                <span className="ml-1 inline-flex items-center gap-0.5 text-[10px] font-medium text-rose-500">
                                  <Clock className="h-3 w-3" /> overdue
                                </span>
                              ) : null}
                            </TD>
                            <TD className="max-w-[240px]">
                              <p className="truncate text-xs text-slate-600">
                                {task.reason || "No reason supplied"}
                              </p>
                            </TD>
                            <TD className="text-right">
                              <div className="flex items-center justify-end gap-1">
                                {task.workflow_run_id ? (
                                  <ButtonLink
                                    to={`/analytics?tab=workflow`}
                                    variant="ghost"
                                    size="sm"
                                  >
                                    Run
                                  </ButtonLink>
                                ) : null}
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() => setCompleting(task)}
                                  disabled={task.status === "completed"}
                                >
                                  <CheckCircle2 className="h-3.5 w-3.5" /> Complete
                                </Button>
                              </div>
                            </TD>
                          </TR>
                        );
                      })}
                    </TBody>
                  </Table>
                </TableWrap>
                <OffsetPager
                  offset={offset}
                  count={followups.length}
                  pageSize={PAGE_SIZE}
                  onChange={setOffset}
                />
              </>
            )}
          </Card>
        </>
      ) : null}

      <CallbackDialog
        open={createOpen}
        onClose={closeCreate}
        leadId={prefillLead}
        callId={prefillCall}
        loading={callbackMutation.isPending}
        error={
          callbackMutation.error instanceof Error
            ? callbackMutation.error.message
            : null
        }
        onSubmit={(values) => callbackMutation.mutate(values)}
      />

      <Dialog
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        title="Update handoff"
        description={
          selected
            ? `${titleCase(selected.kind)} · ${selected.lead_name || selected.lead_id || "No lead"}`
            : undefined
        }
        size="lg"
        footer={
          <>
            <Button variant="outline" onClick={() => setSelected(null)}>
              Close
            </Button>
            <Button
              form="handoff-update-form"
              type="submit"
              loading={updateMutation.isPending}
            >
              Save update
            </Button>
          </>
        }
      >
        {selected ? (
          <HandoffUpdateForm
            key={selected.id}
            handoff={selected}
            loading={updateMutation.isPending}
            error={
              updateMutation.error instanceof Error
                ? updateMutation.error.message
                : null
            }
            onSubmit={(values) => updateMutation.mutate(values)}
          />
        ) : null}
      </Dialog>

      <Dialog
        open={Boolean(completing)}
        onClose={() => setCompleting(null)}
        title="Complete follow-up task"
        description={
          completing
            ? `${titleCase(completing.channel)} task created by the recovery workflow.`
            : undefined
        }
        size="md"
        footer={
          <>
            <Button variant="outline" onClick={() => setCompleting(null)}>
              Cancel
            </Button>
            <Button
              form="followup-complete-form"
              type="submit"
              loading={followupMutation.isPending}
              disabled={!completing}
            >
              <CheckCircle2 className="h-4 w-4" /> Complete task
            </Button>
          </>
        }
      >
        {completing ? (
          <FollowupCompleteForm
            key={completing.id}
            task={completing}
            loading={followupMutation.isPending}
            error={
              followupMutation.error instanceof Error
                ? followupMutation.error.message
                : null
            }
            onSubmit={(values) =>
              followupMutation.mutate({ taskId: completing.id, ...values })
            }
          />
        ) : null}
      </Dialog>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Offset pager                                                        */
/* ------------------------------------------------------------------ */

function OffsetPager({
  offset,
  count,
  pageSize,
  onChange,
}: {
  offset: number;
  count: number;
  pageSize: number;
  onChange: (offset: number) => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-3">
      <p className="text-sm text-slate-500">
        Showing {offset + 1}–{offset + count} · the API does not return a total
      </p>
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={offset === 0}
          onClick={() => onChange(Math.max(0, offset - pageSize))}
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={count < pageSize}
          onClick={() => onChange(offset + pageSize)}
        >
          Next <ArrowRight className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Dialog forms                                                        */
/* ------------------------------------------------------------------ */

type CallbackForm = {
  leadId: string;
  reason: string;
  briefing: string;
  target: string;
};

function CallbackDialog({
  open,
  onClose,
  leadId: initialLeadId,
  callId,
  onSubmit,
  loading,
  error,
}: {
  open: boolean;
  onClose: () => void;
  leadId: string;
  callId: string;
  onSubmit: (values: CallbackForm) => void;
  loading: boolean;
  error: string | null;
}) {
  const [leadId, setLeadId] = useState(initialLeadId);
  const [reason, setReason] = useState("Prospect requested a callback");
  const [briefing, setBriefing] = useState("");
  const [target, setTarget] = useState("");

  useEffect(() => {
    if (!open) return;
    setLeadId(initialLeadId);
    setReason("Prospect requested a callback");
    setBriefing("");
    setTarget("");
  }, [open, initialLeadId]);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Queue a human callback"
      description="This creates a handoff for a human operator. It does not automatically dial the lead."
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={loading}>
            Cancel
          </Button>
          <Button
            onClick={() => onSubmit({ leadId, reason, briefing, target })}
            loading={loading}
            disabled={!leadId || !reason.trim()}
          >
            <CalendarClock className="h-4 w-4" aria-hidden /> Queue callback
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <LeadPicker
          label="Lead"
          value={leadId}
          onChange={(lead) => setLeadId(lead?.id || "")}
          disabled={loading}
        />
        {callId ? (
          <p className="text-xs text-slate-500">
            Linked call: <span className="font-mono text-slate-700">{callId}</span>
          </p>
        ) : null}
        <Field label="Reason" htmlFor="callback-reason" required>
          <Input
            id="callback-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            disabled={loading}
          />
        </Field>
        <Field
          label="Briefing"
          htmlFor="callback-briefing"
          hint="Context for the human operator. Never include secrets."
        >
          <Textarea
            id="callback-briefing"
            rows={4}
            value={briefing}
            onChange={(event) => setBriefing(event.target.value)}
            placeholder="Interested in the enterprise plan. Prefers a call after 4pm."
            disabled={loading}
          />
        </Field>
        <Field
          label="Target number"
          htmlFor="callback-target"
          hint="Leave blank to let the backend use the business handoff target."
        >
          <Input
            id="callback-target"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            placeholder="+91…"
            disabled={loading}
          />
        </Field>
        <InlineAlert tone="warning" title="A handoff is not a guarantee of a connection">
          The backend records the request. Warm transfer availability depends on the provider
          and the business handoff configuration.
        </InlineAlert>
        {error ? (
          <InlineAlert tone="danger" title="Callback was not queued">
            {error}
          </InlineAlert>
        ) : null}
      </div>
    </Dialog>
  );
}

type UpdateForm = { status: string; outcome: string; note: string };

function HandoffUpdateForm({
  handoff,
  onSubmit,
  loading,
  error,
}: {
  handoff: Handoff;
  onSubmit: (values: UpdateForm) => void;
  loading: boolean;
  error: string | null;
}) {
  const [status, setStatus] = useState(handoff.status);
  const [outcome, setOutcome] = useState(handoff.outcome || "");
  const [note, setNote] = useState(handoff.outcome_note || "");
  const needsOutcome = status === "completed";

  return (
    <form
      id="handoff-update-form"
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({ status, outcome, note });
      }}
    >
      <div className="grid gap-3 rounded-lg border border-slate-200 bg-slate-50/60 p-3 text-xs sm:grid-cols-2">
        <p>
          <span className="text-slate-500">Lead: </span>
          {handoff.lead_name || handoff.lead_id || "—"}
        </p>
        <p>
          <span className="text-slate-500">Type: </span>
          {titleCase(handoff.kind)}
        </p>
        <p>
          <span className="text-slate-500">Target: </span>
          <span className="font-mono">
            {handoff.target_number ? maskPhone(handoff.target_number) : "Business default"}
          </span>
        </p>
        <p>
          <span className="text-slate-500">Created: </span>
          {formatDateTime(handoff.created_at)}
        </p>
        <p className="sm:col-span-2">
          <span className="text-slate-500">Briefing: </span>
          {handoff.briefing || "None supplied"}
        </p>
        {handoff.call_id ? (
          <p>
            <span className="text-slate-500">Call: </span>
            <ButtonLink
              to={`/calls/${handoff.call_id}`}
              variant="ghost"
              size="sm"
            >
              Open
            </ButtonLink>
          </p>
        ) : null}
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Status" htmlFor="handoff-update-status">
          <Select
            id="handoff-update-status"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            disabled={loading}
          >
            {HANDOFF_STATUSES.map((value) => (
              <option key={value} value={value}>
                {titleCase(value)}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          label="Outcome"
          htmlFor="handoff-update-outcome"
          hint={needsOutcome ? "Required when completing." : "Optional short outcome."}
        >
          <Input
            id="handoff-update-outcome"
            value={outcome}
            onChange={(event) => setOutcome(event.target.value)}
            placeholder={
              needsOutcome ? "e.g. Callback completed, demo booked" : "Optional"
            }
            disabled={loading}
          />
        </Field>
      </div>

      <Field label="Operator note" htmlFor="handoff-update-note">
        <Textarea
          id="handoff-update-note"
          rows={3}
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="What happened?"
          disabled={loading}
        />
      </Field>

      {needsOutcome && !outcome.trim() ? (
        <InlineAlert tone="warning" title="Outcome required">
          A completed handoff must record an outcome so the history is meaningful.
        </InlineAlert>
      ) : null}

      {status === "failed" || status === "cancelled" ? (
        <InlineAlert tone="warning" title="This closes the handoff">
          Marking a handoff failed or cancelled removes it from the open queue. The action is
          recorded in the audit trail.
        </InlineAlert>
      ) : null}

      {error ? (
        <InlineAlert tone="danger" title="Handoff was not updated">
          {error}
        </InlineAlert>
      ) : null}

      <button
        type="submit"
        className="sr-only"
        disabled={loading || (needsOutcome && !outcome.trim())}
      >
        Save update
      </button>
    </form>
  );
}

type CompleteForm = { outcome: string; notes: string };

function FollowupCompleteForm({
  task,
  onSubmit,
  loading,
  error,
}: {
  task: WorkflowFollowup;
  onSubmit: (values: CompleteForm) => void;
  loading: boolean;
  error: string | null;
}) {
  const [outcome, setOutcome] = useState("");
  const [notes, setNotes] = useState("");

  return (
    <form
      id="followup-complete-form"
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit({ outcome, notes });
      }}
    >
      <div className="rounded-lg border border-slate-200 bg-slate-50/60 p-3 text-xs">
        <p className="flex items-center gap-1.5">
          <UserRound className="h-3.5 w-3.5 text-slate-400" />
          <LeadName leadId={task.lead_id} />
        </p>
        <p className="mt-1 capitalize text-slate-500">
          {task.channel.replace(/_/g, " ")}
          {task.priority ? ` · ${task.priority} priority` : ""}
        </p>
        <p className="mt-1 text-slate-500">Due {formatDateTime(task.due_at)}</p>
        <p className="mt-2 text-slate-600">{task.reason || "No reason supplied"}</p>
      </div>

      <Field
        label="Outcome"
        htmlFor="followup-outcome"
        required
        hint="Record what the operator actually did."
      >
        <Input
          id="followup-outcome"
          value={outcome}
          onChange={(event) => setOutcome(event.target.value)}
          placeholder="e.g. Reached customer, demo booked"
          disabled={loading}
        />
      </Field>

      <Field label="Notes" htmlFor="followup-notes">
        <Textarea
          id="followup-notes"
          rows={3}
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          placeholder="Additional context for the next operator."
          disabled={loading}
        />
      </Field>

      {error ? (
        <InlineAlert tone="danger" title="Follow-up was not completed">
          {error}
        </InlineAlert>
      ) : null}

      <button
        type="submit"
        className="sr-only"
        disabled={loading || !outcome.trim()}
      >
        Complete task
      </button>
    </form>
  );
}
