import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  Ban,
  CheckCircle2,
  ClipboardList,
  FileClock,
  Plus,
  RefreshCw,
  RotateCw,
  Search,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";

import { JsonBlock } from "@/components/domain/json-block";
import { LeadName } from "@/components/domain/lead-name";
import { LeadPicker } from "@/components/domain/lead-picker";
import { Badge, DecisionBadge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ButtonLink } from "@/components/ui/button-link";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/form";
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
import { Tabs } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { JOB_STATUSES } from "@/lib/constants";
import { formatDateTime, maskPhone, titleCase } from "@/lib/utils";
import {
  addDnd,
  deactivateDnd,
  listAudit,
  listComplianceDecisions,
  listDnd,
  listJobs,
  retryJob,
} from "@/services/callora";
import type { DndEntry } from "@/types/api";

const PAGE_SIZE = 25;

type TabId = "decisions" | "audit" | "dnd" | "jobs";

export default function CompliancePage() {
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const toast = useToast();
  const initialTab = (searchParams.get("tab") as TabId) || "decisions";
  const [tab, setTab] = useState<TabId>(
    ["decisions", "audit", "dnd", "jobs"].includes(initialTab)
      ? initialTab
      : "decisions",
  );
  const [offset, setOffset] = useState(0);
  const [decisionLead, setDecisionLead] = useState("");
  const [auditAction, setAuditAction] = useState("");
  const [auditEntity, setAuditEntity] = useState("");
  const [jobStatus, setJobStatus] = useState("");
  const [dndOpen, setDndOpen] = useState(false);
  const [deactivating, setDeactivating] = useState<DndEntry | null>(null);

  const decisionsQuery = useQuery({
    queryKey: ["compliance-decisions", decisionLead, offset],
    queryFn: () =>
      listComplianceDecisions({
        lead_id: decisionLead || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
    enabled: tab === "decisions",
    placeholderData: (previous) => previous,
  });

  const auditQuery = useQuery({
    queryKey: ["audit", auditAction, auditEntity, offset],
    queryFn: () =>
      listAudit({
        action: auditAction || undefined,
        entity_id: auditEntity || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
    enabled: tab === "audit",
    placeholderData: (previous) => previous,
  });

  const dndQuery = useQuery({
    queryKey: ["dnd", offset],
    queryFn: () => listDnd({ limit: PAGE_SIZE, offset }),
    enabled: tab === "dnd",
    placeholderData: (previous) => previous,
  });

  const jobsQuery = useQuery({
    queryKey: ["jobs", jobStatus, offset],
    queryFn: () =>
      listJobs({
        status: jobStatus || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
    enabled: tab === "jobs",
    placeholderData: (previous) => previous,
    refetchInterval: 30_000,
  });

  const addDndMutation = useMutation({
    mutationFn: (values: { phone: string; scope: string; reason: string }) =>
      addDnd({
        phone: values.phone,
        scope: values.scope,
        reason: values.reason || null,
        source: "dashboard",
      }),
    onSuccess: () => {
      toast.success(
        "Number added to DND",
        "The compliance gate blocks outbound calls to this number.",
      );
      setDndOpen(false);
      invalidateCompliance();
    },
    onError: (error) => {
      toast.error(
        "DND entry was not created",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const deactivateMutation = useMutation({
    mutationFn: (entryId: string) => deactivateDnd(entryId),
    onSuccess: () => {
      toast.success(
        "DND entry deactivated",
        "Future compliance checks will no longer treat this number as blocked by DND.",
      );
      setDeactivating(null);
      invalidateCompliance();
    },
    onError: (error) => {
      toast.error(
        "DND entry was not deactivated",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const retryMutation = useMutation({
    mutationFn: (jobId: string) => retryJob(jobId),
    onSuccess: (job) => {
      toast.success(
        "Job retry queued",
        `${titleCase(job.type)} will be attempted again by the worker.`,
      );
      void queryClient.invalidateQueries({ queryKey: ["jobs"] });
      void queryClient.invalidateQueries({ queryKey: ["voice-stats"] });
    },
    onError: (error) => {
      toast.error(
        "Job retry failed",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const decisions = decisionsQuery.data?.items ?? [];
  const audit = auditQuery.data?.items ?? [];
  const dndEntries = dndQuery.data?.items ?? [];
  const jobs = jobsQuery.data?.items ?? [];

  const activeDnd = useMemo(
    () => dndEntries.filter((entry) => entry.active).length,
    [dndEntries],
  );

  function invalidateCompliance() {
    void queryClient.invalidateQueries({ queryKey: ["dnd"] });
    void queryClient.invalidateQueries({ queryKey: ["compliance-decisions"] });
    void queryClient.invalidateQueries({ queryKey: ["voice-leads"] });
    void queryClient.invalidateQueries({ queryKey: ["voice-stats"] });
    void queryClient.invalidateQueries({ queryKey: ["audit"] });
  }

  function switchTab(next: TabId) {
    setTab(next);
    setOffset(0);
  }

  const tabs = [
    { id: "decisions", label: "Compliance decisions", count: decisions.length || undefined },
    { id: "audit", label: "Audit trail", count: audit.length || undefined },
    { id: "dnd", label: "DND registry", count: dndEntries.length || undefined },
    { id: "jobs", label: "Background jobs", count: jobs.length || undefined },
  ];

  return (
    <div>
      <PageHeader
        title="Compliance & audit"
        description="Evidence, decisions and operational history. The backend writes compliance decisions and audit entries; this page reads them and never rewrites history."
        actions={
          <>
            <Button
              variant="outline"
              onClick={() => {
                if (tab === "decisions") void decisionsQuery.refetch();
                else if (tab === "audit") void auditQuery.refetch();
                else if (tab === "dnd") void dndQuery.refetch();
                else void jobsQuery.refetch();
              }}
            >
              <RefreshCw className="h-4 w-4" aria-hidden /> Refresh
            </Button>
            <Button onClick={() => setDndOpen(true)}>
              <Plus className="h-4 w-4" aria-hidden /> Add to DND
            </Button>
          </>
        }
        meta={
          <>
            <Badge tone="success">
              <ShieldCheck className="h-3 w-3" /> Compliance gate active
            </Badge>
            {tab === "dnd" ? (
              <Badge tone={activeDnd ? "warning" : "neutral"}>
                {activeDnd} active in loaded entries
              </Badge>
            ) : null}
            <span className="text-xs text-slate-400">
              Decision checks and audit metadata are shown verbatim.
            </span>
          </>
        }
      />

      <Tabs items={tabs} value={tab} onChange={(next) => switchTab(next as TabId)} className="mb-4" />

      {tab === "decisions" ? (
        <>
          <Card className="mb-4">
            <div className="grid gap-3 p-4 sm:grid-cols-[1fr_auto] sm:items-end">
              <LeadPicker
                label="Filter by lead"
                value={decisionLead}
                onChange={(lead) => {
                  setDecisionLead(lead?.id || "");
                  setOffset(0);
                }}
              />
              <div className="flex gap-2 pb-1">
                {decisionLead ? (
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setDecisionLead("");
                      setOffset(0);
                    }}
                  >
                    Clear
                  </Button>
                ) : null}
                <Button
                  variant="outline"
                  onClick={() => void decisionsQuery.refetch()}
                >
                  <RefreshCw className="h-4 w-4" aria-hidden /> Refresh
                </Button>
              </div>
            </div>
          </Card>

          <Card>
            {decisionsQuery.isPending ? (
              <TableSkeleton rows={6} columns={5} />
            ) : decisionsQuery.error ? (
              <ErrorState
                error={decisionsQuery.error}
                title="Compliance decisions unavailable"
                onRetry={() => void decisionsQuery.refetch()}
              />
            ) : decisions.length === 0 ? (
              <EmptyState
                icon={<ShieldAlert className="h-5 w-5" aria-hidden />}
                title="No compliance decisions"
                description="A decision is written every time a call attempt passes through the compliance gate, including blocked attempts."
              />
            ) : (
              <>
                <TableWrap>
                  <Table>
                    <THead>
                      <TR>
                        <TH>When</TH>
                        <TH>Decision</TH>
                        <TH>Lead</TH>
                        <TH>Call</TH>
                        <TH>Why</TH>
                        <TH>Checks</TH>
                      </TR>
                    </THead>
                    <TBody>
                      {decisions.map((decision) => (
                        <TR key={decision.id}>
                          <TD className="whitespace-nowrap text-xs text-slate-500">
                            {formatDateTime(decision.created_at)}
                          </TD>
                          <TD>
                            <DecisionBadge decision={decision.decision} />
                            <p className="mt-0.5 font-mono text-[10px] text-slate-400">
                              {decision.reason_code}
                            </p>
                          </TD>
                          <TD>
                            <ButtonLink
                              to={`/leads/${decision.lead_id}`}
                              variant="ghost"
                              size="sm"
                            >
                              <LeadName leadId={decision.lead_id} />
                            </ButtonLink>
                          </TD>
                          <TD>
                            {decision.call_id ? (
                              <ButtonLink
                                to={`/calls/${decision.call_id}`}
                                variant="ghost"
                                size="sm"
                              >
                                Open call
                              </ButtonLink>
                            ) : (
                              <span className="text-xs text-slate-400">No call</span>
                            )}
                          </TD>
                          <TD className="max-w-[260px] text-xs text-slate-600">
                            {decision.reason || "No reason supplied"}
                          </TD>
                          <TD className="min-w-[180px]">
                            {decision.checks?.length ? (
                              <JsonBlock title="Checks" value={decision.checks} />
                            ) : (
                              <span className="text-xs text-slate-400">None</span>
                            )}
                          </TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </TableWrap>
                <OffsetPager
                  offset={offset}
                  count={decisions.length}
                  onChange={setOffset}
                />
              </>
            )}
          </Card>
        </>
      ) : null}

      {tab === "audit" ? (
        <>
          <Card className="mb-4">
            <div className="grid gap-3 p-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
              <Field label="Action contains" htmlFor="audit-action">
                <Input
                  id="audit-action"
                  value={auditAction}
                  placeholder="e.g. call.blocked"
                  onChange={(event) => {
                    setAuditAction(event.target.value);
                    setOffset(0);
                  }}
                />
              </Field>
              <Field label="Entity id" htmlFor="audit-entity">
                <Input
                  id="audit-entity"
                  value={auditEntity}
                  placeholder="Lead, call or handoff id"
                  onChange={(event) => {
                    setAuditEntity(event.target.value);
                    setOffset(0);
                  }}
                />
              </Field>
              <div className="flex gap-2 pb-1">
                {auditAction || auditEntity ? (
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setAuditAction("");
                      setAuditEntity("");
                      setOffset(0);
                    }}
                  >
                    Clear
                  </Button>
                ) : null}
                <Button variant="outline" onClick={() => void auditQuery.refetch()}>
                  <RefreshCw className="h-4 w-4" aria-hidden /> Refresh
                </Button>
              </div>
            </div>
          </Card>

          <Card>
            {auditQuery.isPending ? (
              <TableSkeleton rows={8} columns={5} />
            ) : auditQuery.error ? (
              <ErrorState
                error={auditQuery.error}
                title="Audit trail unavailable"
                onRetry={() => void auditQuery.refetch()}
              />
            ) : audit.length === 0 ? (
              <EmptyState
                icon={<FileClock className="h-5 w-5" aria-hidden />}
                title="No audit entries"
                description={
                  auditAction || auditEntity
                    ? "No entries match the current filters. Clear them to see the full trail."
                    : "Operational actions will be recorded here as they occur."
                }
              />
            ) : (
              <>
                <TableWrap>
                  <Table>
                    <THead>
                      <TR>
                        <TH>When</TH>
                        <TH>Actor</TH>
                        <TH>Action</TH>
                        <TH>Entity</TH>
                        <TH>Decision / reason</TH>
                        <TH>Metadata</TH>
                      </TR>
                    </THead>
                    <TBody>
                      {audit.map((entry) => (
                        <TR key={entry.id}>
                          <TD className="whitespace-nowrap text-xs text-slate-500">
                            {formatDateTime(entry.created_at)}
                          </TD>
                          <TD className="text-xs">{entry.actor}</TD>
                          <TD>
                            <span className="font-mono text-[11px] text-slate-700">
                              {entry.action}
                            </span>
                          </TD>
                          <TD>
                            <p className="text-xs text-slate-700">
                              {titleCase(entry.entity_type)}
                            </p>
                            <p className="break-all font-mono text-[10px] text-slate-400">
                              {entry.entity_id}
                            </p>
                          </TD>
                          <TD className="max-w-[220px] text-xs">
                            {entry.decision ? (
                              <StatusBadge status={entry.decision} />
                            ) : null}
                            {entry.reason_code ? (
                              <p className="mt-0.5 font-mono text-[10px] text-slate-400">
                                {entry.reason_code}
                              </p>
                            ) : null}
                            {entry.reason ? (
                              <p className="mt-0.5 text-slate-600">{entry.reason}</p>
                            ) : null}
                          </TD>
                          <TD className="min-w-[160px]">
                            {Object.keys(entry.metadata || {}).length ? (
                              <JsonBlock title="Metadata" value={entry.metadata} />
                            ) : (
                              <span className="text-xs text-slate-400">None</span>
                            )}
                          </TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </TableWrap>
                <OffsetPager offset={offset} count={audit.length} onChange={setOffset} />
              </>
            )}
          </Card>
        </>
      ) : null}

      {tab === "dnd" ? (
        <Card>
          <CardHeader
            title="Do-not-disturb registry"
            description="Business and global suppressions. Deactivating an entry does not erase its history."
            actions={
              <Button size="sm" onClick={() => setDndOpen(true)}>
                <Plus className="h-3.5 w-3.5" /> Add number
              </Button>
            }
          />
          {dndQuery.isPending ? (
            <TableSkeleton rows={6} columns={6} />
          ) : dndQuery.error ? (
            <ErrorState
              error={dndQuery.error}
              title="DND registry unavailable"
              onRetry={() => void dndQuery.refetch()}
            />
          ) : dndEntries.length === 0 ? (
            <EmptyState
              icon={<Ban className="h-5 w-5" aria-hidden />}
              title="No DND entries"
              description="Add numbers that must never be called. The compliance gate checks the registry on every attempt."
              action={
                <Button size="sm" onClick={() => setDndOpen(true)}>
                  Add number
                </Button>
              }
            />
          ) : (
            <>
              <TableWrap>
                <Table>
                  <THead>
                    <TR>
                      <TH>Phone</TH>
                      <TH>Scope</TH>
                      <TH>Status</TH>
                      <TH>Reason</TH>
                      <TH>Source</TH>
                      <TH>Created</TH>
                      <TH className="text-right">Actions</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {dndEntries.map((entry) => (
                      <TR key={entry.id}>
                        <TD className="font-mono text-xs">{maskPhone(entry.phone)}</TD>
                        <TD className="text-xs capitalize">{entry.scope}</TD>
                        <TD>
                          <Badge tone={entry.active ? "danger" : "neutral"}>
                            {entry.active ? "active" : "inactive"}
                          </Badge>
                        </TD>
                        <TD className="max-w-[220px] text-xs text-slate-600">
                          {entry.reason || "—"}
                        </TD>
                        <TD className="text-xs">{entry.source || "—"}</TD>
                        <TD className="whitespace-nowrap text-xs text-slate-500">
                          {formatDateTime(entry.created_at)}
                        </TD>
                        <TD className="text-right">
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={!entry.active}
                            onClick={() => setDeactivating(entry)}
                          >
                            Deactivate
                          </Button>
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </TableWrap>
              <OffsetPager offset={offset} count={dndEntries.length} onChange={setOffset} />
              <div className="border-t border-slate-100 px-4 py-2 text-[11px] text-slate-400">
                The registry returns at most one page at a time; offsets advance by page size.
              </div>
            </>
          )}
        </Card>
      ) : null}

      {tab === "jobs" ? (
        <>
          <Card className="mb-4">
            <div className="grid gap-3 p-4 sm:grid-cols-[220px_auto] sm:items-end">
              <div>
                <label htmlFor="job-status" className="label">
                  Status
                </label>
                <Select
                  id="job-status"
                  value={jobStatus}
                  onChange={(event) => {
                    setJobStatus(event.target.value);
                    setOffset(0);
                  }}
                >
                  <option value="">All statuses</option>
                  {JOB_STATUSES.map((status) => (
                    <option key={status} value={status}>
                      {titleCase(status)}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="flex gap-2 pb-1">
                {jobStatus ? (
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setJobStatus("");
                      setOffset(0);
                    }}
                  >
                    Clear
                  </Button>
                ) : null}
                <Button variant="outline" onClick={() => void jobsQuery.refetch()}>
                  <RefreshCw className="h-4 w-4" aria-hidden /> Refresh
                </Button>
              </div>
            </div>
          </Card>

          <Card>
            <CardHeader
              title="Background jobs"
              description="Webhook processing, provider synchronization and other queued work."
            />
            {jobsQuery.isPending ? (
              <TableSkeleton rows={6} columns={5} />
            ) : jobsQuery.error ? (
              <ErrorState
                error={jobsQuery.error}
                title="Jobs unavailable"
                onRetry={() => void jobsQuery.refetch()}
              />
            ) : jobs.length === 0 ? (
              <EmptyState
                icon={<Activity className="h-5 w-5" aria-hidden />}
                title="No jobs"
                description="Queued and completed background jobs will appear here. A simulated workspace may have none."
              />
            ) : (
              <>
                <TableWrap>
                  <Table>
                    <THead>
                      <TR>
                        <TH>Job</TH>
                        <TH>Status</TH>
                        <TH>Attempts</TH>
                        <TH>Schedule</TH>
                        <TH>Last error</TH>
                        <TH className="text-right">Actions</TH>
                      </TR>
                    </THead>
                    <TBody>
                      {jobs.map((job) => (
                        <TR key={job.id}>
                          <TD>
                            <p className="text-xs font-medium capitalize text-slate-800">
                              {job.type.replace(/_/g, " ")}
                            </p>
                            <p className="font-mono text-[10px] text-slate-400">{job.id}</p>
                          </TD>
                          <TD>
                            <StatusBadge status={job.status} />
                          </TD>
                          <TD className="text-xs tabular-nums">
                            {job.attempts}/{job.max_attempts}
                          </TD>
                          <TD className="whitespace-nowrap text-xs text-slate-500">
                            {formatDateTime(job.scheduled_at)}
                          </TD>
                          <TD className="max-w-[260px] text-xs text-rose-600">
                            {job.last_error || "—"}
                          </TD>
                          <TD className="text-right">
                            <div className="flex items-center justify-end gap-1">
                              {job.result ? (
                                <JsonBlock title="Result" value={job.result} />
                              ) : null}
                              {["failed", "dead"].includes(job.status) ? (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() => retryMutation.mutate(job.id)}
                                  loading={
                                    retryMutation.isPending &&
                                    retryMutation.variables === job.id
                                  }
                                >
                                  <RotateCw className="h-3.5 w-3.5" /> Retry
                                </Button>
                              ) : null}
                            </div>
                          </TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </TableWrap>
                <OffsetPager offset={offset} count={jobs.length} onChange={setOffset} />
              </>
            )}
          </Card>
        </>
      ) : null}

      <AddDndDialog
        open={dndOpen}
        onClose={() => setDndOpen(false)}
        loading={addDndMutation.isPending}
        error={
          addDndMutation.error instanceof Error
            ? addDndMutation.error.message
            : null
        }
        onSubmit={(values) => addDndMutation.mutate(values)}
      />

      <ConfirmDialog
        open={Boolean(deactivating)}
        onClose={() => setDeactivating(null)}
        onConfirm={() => {
          if (deactivating) deactivateMutation.mutate(deactivating.id);
        }}
        title="Deactivate this DND entry?"
        description={
          <>
            Calls to{" "}
            <span className="font-mono font-medium text-slate-900">
              {maskPhone(deactivating?.phone)}
            </span>{" "}
            will no longer be blocked by this entry. Only do this when the customer has
            explicitly asked to be contacted again. The change is audited.
          </>
        }
        confirmLabel="Deactivate entry"
        tone="danger"
        loading={deactivateMutation.isPending}
      />
    </div>
  );
}

function AddDndDialog({
  open,
  onClose,
  onSubmit,
  loading,
  error,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (values: { phone: string; scope: string; reason: string }) => void;
  loading: boolean;
  error: string | null;
}) {
  const [phone, setPhone] = useState("");
  const [scope, setScope] = useState("business");
  const [reason, setReason] = useState("");

  const valid = /^\+?[0-9][0-9\s-]{6,20}$/.test(phone.trim());

  return (
    <Dialog
      open={open}
      onClose={() => {
        if (!loading) {
          setPhone("");
          setReason("");
          onClose();
        }
      }}
      title="Add a number to the DND registry"
      description="The compliance gate will block outbound calls to this number. The entry is audited."
      size="md"
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={loading}>
            Cancel
          </Button>
          <Button
            variant="danger"
            onClick={() => onSubmit({ phone: phone.trim(), scope, reason })}
            disabled={!valid}
            loading={loading}
          >
            <Ban className="h-4 w-4" aria-hidden /> Add to DND
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Phone" htmlFor="dnd-phone" required hint="Include country code.">
          <Input
            id="dnd-phone"
            value={phone}
            onChange={(event) => setPhone(event.target.value)}
            placeholder="+91…"
            inputMode="tel"
            disabled={loading}
          />
        </Field>
        <Field label="Scope" htmlFor="dnd-scope">
          <Select
            id="dnd-scope"
            value={scope}
            onChange={(event) => setScope(event.target.value)}
            disabled={loading}
          >
            <option value="business">This business</option>
            <option value="global">Global</option>
          </Select>
        </Field>
        <Field label="Reason" htmlFor="dnd-reason">
          <Textarea
            id="dnd-reason"
            rows={3}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Customer requested no contact on 12 Oct."
            disabled={loading}
          />
        </Field>
        <InlineAlert tone="warning" title="Compliance action">
          DND entries prevent calls. Removing them later should only happen with explicit
          customer consent.
        </InlineAlert>
        {error ? (
          <InlineAlert tone="danger" title="DND entry was not created">
            {error}
          </InlineAlert>
        ) : null}
      </div>
    </Dialog>
  );
}

function OffsetPager({
  offset,
  count,
  onChange,
}: {
  offset: number;
  count: number;
  onChange: (offset: number) => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-3">
      <p className="text-sm text-slate-500">
        Showing {count === 0 ? 0 : offset + 1}–{offset + count}
      </p>
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={offset === 0}
          onClick={() => onChange(Math.max(0, offset - PAGE_SIZE))}
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={count < PAGE_SIZE}
          onClick={() => onChange(offset + PAGE_SIZE)}
        >
          Next <ArrowRight className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}

/* Keep some imports meaningful for extensibility without dead UI. */
void ClipboardList;
void CheckCircle2;
void Search;
