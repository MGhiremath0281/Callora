import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  CalendarClock,
  CheckCircle2,
  ClipboardCopy,
  Download,
  FileText,
  Loader2,
  PhoneCall,
  RefreshCw,
  ShieldAlert,
  XCircle,
} from "lucide-react";

import { JsonBlock } from "@/components/domain/json-block";
import { LeadName } from "@/components/domain/lead-name";
import { Badge, DecisionBadge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ButtonLink } from "@/components/ui/button-link";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
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
import { ApiError } from "@/lib/api";
import {
  ACTIVE_CALL_STATUSES,
  LANGUAGE_NAMES,
  TERMINAL_CALL_STATUSES,
} from "@/lib/constants";
import {
  formatCurrency,
  formatDateTime,
  formatDuration,
  maskPhone,
  titleCase,
} from "@/lib/utils";
import { parseTranscript, transcriptAsText } from "@/lib/transcript";
import {
  cancelCall,
  getCall,
  getCallEvents,
  getCallOutcome,
  getVoiceLead,
  listComplianceDecisions,
  queuePostcall,
  refreshCall,
} from "@/services/callora";

export default function CallDetailPage() {
  const { callId = "" } = useParams();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState("summary");
  const [cancelOpen, setCancelOpen] = useState(false);

  const callQuery = useQuery({
    queryKey: ["call", callId],
    queryFn: () => getCall(callId),
    enabled: Boolean(callId),
    retry: false,
    refetchInterval: (query) => {
      const call = query.state.data;
      return call && ACTIVE_CALL_STATUSES.has(call.status) ? 5000 : false;
    },
  });

  const call = callQuery.data;
  const isActive = Boolean(call && ACTIVE_CALL_STATUSES.has(call.status));
  const isTerminal = Boolean(call && TERMINAL_CALL_STATUSES.has(call.status));

  const eventsQuery = useQuery({
    queryKey: ["call-events", callId],
    queryFn: () => getCallEvents(callId, { limit: 200 }),
    enabled: Boolean(callId),
    refetchInterval: isActive ? 10_000 : false,
  });

  const outcomeQuery = useQuery({
    queryKey: ["call-outcome", callId],
    queryFn: () => getCallOutcome(callId),
    enabled: Boolean(callId),
    retry: false,
  });

  const profileQuery = useQuery({
    queryKey: ["voice-lead", call?.lead_id],
    queryFn: () => getVoiceLead(call?.lead_id as string),
    enabled: Boolean(call?.lead_id),
    retry: false,
  });

  const decisionsQuery = useQuery({
    queryKey: ["call-decisions", call?.lead_id],
    queryFn: () =>
      listComplianceDecisions({ lead_id: call?.lead_id, limit: 50 }),
    enabled: Boolean(call?.lead_id),
  });

  const refreshMutation = useMutation({
    mutationFn: () => refreshCall(callId),
    onSuccess: (result) => {
      toast.success(
        "Call refreshed",
        result.message || "The backend polled the provider and updated the call.",
      );
      void queryClient.invalidateQueries({ queryKey: ["call", callId] });
      void queryClient.invalidateQueries({ queryKey: ["call-events", callId] });
      void queryClient.invalidateQueries({ queryKey: ["calls"] });
    },
    onError: (error) => {
      toast.error(
        "Refresh failed",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const cancelMutation = useMutation({
    mutationFn: () => cancelCall(callId),
    onSuccess: () => {
      setCancelOpen(false);
      toast.success(
        "Call cancelled",
        "The cancellation request was accepted. The final provider status may take a moment to arrive.",
      );
      void queryClient.invalidateQueries({ queryKey: ["call", callId] });
      void queryClient.invalidateQueries({ queryKey: ["calls"] });
      void queryClient.invalidateQueries({ queryKey: ["voice-stats"] });
    },
    onError: (error) => {
      toast.error(
        "Call was not cancelled",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const postcallMutation = useMutation({
    mutationFn: () => queuePostcall(callId),
    onSuccess: () => {
      toast.success(
        "Post-call processing queued",
        "The backend will analyse the call asynchronously. Refresh in a few moments to see the outcome.",
      );
      void queryClient.invalidateQueries({ queryKey: ["call", callId] });
      void queryClient.invalidateQueries({ queryKey: ["call-outcome", callId] });
      void queryClient.invalidateQueries({ queryKey: ["recommendations"] });
    },
    onError: (error) => {
      toast.error(
        "Post-call processing was not queued",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  useEffect(() => {
    if (!callId) return;
    setTab("summary");
  }, [callId]);

  const outcomeMissing =
    outcomeQuery.error instanceof ApiError && outcomeQuery.error.isNotFound;

  const relevantDecisions = useMemo(() => {
    if (!call) return [];
    return (decisionsQuery.data?.items ?? []).filter(
      (decision) =>
        decision.call_id === call.id || decision.id === call.compliance_decision_id,
    );
  }, [call, decisionsQuery.data]);

  const turns = useMemo(() => parseTranscript(call?.transcript), [call?.transcript]);
  const events = eventsQuery.data?.items ?? [];

  const tabs = useMemo(
    () => [
      { id: "summary", label: "Summary" },
      {
        id: "transcript",
        label: "Transcript",
        count: turns.length || undefined,
      },
      { id: "recording", label: "Recording" },
      { id: "events", label: "Events & technical", count: events.length || undefined },
      {
        id: "compliance",
        label: "Compliance",
        count: relevantDecisions.length || undefined,
      },
    ],
    [turns.length, events.length, relevantDecisions.length],
  );

  if (callQuery.isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-9 w-64" />
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (callQuery.error || !call) {
    return (
      <div>
        <ButtonLink to="/calls" variant="ghost" size="sm" className="mb-3">
          <ArrowLeft className="h-3.5 w-3.5" /> Calls
        </ButtonLink>
        <Card>
          <ErrorState
            error={callQuery.error}
            title="Call could not be loaded"
            onRetry={() => void callQuery.refetch()}
          />
        </Card>
      </div>
    );
  }

  async function copyCallId() {
    try {
      await navigator.clipboard.writeText(call!.id);
      toast.success("Call id copied");
    } catch {
      toast.error("Clipboard unavailable", "Select the call id manually.");
    }
  }

  function downloadTranscript() {
    const text = transcriptAsText(call!.transcript);
    if (!text) return;
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${call!.id}-transcript.txt`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div>
      <div className="mb-2">
        <ButtonLink to="/calls" variant="ghost" size="sm">
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> All calls
        </ButtonLink>
      </div>

      <PageHeader
        title="Call detail"
        description={
          <>
            <LeadName leadId={call.lead_id} /> ·{" "}
            <span className="font-mono text-xs">{call.id}</span>
          </>
        }
        actions={
          <>
            <Button
              variant="outline"
              onClick={() => refreshMutation.mutate()}
              loading={refreshMutation.isPending}
            >
              <RefreshCw className="h-4 w-4" aria-hidden /> Refresh
            </Button>
            <ButtonLink
              to={`/handoffs?new=1&call=${encodeURIComponent(call.id)}&lead=${encodeURIComponent(call.lead_id)}`}
              variant="outline"
            >
              <CalendarClock className="h-4 w-4" aria-hidden /> Request handoff
            </ButtonLink>
            {isActive ? (
              <Button variant="danger" onClick={() => setCancelOpen(true)}>
                <XCircle className="h-4 w-4" aria-hidden /> Cancel call
              </Button>
            ) : null}
          </>
        }
        meta={
          <>
            <StatusBadge status={call.status} />
            {call.simulated ? <Badge tone="violet">simulated</Badge> : null}
            {call.direction ? <Badge tone="neutral">{call.direction}</Badge> : null}
            <Badge tone="neutral">{call.provider}</Badge>
            {isActive ? (
              <span className="inline-flex items-center gap-1 text-xs text-accent-600">
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden /> Live · refreshing
              </span>
            ) : null}
            <button
              type="button"
              className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-700"
              onClick={() => void copyCallId()}
            >
              <ClipboardCopy className="h-3 w-3" aria-hidden /> Copy call id
            </button>
          </>
        }
      />

      {call.status === "blocked" ? (
        <div className="mb-4">
          <InlineAlert tone="danger" title="The compliance gate blocked this call">
            {call.blocked_reason || "The backend recorded the block without a reason."}
            {call.compliance_decision_id ? (
              <span className="ml-1 font-mono text-xs">
                ({call.compliance_decision_id})
              </span>
            ) : null}
          </InlineAlert>
        </div>
      ) : null}

      {call.error_message ? (
        <div className="mb-4">
          <InlineAlert tone="warning" title="Provider or processing error">
            {call.error_message}
          </InlineAlert>
        </div>
      ) : null}

      <Tabs items={tabs} value={tab} onChange={setTab} className="mb-4" />

      {tab === "summary" ? (
        <div className="grid gap-4 xl:grid-cols-3">
          <div className="space-y-4 xl:col-span-2">
            <Card>
              <CardHeader
                title="Post-call outcome"
                description="Structured qualification produced by the backend after the call."
                actions={
                  isTerminal || call.status === "blocked" ? (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => postcallMutation.mutate()}
                      loading={postcallMutation.isPending}
                    >
                      <FileText className="h-3.5 w-3.5" aria-hidden /> Queue analysis
                    </Button>
                  ) : null
                }
              />
              {outcomeQuery.isPending ? (
                <CardBody>
                  <Skeleton className="h-24 w-full" />
                </CardBody>
              ) : outcomeMissing ? (
                <EmptyState
                  icon={<FileText className="h-5 w-5" aria-hidden />}
                  title={
                    isActive
                      ? "Outcome appears after the call ends"
                      : "No post-call analysis yet"
                  }
                  description={
                    isActive
                      ? "The call is still in progress. The backend writes the outcome when the provider reports completion."
                      : "Queue post-call processing to extract the summary, qualification and follow-up actions. This runs asynchronously."
                  }
                  action={
                    isActive ? undefined : (
                      <Button
                        size="sm"
                        onClick={() => postcallMutation.mutate()}
                        loading={postcallMutation.isPending}
                      >
                        Queue post-call analysis
                      </Button>
                    )
                  }
                />
              ) : outcomeQuery.error ? (
                <ErrorState
                  error={outcomeQuery.error}
                  title="Outcome unavailable"
                  onRetry={() => void outcomeQuery.refetch()}
                />
              ) : outcomeQuery.data ? (
                <CardBody className="space-y-4">
                  <div className="grid gap-3 sm:grid-cols-4">
                    <Fact label="Disposition" value={titleCase(outcomeQuery.data.disposition)} />
                    <Fact
                      label="Qualified"
                      value={
                        outcomeQuery.data.qualified === null
                          ? "Not determined"
                          : outcomeQuery.data.qualified
                            ? "Yes"
                            : "No"
                      }
                    />
                    <Fact
                      label="Score"
                      value={
                        outcomeQuery.data.qualification_score === null
                          ? "—"
                          : String(outcomeQuery.data.qualification_score)
                      }
                    />
                    <Fact
                      label="Language"
                      value={
                        outcomeQuery.data.language_detected
                          ? LANGUAGE_NAMES[outcomeQuery.data.language_detected] ||
                            outcomeQuery.data.language_detected
                          : "—"
                      }
                    />
                  </div>

                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                      Summary
                    </p>
                    <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
                      {outcomeQuery.data.summary || "No summary was returned."}
                    </p>
                  </div>

                  {outcomeQuery.data.refusal_reason ? (
                    <InlineAlert tone="warning" title="Refusal">
                      {outcomeQuery.data.refusal_reason}
                      {outcomeQuery.data.refusal_category ? (
                        <span className="ml-1 text-xs">
                          ({outcomeQuery.data.refusal_category})
                        </span>
                      ) : null}
                    </InlineAlert>
                  ) : null}

                  {outcomeQuery.data.follow_up_actions?.length ? (
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                        Suggested follow-up
                      </p>
                      <ul className="mt-1 space-y-1 text-sm text-slate-700">
                        {outcomeQuery.data.follow_up_actions.map((action, index) => (
                          <li key={index} className="flex items-start gap-2">
                            <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
                            {action}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}

                  {Object.keys(outcomeQuery.data.qualification_answers || {}).length ? (
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                        Qualification answers
                      </p>
                      <div className="mt-1">
                        <JsonBlock
                          title="Structured answers"
                          value={outcomeQuery.data.qualification_answers}
                        />
                      </div>
                    </div>
                  ) : null}

                  {outcomeQuery.data.handoff_required ? (
                    <InlineAlert tone="info" title="Human handoff required">
                      {outcomeQuery.data.handoff_briefing ||
                        "The outcome flags a human follow-up. Check the handoff queue."}
                    </InlineAlert>
                  ) : null}
                </CardBody>
              ) : null}
            </Card>

            <Card>
              <CardHeader
                title="Transcript preview"
                description="The first turns returned by the provider."
                actions={
                  turns.length ? (
                    <Button variant="outline" size="sm" onClick={() => setTab("transcript")}>
                      Open transcript
                    </Button>
                  ) : null
                }
              />
              {turns.length === 0 ? (
                <EmptyState
                  title="No transcript available"
                  description="Transcripts are provided by the voice provider. If none is returned, Callora does not generate a synthetic one."
                  className="py-8"
                />
              ) : (
                <ul className="divide-y divide-slate-100">
                  {turns.slice(0, 4).map((turn, index) => (
                    <li key={index} className="px-5 py-3">
                      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                        {turn.speaker || "Unattributed turn"}
                      </p>
                      <p className="mt-0.5 line-clamp-2 text-sm text-slate-700">
                        {turn.text}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>

          <div className="space-y-4">
            <Card>
              <CardHeader
                title="Lead"
                actions={
                  <ButtonLink
                    to={`/leads/${call.lead_id}`}
                    variant="ghost"
                    size="sm"
                  >
                    Open lead
                  </ButtonLink>
                }
              />
              <CardBody className="space-y-3">
                <div>
                  <p className="text-sm font-semibold text-slate-900">
                    <LeadName leadId={call.lead_id} />
                  </p>
                  <p className="font-mono text-xs text-slate-500">
                    {maskPhone(call.to_number)}
                  </p>
                </div>
                {profileQuery.isPending ? (
                  <Skeleton className="h-10 w-full" />
                ) : profileQuery.error ? (
                  <p className="text-xs text-slate-500">
                    Voice profile and consent could not be loaded.
                  </p>
                ) : (
                  <>
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-slate-500">Consent</span>
                      <StatusBadge
                        status={profileQuery.data?.consent?.status || "missing"}
                      />
                    </div>
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-slate-500">DND</span>
                      <Badge
                        tone={profileQuery.data?.dnd_active ? "danger" : "success"}
                      >
                        {profileQuery.data?.dnd_active ? "blocked" : "clear"}
                      </Badge>
                    </div>
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-slate-500">Do not call</span>
                      <Badge
                        tone={profileQuery.data?.do_not_call ? "warning" : "success"}
                      >
                        {profileQuery.data?.do_not_call ? "set" : "clear"}
                      </Badge>
                    </div>
                  </>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Call facts" />
              <CardBody className="space-y-2 text-sm">
                <FactRow label="Duration" value={formatDuration(call.duration_seconds)} />
                <FactRow
                  label="Cost"
                  value={formatCurrency(call.cost, call.currency || "INR")}
                />
                <FactRow
                  label="Language"
                  value={
                    call.language ? LANGUAGE_NAMES[call.language] || call.language : "—"
                  }
                />
                <FactRow label="Direction" value={titleCase(call.direction)} />
                <FactRow label="Scheduled" value={formatDateTime(call.scheduled_at)} />
                <FactRow label="Initiated" value={formatDateTime(call.initiated_at)} />
                <FactRow label="Connected" value={formatDateTime(call.connected_at)} />
                <FactRow label="Ended" value={formatDateTime(call.ended_at)} />
                <FactRow
                  label="Provider call id"
                  value={call.provider_call_id || "—"}
                  mono
                />
                <FactRow label="From" value={maskPhone(call.from_number)} mono />
                <FactRow label="To" value={maskPhone(call.to_number)} mono />
                <FactRow label="Simulated" value={call.simulated ? "Yes" : "No"} />
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Live actions" />
              <CardBody className="space-y-2">
                {isActive ? (
                  <InlineAlert tone="warning" title="This call is live">
                    Cancelling is permanent for this attempt and is confirmed before the
                    request is sent.
                  </InlineAlert>
                ) : (
                  <p className="text-xs text-slate-500">
                    The call is no longer active. Refresh to pull the latest provider state.
                  </p>
                )}
                <Button
                  variant="outline"
                  className="w-full"
                  onClick={() => refreshMutation.mutate()}
                  loading={refreshMutation.isPending}
                >
                  <RefreshCw className="h-4 w-4" aria-hidden /> Refresh from provider
                </Button>
                {isActive ? (
                  <Button
                    variant="danger"
                    className="w-full"
                    onClick={() => setCancelOpen(true)}
                  >
                    <XCircle className="h-4 w-4" aria-hidden /> Cancel this call
                  </Button>
                ) : null}
              </CardBody>
            </Card>
          </div>
        </div>
      ) : null}

      {tab === "transcript" ? (
        <Card>
          <CardHeader
            title="Transcript"
            description="Stored verbatim from the provider. Speaker labels are normalised only when the source labels them."
            actions={
              turns.length ? (
                <Button variant="outline" size="sm" onClick={downloadTranscript}>
                  <Download className="h-3.5 w-3.5" aria-hidden /> Download .txt
                </Button>
              ) : null
            }
          />
          {turns.length === 0 ? (
            <EmptyState
              icon={<FileText className="h-5 w-5" aria-hidden />}
              title="No transcript"
              description="The provider has not returned a transcript for this call. It may still arrive through a webhook."
            />
          ) : (
            <ul className="divide-y divide-slate-100">
              {turns.map((turn, index) => (
                <li key={index} className="flex gap-3 px-5 py-3">
                  <span
                    className={
                      turn.speaker === "Agent"
                        ? "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent-50 text-[10px] font-semibold text-accent-700"
                        : turn.speaker === "Customer"
                          ? "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-sky-50 text-[10px] font-semibold text-sky-700"
                          : "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[10px] font-semibold text-slate-500"
                    }
                  >
                    {turn.speaker ? turn.speaker.slice(0, 2).toUpperCase() : "—"}
                  </span>
                  <div className="min-w-0">
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                      {turn.speaker || "Unattributed"}
                    </p>
                    <p className="mt-0.5 whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
                      {turn.text}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      ) : null}

      {tab === "recording" ? (
        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader
              title="Recording"
              description="The provider's recording URL, when available."
            />
            <CardBody>
              {call.recording_url ? (
                <div className="space-y-3">
                  <audio controls src={call.recording_url} className="w-full">
                    Your browser does not support audio playback.
                  </audio>
                  <a
                    href={call.recording_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1.5 text-sm font-medium text-accent-600 hover:text-accent-700"
                  >
                    <Download className="h-4 w-4" aria-hidden /> Open recording
                  </a>
                  <p className="text-xs leading-relaxed text-slate-500">
                    Provider recordings are usually signed and may expire. Callora does not
                    proxy the audio, so a URL that requires backend credentials may not
                    play in the browser.
                  </p>
                </div>
              ) : (
                <EmptyState
                  title="No recording available"
                  description="The provider has not returned a recording URL. Recording availability depends on the provider configuration and the call outcome."
                />
              )}
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Access note" />
            <CardBody className="space-y-2 text-sm text-slate-600">
              <p className="flex items-start gap-2">
                <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
                Recordings can contain sensitive customer data. Share them only with
                authorised team members.
              </p>
              <p className="text-xs text-slate-500">
                Callora shows the URL the backend returned; it never fabricates a recording
                or generates a transcript when the provider did not supply one.
              </p>
            </CardBody>
          </Card>
        </div>
      ) : null}

      {tab === "events" ? (
        <div className="space-y-4">
          <Card>
            <CardHeader
              title="Provider events"
              description="Raw webhook events stored by the backend, newest first where supplied."
              actions={
                <span className="text-xs text-slate-400">{events.length} events</span>
              }
            />
            {eventsQuery.isPending ? (
              <TableSkeleton rows={5} columns={4} />
            ) : eventsQuery.error ? (
              <ErrorState
                error={eventsQuery.error}
                title="Events unavailable"
                onRetry={() => void eventsQuery.refetch()}
              />
            ) : events.length === 0 ? (
              <EmptyState
                title="No provider events"
                description="Events are written when the provider calls the webhook. A simulated call may not produce any."
              />
            ) : (
              <ul className="divide-y divide-slate-100">
                {events.map((event) => (
                  <li key={event.id} className="px-5 py-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone="neutral">{event.event_type}</Badge>
                      {event.provider_status ? (
                        <StatusBadge status={event.provider_status} />
                      ) : null}
                      {event.processed ? (
                        <Badge tone="success">processed</Badge>
                      ) : (
                        <Badge tone="warning">not processed</Badge>
                      )}
                      {event.signature_valid === false ? (
                        <Badge tone="danger">signature invalid</Badge>
                      ) : event.signature_valid ? (
                        <Badge tone="success">signature valid</Badge>
                      ) : null}
                      <span className="ml-auto text-[11px] text-slate-400">
                        {formatDateTime(event.received_at)}
                      </span>
                    </div>
                    <p className="mt-1 font-mono text-[11px] text-slate-500">
                      {event.event_key}
                      {event.provider_event_id ? ` · ${event.provider_event_id}` : ""}
                    </p>
                    {event.processing_error ? (
                      <p className="mt-1 text-xs text-rose-600">{event.processing_error}</p>
                    ) : null}
                    <div className="mt-2">
                      <JsonBlock title="Event payload" value={event.payload} />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Call metadata" description="Stored provider metadata." />
              <CardBody>
                <JsonBlock title="metadata" value={call.metadata} />
              </CardBody>
            </Card>
            <Card>
              <CardHeader title="Identifiers" description="Useful when escalating to support." />
              <CardBody className="space-y-2 text-sm">
                <FactRow label="Call id" value={call.id} mono />
                <FactRow label="Business id" value={call.business_id} mono />
                <FactRow label="Lead id" value={call.lead_id} mono />
                <FactRow label="Provider" value={call.provider} />
                <FactRow label="Provider call id" value={call.provider_call_id || "—"} mono />
                <FactRow
                  label="Compliance decision id"
                  value={call.compliance_decision_id || "—"}
                  mono
                />
                <FactRow label="Created" value={formatDateTime(call.created_at)} />
                <FactRow label="Updated" value={formatDateTime(call.updated_at)} />
              </CardBody>
            </Card>
          </div>
        </div>
      ) : null}

      {tab === "compliance" ? (
        <div className="space-y-4">
          <Card>
            <CardHeader
              title="Compliance decisions for this call"
              description="Only decisions linked to this call record are shown."
            />
            {decisionsQuery.isPending ? (
              <TableSkeleton rows={3} columns={4} />
            ) : decisionsQuery.error ? (
              <ErrorState
                error={decisionsQuery.error}
                title="Decisions unavailable"
                onRetry={() => void decisionsQuery.refetch()}
              />
            ) : relevantDecisions.length === 0 ? (
              <EmptyState
                icon={<ShieldAlert className="h-5 w-5" aria-hidden />}
                title="No linked compliance decision"
                description={
                  call.compliance_decision_id
                    ? `The call stores decision id ${call.compliance_decision_id}, but it was not returned in the lead's recent decisions. Open Compliance & Audit to search the full audit trail.`
                    : "The backend did not attach a compliance decision to this call."
                }
                action={
                  <ButtonLink
                    to={`/compliance?tab=decisions`}
                    variant="outline"
                    size="sm"
                  >
                    Open decision log
                  </ButtonLink>
                }
              />
            ) : (
              <ul className="divide-y divide-slate-100">
                {relevantDecisions.map((decision) => (
                  <li key={decision.id} className="px-5 py-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <DecisionBadge decision={decision.decision} />
                      <span className="font-mono text-[11px] text-slate-500">
                        {decision.reason_code}
                      </span>
                      <span className="ml-auto text-[11px] text-slate-400">
                        {formatDateTime(decision.created_at)}
                      </span>
                    </div>
                    <p className="mt-1 text-sm text-slate-600">
                      {decision.reason || "No reason supplied."}
                    </p>
                    {decision.checks?.length ? (
                      <div className="mt-2">
                        <JsonBlock title="Checks evaluated" value={decision.checks} />
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            <CardHeader
              title="Consent and DND at a glance"
              description="The current state of the lead, not the state at the time of the call."
            />
            <CardBody className="grid gap-3 sm:grid-cols-3">
              <Fact
                label="Consent now"
                value={
                  profileQuery.isPending ? (
                    "Loading…"
                  ) : (
                    <StatusBadge
                      status={profileQuery.data?.consent?.status || "missing"}
                    />
                  )
                }
              />
              <Fact
                label="DND now"
                value={
                  profileQuery.isPending ? (
                    "Loading…"
                  ) : (
                    <Badge tone={profileQuery.data?.dnd_active ? "danger" : "success"}>
                      {profileQuery.data?.dnd_active ? "blocked" : "clear"}
                    </Badge>
                  )
                }
              />
              <Fact
                label="Decision recorded at"
                value={formatDateTime(
                  relevantDecisions[0]?.created_at || call.created_at,
                )}
              />
            </CardBody>
          </Card>
        </div>
      ) : null}

      <ConfirmDialog
        open={cancelOpen}
        onClose={() => setCancelOpen(false)}
        onConfirm={() => cancelMutation.mutate()}
        title="Cancel this live call?"
        description={
          <>
            The backend will ask the provider to end the call. This cannot be undone and the
            lead may have already heard part of the conversation. The cancellation is
            audited.
          </>
        }
        confirmLabel="Cancel call"
        tone="danger"
        loading={cancelMutation.isPending}
      />
    </div>
  );
}

function Fact({
  label,
  value,
}: {
  label: string;
  value: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-slate-200 p-3">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <div className="mt-1 text-sm text-slate-800">{value}</div>
    </div>
  );
}

function FactRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-slate-100 py-1.5 last:border-0">
      <span className="shrink-0 text-slate-500">{label}</span>
      <span
        className={
          mono
            ? "break-all text-right font-mono text-xs text-slate-800"
            : "text-right text-sm text-slate-800"
        }
      >
        {value}
      </span>
    </div>
  );
}
