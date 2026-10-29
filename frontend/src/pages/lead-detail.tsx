import { useMemo, useState, type ComponentType, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import {
  AlertTriangle,
  ArrowLeft,
  CalendarClock,
  CheckCircle2,
  FileText,
  Mail,
  Pencil,
  PhoneCall,
  ShieldAlert,
  ShieldCheck,
  UserRound,
} from "lucide-react";

import { useBusiness } from "@/app/auth";
import { StartCallDialog } from "@/components/domain/start-call-dialog";
import { JsonBlock } from "@/components/domain/json-block";
import { Badge, DecisionBadge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ButtonLink } from "@/components/ui/button-link";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
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
  CONSENT_CHANNELS,
  CONSENT_STATUSES,
  LANGUAGE_NAMES,
  TIMEZONES,
} from "@/lib/constants";
import {
  formatCurrency,
  formatDateTime,
  formatDuration,
  maskPhone,
  titleCase,
} from "@/lib/utils";
import { consentSchema, type ConsentFormValues } from "@/lib/validation";
import {
  addDnd,
  getConsent,
  getLead,
  getVoiceLead,
  listCalls,
  listComplianceDecisions,
  listFollowups,
  listHandoffs,
  recordConsent,
  requestCallback,
  updateVoiceLeadProfile,
} from "@/services/callora";

function toIso(value?: string) {
  return value ? new Date(value).toISOString() : null;
}

export default function LeadDetailPage() {
  const { leadId = "" } = useParams();
  const business = useBusiness();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState("overview");
  const [callOpen, setCallOpen] = useState(false);
  const [consentOpen, setConsentOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [dndOpen, setDndOpen] = useState(false);
  const [callbackOpen, setCallbackOpen] = useState(false);

  const leadQuery = useQuery({
    queryKey: ["lead", leadId],
    queryFn: () => getLead(leadId),
    enabled: Boolean(leadId),
    retry: false,
  });

  const profileQuery = useQuery({
    queryKey: ["voice-lead", leadId],
    queryFn: () => getVoiceLead(leadId),
    enabled: Boolean(leadId),
    retry: false,
  });

  const consentQuery = useQuery({
    queryKey: ["consent", leadId],
    queryFn: () => getConsent(leadId),
    enabled: Boolean(leadId),
    retry: false,
  });

  const callsQuery = useQuery({
    queryKey: ["lead-calls", leadId],
    queryFn: () => listCalls({ lead_id: leadId, limit: 100 }),
    enabled: Boolean(leadId),
  });

  const decisionsQuery = useQuery({
    queryKey: ["lead-decisions", leadId],
    queryFn: () => listComplianceDecisions({ lead_id: leadId, limit: 50 }),
    enabled: Boolean(leadId),
  });

  const handoffsQuery = useQuery({
    queryKey: ["lead-handoffs"],
    queryFn: () => listHandoffs({ limit: 200 }),
    enabled: Boolean(leadId),
  });

  const followupsQuery = useQuery({
    queryKey: ["lead-followups", leadId],
    queryFn: () => listFollowups({ lead_id: leadId, limit: 100 }),
    enabled: Boolean(leadId),
  });

  const lead = leadQuery.data;
  const profile = profileQuery.data;
  const consentRecords = consentQuery.data ?? [];
  const calls = callsQuery.data?.items ?? [];
  const latestCall = calls[0];
  const decisions = decisionsQuery.data?.items ?? [];
  const handoffs = (handoffsQuery.data?.items ?? []).filter(
    (handoff) => handoff.lead_id === leadId,
  );
  const followups = followupsQuery.data?.items ?? [];
  const consentStatus = profile
    ? profile.consent?.status || "missing"
    : "unknown";

  const consentForm = useForm<ConsentFormValues>({
    resolver: zodResolver(consentSchema),
    defaultValues: {
      status: "granted",
      channel: "voice",
      source: "Dashboard",
      granted_at: "",
      expires_at: "",
      note: "",
    },
  });

  const consentMutation = useMutation({
    mutationFn: (values: ConsentFormValues) =>
      recordConsent(leadId, {
        status: values.status,
        channel: values.channel,
        phone: lead?.phone || null,
        source: values.source?.trim() || "Dashboard",
        granted_at: toIso(values.granted_at),
        expires_at: toIso(values.expires_at),
        revoked_at: values.status === "revoked" ? new Date().toISOString() : null,
        evidence: values.note?.trim() ? { note: values.note.trim() } : {},
      }),
    onSuccess: () => {
      toast.success("Consent evidence recorded", "The compliance gate will use this immediately.");
      setConsentOpen(false);
      consentForm.reset();
      void queryClient.invalidateQueries({ queryKey: ["consent", leadId] });
      void queryClient.invalidateQueries({ queryKey: ["voice-lead", leadId] });
      void queryClient.invalidateQueries({ queryKey: ["voice-leads"] });
      void queryClient.invalidateQueries({ queryKey: ["voice-stats"] });
    },
    onError: (error) => {
      toast.error(
        "Consent evidence was not saved",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const profileForm = {
    language: useState(""),
    timezone: useState(""),
    notes: useState(""),
    doNotCall: useState(false),
  };

  const profileMutation = useMutation({
    mutationFn: () =>
      updateVoiceLeadProfile(leadId, {
        preferred_language: profileForm.language[0] || null,
        timezone: profileForm.timezone[0] || null,
        notes: profileForm.notes[0] || null,
        do_not_call: profileForm.doNotCall[0],
      }),
    onSuccess: () => {
      toast.success("Voice profile updated");
      setProfileOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["voice-lead", leadId] });
      void queryClient.invalidateQueries({ queryKey: ["voice-leads"] });
    },
    onError: (error) => {
      toast.error(
        "Voice profile was not updated",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  function openProfile() {
    if (!profile) return;
    profileForm.language[1](profile.preferred_language || "");
    profileForm.timezone[1](profile?.timezone || "");
    profileForm.notes[1](profile?.notes || "");
    profileForm.doNotCall[1](profile?.do_not_call || false);
    setProfileOpen(true);
  }

  const dndMutation = useMutation({
    mutationFn: (reason: string) =>
      addDnd({
        phone: lead?.phone || "",
        scope: "business",
        reason: reason || "Added from lead dashboard",
        source: "dashboard",
      }),
    onSuccess: () => {
      toast.success("Number added to DND", "The compliance gate will block calls to it.");
      setDndOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["voice-lead", leadId] });
      void queryClient.invalidateQueries({ queryKey: ["voice-leads"] });
      void queryClient.invalidateQueries({ queryKey: ["dnd"] });
      void queryClient.invalidateQueries({ queryKey: ["voice-stats"] });
    },
    onError: (error) => {
      toast.error(
        "DND entry was not created",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const callbackMutation = useMutation({
    mutationFn: (values: { reason: string; briefing: string; target: string }) =>
      requestCallback({
        lead_id: leadId,
        call_id: latestCall?.id || null,
        reason: values.reason,
        briefing: values.briefing,
        target_number: values.target || null,
        requested_by: "dashboard",
      }),
    onSuccess: (handoff) => {
      toast.success("Callback requested", `Handoff ${handoff.id} is now in the queue.`);
      setCallbackOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["handoffs"] });
      void queryClient.invalidateQueries({ queryKey: ["lead-handoffs", leadId] });
      void queryClient.invalidateQueries({ queryKey: ["voice-stats"] });
    },
    onError: (error) => {
      toast.error(
        "Callback was not requested",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  const tabs = useMemo(
    () => [
      { id: "overview", label: "Overview" },
      { id: "compliance", label: "Consent & compliance", count: consentRecords.length },
      { id: "calls", label: "Call history", count: calls.length },
      { id: "activity", label: "Handoffs & tasks", count: handoffs.length + followups.length },
    ],
    [consentRecords.length, calls.length, handoffs.length, followups.length],
  );

  if (leadQuery.isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-9 w-64" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (leadQuery.error || !lead) {
    return (
      <div>
        <ButtonLink to="/leads" variant="ghost" size="sm" className="mb-3">
          <ArrowLeft className="h-3.5 w-3.5" /> Leads
        </ButtonLink>
        <Card>
          <ErrorState
            error={leadQuery.error}
            title="Lead could not be loaded"
            onRetry={() => void leadQuery.refetch()}
          />
        </Card>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-2">
        <ButtonLink to="/leads" variant="ghost" size="sm">
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> All leads
        </ButtonLink>
      </div>

      <PageHeader
        title={lead.name}
        description={`${titleCase(lead.source)} lead · ${maskPhone(lead.phone)} · created ${formatDateTime(lead.created_at)}`}
        actions={
          <>
            <Button
              variant="outline"
              onClick={openProfile}
              disabled={!profile}
              title={!profile ? "The voice profile is not loaded." : undefined}
            >
              <Pencil className="h-4 w-4" aria-hidden /> Edit profile
            </Button>
            <Button variant="outline" onClick={() => setCallbackOpen(true)}>
              <CalendarClock className="h-4 w-4" aria-hidden /> Request callback
            </Button>
            <Button
              onClick={() => setCallOpen(true)}
              disabled={!business.is_active}
              title={
                !business.is_active
                  ? "Activate the agent before placing calls."
                  : "Run the compliance gate and start a call."
              }
            >
              <PhoneCall className="h-4 w-4" aria-hidden /> Start call
            </Button>
          </>
        }
        meta={
          <>
            {profile ? (
              <StatusBadge status={consentStatus} />
            ) : (
              <Badge tone="neutral">Consent unknown</Badge>
            )}
            {profile ? (
              profile.dnd_active ? (
                <Badge tone="danger">DND blocked</Badge>
              ) : (
                <Badge tone="success">Not on DND</Badge>
              )
            ) : (
              <Badge tone="neutral">DND unknown</Badge>
            )}
            {profile?.do_not_call ? <Badge tone="warning">Do not call</Badge> : null}
            <Badge tone="neutral">{lead.id}</Badge>
            <span className="text-xs text-slate-400">
              {LANGUAGE_NAMES[lead.language || ""] || lead.language || "No language"}
            </span>
          </>
        }
      />

      {!business.is_active ? (
        <div className="mb-4">
          <InlineAlert tone="warning" title="The agent is not active">
            Outbound calls are disabled at the workspace level. The compliance gate would
            refuse the attempt. Activate the agent in Agent Studio first.
          </InlineAlert>
        </div>
      ) : null}

      {consentStatus === "unknown" ? (
        <div className="mb-4">
          <InlineAlert tone="warning" title="Consent status unavailable">
            The voice profile could not be loaded, so the UI does not claim this lead has
            consent. The backend compliance gate remains authoritative when a call is attempted.
          </InlineAlert>
        </div>
      ) : consentStatus === "missing" ||
        consentStatus === "revoked" ||
        consentStatus === "expired" ? (
        <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <InlineAlert
            tone="warning"
            title={
              consentStatus === "missing"
                ? "No consent evidence"
                : `Consent is ${consentStatus}`
            }
          >
            A compliant call requires valid consent evidence. Record it before dialling;
            the backend will block the call otherwise.
          </InlineAlert>
          <Button
            variant="secondary"
            className="shrink-0"
            onClick={() => setConsentOpen(true)}
          >
            <ShieldCheck className="h-4 w-4" aria-hidden /> Record consent
          </Button>
        </div>
      ) : null}

      <Tabs items={tabs} value={tab} onChange={setTab} className="mb-4" />

      {tab === "overview" ? (
        <div className="grid gap-4 xl:grid-cols-3">
          <div className="space-y-4 xl:col-span-2">
            <Card>
              <CardHeader
                title="Contact"
                description="Canonical lead record from Module 1."
              />
              <CardBody className="grid gap-4 sm:grid-cols-2">
                <Detail label="Name" value={lead.name} icon={UserRound} />
                <Detail label="Phone" value={maskPhone(lead.phone)} icon={PhoneCall} mono />
                <Detail label="Email" value={lead.email || "—"} icon={Mail} />
                <Detail label="Source" value={titleCase(lead.source)} />
                <Detail
                  label="Deal value"
                  value={lead.deal_value ? formatCurrency(Number(lead.deal_value), "INR") : "—"}
                />
                <Detail label="External id" value={lead.external_id || "—"} mono />
                <Detail label="Last contact" value={formatDateTime(lead.last_contact_at)} />
                <Detail label="Created" value={formatDateTime(lead.created_at)} />
                {Object.keys(lead.custom_fields || {}).length ? (
                  <div className="sm:col-span-2">
                    <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                      Custom fields
                    </p>
                    <div className="mt-2">
                      <JsonBlock title="Custom field data" value={lead.custom_fields} />
                    </div>
                  </div>
                ) : null}
              </CardBody>
            </Card>

            <Card>
              <CardHeader
                title="Voice profile"
                description="Language preference and operator notes used by the voice workflow."
                actions={
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={openProfile}
                    disabled={!profile}
                    title={!profile ? "The voice profile is not loaded." : undefined}
                  >
                    <Pencil className="h-3.5 w-3.5" aria-hidden /> Edit
                  </Button>
                }
              />
              {profileQuery.isPending ? (
                <CardBody>
                  <Skeleton className="h-24 w-full" />
                </CardBody>
              ) : profileQuery.error ? (
                <ErrorState
                  error={profileQuery.error}
                  title="Voice profile unavailable"
                  onRetry={() => void profileQuery.refetch()}
                />
              ) : (
                <CardBody>
                  <div className="grid gap-4 sm:grid-cols-3">
                    <Detail
                      label="Preferred language"
                      value={
                        profile?.preferred_language
                          ? LANGUAGE_NAMES[profile.preferred_language] ||
                            profile.preferred_language
                          : "—"
                      }
                    />
                    <Detail label="Timezone" value={profile?.timezone || "—"} />
                    <Detail
                      label="Call attempts"
                      value={String(profile?.call_attempts ?? 0)}
                    />
                    <Detail
                      label="Last call"
                      value={formatDateTime(profile?.last_call_at)}
                    />
                    <Detail
                      label="Operational DNC"
                      value={profile?.do_not_call ? "Set" : "Not set"}
                    />
                    <Detail
                      label="DND registry"
                      value={profile?.dnd_active ? "Number is blocked" : "Clear"}
                    />
                  </div>
                  <div className="mt-4 rounded-lg border border-slate-200 p-3">
                    <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                      Internal notes
                    </p>
                    <p className="mt-1 whitespace-pre-wrap text-sm text-slate-700">
                      {profile?.notes || "No notes recorded."}
                    </p>
                  </div>
                </CardBody>
              )}
            </Card>

            <Card>
              <CardHeader
                title="Latest call"
                description="The most recent call record for this lead."
                actions={
                  latestCall ? (
                    <ButtonLink
                      to={`/calls/${latestCall.id}`}
                      variant="outline"
                      size="sm"
                    >
                      Open call
                    </ButtonLink>
                  ) : null
                }
              />
              {callsQuery.isPending ? (
                <CardBody>
                  <Skeleton className="h-20 w-full" />
                </CardBody>
              ) : latestCall ? (
                <CardBody className="grid gap-4 sm:grid-cols-4">
                  <Detail label="Status" value={<StatusBadge status={latestCall.status} />} />
                  <Detail label="Duration" value={formatDuration(latestCall.duration_seconds)} />
                  <Detail
                    label="Cost"
                    value={formatCurrency(latestCall.cost, latestCall.currency || "INR")}
                  />
                  <Detail label="Started" value={formatDateTime(latestCall.initiated_at)} />
                  {latestCall.blocked_reason ? (
                    <div className="sm:col-span-4">
                      <InlineAlert tone="warning" title="Blocked reason">
                        {latestCall.blocked_reason}
                      </InlineAlert>
                    </div>
                  ) : null}
                </CardBody>
              ) : (
                <EmptyState
                  icon={<PhoneCall className="h-5 w-5" aria-hidden />}
                  title="No calls yet"
                  description="Start a call once consent is recorded and the agent is active."
                  action={
                    <Button onClick={() => setCallOpen(true)}>Start a call</Button>
                  }
                />
              )}
            </Card>
          </div>

          <div className="space-y-4">
            <Card>
              <CardHeader title="Compliance snapshot" />
              <CardBody className="space-y-3">
                <ComplianceRow
                  label="Consent"
                  value={<StatusBadge status={consentStatus} />}
                  ok={consentStatus === "granted"}
                />
                <ComplianceRow
                  label="DND registry"
                  value={
                    profile ? (
                      <Badge tone={profile.dnd_active ? "danger" : "success"}>
                        {profile.dnd_active ? "blocked" : "clear"}
                      </Badge>
                    ) : (
                      <Badge tone="neutral">unknown</Badge>
                    )
                  }
                  ok={Boolean(profile && !profile.dnd_active)}
                />
                <ComplianceRow
                  label="Operational DNC"
                  value={
                    profile ? (
                      <Badge tone={profile.do_not_call ? "warning" : "success"}>
                        {profile.do_not_call ? "set" : "clear"}
                      </Badge>
                    ) : (
                      <Badge tone="neutral">unknown</Badge>
                    )
                  }
                  ok={Boolean(profile && !profile.do_not_call)}
                />
                <div className="flex flex-wrap gap-2 pt-1">
                  <Button size="sm" variant="outline" onClick={() => setConsentOpen(true)}>
                    Record consent
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setDndOpen(true)}
                    disabled={Boolean(profile?.dnd_active)}
                  >
                    Add to DND
                  </Button>
                </div>
              </CardBody>
            </Card>

            <Card>
              <CardHeader
                title="Open handoffs"
                description="Callbacks and warm transfers for this lead."
                actions={
                  <ButtonLink to="/handoffs" variant="ghost" size="sm">
                    Queue
                  </ButtonLink>
                }
              />
              {handoffsQuery.isPending ? (
                <CardBody>
                  <Skeleton className="h-16 w-full" />
                </CardBody>
              ) : handoffs.filter((handoff) =>
                  ["requested", "initiated"].includes(handoff.status),
                ).length === 0 ? (
                <EmptyState
                  title="No open handoffs"
                  description="Request a callback if the prospect wants a human follow-up."
                  className="py-8"
                />
              ) : (
                <ul className="divide-y divide-slate-100">
                  {handoffs
                    .filter((handoff) => ["requested", "initiated"].includes(handoff.status))
                    .map((handoff) => (
                      <li key={handoff.id} className="px-5 py-3">
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-sm font-medium capitalize text-slate-800">
                            {handoff.kind.replace(/_/g, " ")}
                          </p>
                          <StatusBadge status={handoff.status} />
                        </div>
                        <p className="mt-0.5 line-clamp-2 text-xs text-slate-500">
                          {handoff.reason || "No reason supplied"}
                        </p>
                        <p className="mt-1 text-[11px] text-slate-400">
                          {formatDateTime(handoff.created_at)}
                        </p>
                      </li>
                    ))}
                </ul>
              )}
            </Card>

            <Card>
              <CardHeader title="Follow-up tasks" />
              {followupsQuery.isPending ? (
                <CardBody>
                  <Skeleton className="h-16 w-full" />
                </CardBody>
              ) : followups.length === 0 ? (
                <EmptyState
                  title="No follow-up tasks"
                  description="Recovery workflows create tasks from call outcomes."
                  className="py-8"
                />
              ) : (
                <ul className="divide-y divide-slate-100">
                  {followups.slice(0, 5).map((task) => (
                    <li key={task.id} className="px-5 py-3">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-sm font-medium capitalize text-slate-800">
                          {task.channel.replace(/_/g, " ")}
                        </p>
                        <StatusBadge status={task.status} />
                      </div>
                      <p className="text-xs text-slate-500">
                        Due {formatDateTime(task.due_at)}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </div>
      ) : null}

      {tab === "compliance" ? (
        <div className="space-y-4">
          <Card>
            <CardHeader
              title="Consent evidence"
              description="Append-only consent history. Every live answer and call decision uses the current record."
              actions={
                <Button size="sm" onClick={() => setConsentOpen(true)}>
                  <ShieldCheck className="h-3.5 w-3.5" aria-hidden /> Record consent
                </Button>
              }
            />
            {consentQuery.isPending ? (
              <TableSkeleton rows={3} columns={5} />
            ) : consentQuery.error ? (
              <ErrorState
                error={consentQuery.error}
                title="Consent records unavailable"
                onRetry={() => void consentQuery.refetch()}
              />
            ) : consentRecords.length === 0 ? (
              <EmptyState
                icon={<ShieldAlert className="h-5 w-5" aria-hidden />}
                title="No consent evidence"
                description="The compliance gate blocks outbound calls until valid consent is recorded."
                action={
                  <Button size="sm" onClick={() => setConsentOpen(true)}>
                    Record consent
                  </Button>
                }
              />
            ) : (
              <TableWrap>
                <Table>
                  <THead>
                    <TR>
                      <TH>Status</TH>
                      <TH>Channel</TH>
                      <TH>Source</TH>
                      <TH>Granted</TH>
                      <TH>Expires</TH>
                      <TH>Revoked</TH>
                      <TH>Evidence</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {consentRecords.map((record) => (
                      <TR key={record.id}>
                        <TD>
                          <StatusBadge status={record.status} />
                        </TD>
                        <TD className="text-xs capitalize">{record.channel}</TD>
                        <TD className="text-xs">{record.source || "—"}</TD>
                        <TD className="whitespace-nowrap text-xs">
                          {formatDateTime(record.granted_at)}
                        </TD>
                        <TD className="whitespace-nowrap text-xs">
                          {formatDateTime(record.expires_at)}
                        </TD>
                        <TD className="whitespace-nowrap text-xs">
                          {formatDateTime(record.revoked_at)}
                        </TD>
                        <TD className="max-w-[260px]">
                          {Object.keys(record.evidence || {}).length ? (
                            <JsonBlock title="Evidence payload" value={record.evidence} />
                          ) : (
                            <span className="text-xs text-slate-400">None supplied</span>
                          )}
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </TableWrap>
            )}
          </Card>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader
                title="Do-not-disturb"
                description="DND entries are separate from operational do-not-call flags."
                actions={
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setDndOpen(true)}
                    disabled={Boolean(profile?.dnd_active)}
                  >
                    Add to DND
                  </Button>
                }
              />
              <CardBody>
                {!profile ? (
                  <InlineAlert tone="info" title="DND state unavailable">
                    The voice profile could not be loaded, so this page cannot say whether the
                    number is on the DND registry. The compliance gate still checks DND.
                  </InlineAlert>
                ) : profile.dnd_active ? (
                  <InlineAlert tone="danger" title="This number is on the DND registry">
                    The compliance gate blocks calls to this number. Remove the registry entry
                    from Compliance → DND only when the customer asks to be contacted again.
                  </InlineAlert>
                ) : (
                  <InlineAlert tone="success" title="Number is not on the DND registry">
                    Adding it here immediately blocks future outbound attempts.
                  </InlineAlert>
                )}
                <div className="mt-4">
                  <ButtonLink to="/compliance?tab=dnd" variant="outline" size="sm">
                    Open DND registry
                  </ButtonLink>
                </div>
              </CardBody>
            </Card>

            <Card>
              <CardHeader
                title="Compliance decisions"
                description="Every attempt and its recorded reason."
              />
              {decisionsQuery.isPending ? (
                <CardBody>
                  <Skeleton className="h-20 w-full" />
                </CardBody>
              ) : decisions.length === 0 ? (
                <EmptyState
                  title="No decisions yet"
                  description="Decisions appear when a call attempt is made."
                  className="py-8"
                />
              ) : (
                <ul className="divide-y divide-slate-100">
                  {decisions.slice(0, 8).map((decision) => (
                    <li key={decision.id} className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        <DecisionBadge decision={decision.decision} />
                        <span className="font-mono text-[11px] text-slate-500">
                          {decision.reason_code}
                        </span>
                        <span className="ml-auto text-[11px] text-slate-400">
                          {formatDateTime(decision.created_at)}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-slate-500">
                        {decision.reason || "No reason supplied"}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </div>
      ) : null}

      {tab === "calls" ? (
        <Card>
          <CardHeader
            title="Call history"
            description="Calls stored for this lead. Recordings and transcripts are only shown when the backend returns them."
            actions={
              <Button
                size="sm"
                onClick={() => setCallOpen(true)}
                disabled={!business.is_active}
              >
                <PhoneCall className="h-3.5 w-3.5" aria-hidden /> New call
              </Button>
            }
          />
          {callsQuery.isPending ? (
            <TableSkeleton rows={5} columns={6} />
          ) : callsQuery.error ? (
            <ErrorState
              error={callsQuery.error}
              title="Call history unavailable"
              onRetry={() => void callsQuery.refetch()}
            />
          ) : calls.length === 0 ? (
            <EmptyState
              icon={<PhoneCall className="h-5 w-5" aria-hidden />}
              title="No calls yet"
              description="The first call will appear here with its compliance decision."
            />
          ) : (
            <TableWrap>
              <Table>
                <THead>
                  <TR>
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
                        <Link
                          to={`/calls/${call.id}`}
                          className="font-mono text-xs text-slate-800 hover:text-accent-700"
                        >
                          {call.id}
                        </Link>
                      </TD>
                      <TD>
                        <StatusBadge status={call.status} />
                      </TD>
                      <TD className="text-xs uppercase">{call.language || "—"}</TD>
                      <TD className="text-xs">{formatDuration(call.duration_seconds)}</TD>
                      <TD className="text-xs">
                        {formatCurrency(call.cost, call.currency || "INR")}
                      </TD>
                      <TD className="whitespace-nowrap text-xs text-slate-500">
                        {formatDateTime(call.created_at)}
                      </TD>
                      <TD className="text-right">
                        <ButtonLink
                          to={`/calls/${call.id}`}
                          variant="ghost"
                          size="sm"
                        >
                          Open
                        </ButtonLink>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          )}
        </Card>
      ) : null}

      {tab === "activity" ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader
              title="Handoffs"
              description="Callbacks and warm transfers linked to this lead."
            />
            {handoffsQuery.isPending ? (
              <TableSkeleton rows={3} columns={3} />
            ) : handoffs.length === 0 ? (
              <EmptyState
                title="No handoffs"
                description="Request a callback if the prospect wants a human follow-up."
                className="py-8"
                action={
                  <Button size="sm" variant="outline" onClick={() => setCallbackOpen(true)}>
                    Request callback
                  </Button>
                }
              />
            ) : (
              <ul className="divide-y divide-slate-100">
                {handoffs.map((handoff) => (
                  <li key={handoff.id} className="px-5 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-sm font-medium capitalize text-slate-800">
                        {handoff.kind.replace(/_/g, " ")}
                      </p>
                      <StatusBadge status={handoff.status} />
                    </div>
                    <p className="mt-0.5 text-xs text-slate-500">
                      {handoff.reason || "No reason"}
                    </p>
                    {handoff.outcome ? (
                      <p className="mt-1 text-xs text-slate-600">
                        Outcome: {handoff.outcome}
                      </p>
                    ) : null}
                    <p className="mt-1 text-[11px] text-slate-400">
                      {formatDateTime(handoff.created_at)} ·{" "}
                      <Link to="/handoffs" className="hover:text-accent-700">
                        Open in queue
                      </Link>
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            <CardHeader
              title="Workflow follow-up tasks"
              description="Tasks generated by the recovery workflow."
            />
            {followupsQuery.isPending ? (
              <TableSkeleton rows={3} columns={3} />
            ) : followups.length === 0 ? (
              <EmptyState
                title="No follow-up tasks"
                description="Tasks are created by workflow rules after calls are analysed."
                className="py-8"
              />
            ) : (
              <ul className="divide-y divide-slate-100">
                {followups.map((task) => (
                  <li key={task.id} className="px-5 py-3">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-sm font-medium capitalize text-slate-800">
                        {task.channel.replace(/_/g, " ")}
                      </p>
                      <StatusBadge status={task.status} />
                    </div>
                    <p className="text-xs text-slate-500">
                      Due {formatDateTime(task.due_at)}
                      {task.priority ? ` · ${task.priority}` : ""}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      {task.reason || "No reason supplied"}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      ) : null}

      <StartCallDialog
        open={callOpen}
        onClose={() => setCallOpen(false)}
        defaultLeadId={leadId}
      />

      <Dialog
        open={consentOpen}
        onClose={() => {
          if (!consentMutation.isPending) {
            setConsentOpen(false);
            consentForm.reset();
          }
        }}
        title="Record consent evidence"
        description="Consent is evidence, not a boolean. Record where it came from so a later compliance decision can be audited."
        size="lg"
        footer={
          <>
            <Button
              variant="outline"
              onClick={() => setConsentOpen(false)}
              disabled={consentMutation.isPending}
            >
              Cancel
            </Button>
            <Button
              onClick={consentForm.handleSubmit((values) => consentMutation.mutate(values))}
              loading={consentMutation.isPending}
            >
              Save consent
            </Button>
          </>
        }
      >
        <form className="space-y-4" noValidate>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label="Status"
              htmlFor="consent-status"
              error={consentForm.formState.errors.status?.message}
              required
            >
              <Select id="consent-status" {...consentForm.register("status")}>
                {CONSENT_STATUSES.map((status) => (
                  <option key={status} value={status}>
                    {titleCase(status)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field
              label="Channel"
              htmlFor="consent-channel"
              error={consentForm.formState.errors.channel?.message}
              required
            >
              <Select id="consent-channel" {...consentForm.register("channel")}>
                {CONSENT_CHANNELS.map((channel) => (
                  <option key={channel} value={channel}>
                    {titleCase(channel)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field
              label="Source"
              htmlFor="consent-source"
              hint="e.g. Website form, inbound call, partner CRM."
              error={consentForm.formState.errors.source?.message}
            >
              <Input id="consent-source" {...consentForm.register("source")} />
            </Field>
            <Field
              label="Granted at"
              htmlFor="consent-granted"
              error={consentForm.formState.errors.granted_at?.message}
            >
              <Input
                id="consent-granted"
                type="datetime-local"
                {...consentForm.register("granted_at")}
              />
            </Field>
            <Field
              label="Expires at"
              htmlFor="consent-expires"
              hint="Leave blank when consent has no expiry."
              error={consentForm.formState.errors.expires_at?.message}
            >
              <Input
                id="consent-expires"
                type="datetime-local"
                {...consentForm.register("expires_at")}
              />
            </Field>
            <div className="sm:col-span-2">
              <Field
                label="Evidence note"
                htmlFor="consent-note"
                hint="Stored in the evidence payload. Do not paste secrets."
                error={consentForm.formState.errors.note?.message}
              >
                <Textarea
                  id="consent-note"
                  rows={3}
                  placeholder="Customer checked the consent box on the pricing form on 12 Oct."
                  {...consentForm.register("note")}
                />
              </Field>
            </div>
          </div>
          <InlineAlert tone="info" title="Backend is authoritative">
            The compliance gate evaluates this record on the next call attempt and audits
            the result.
          </InlineAlert>
          {consentMutation.error ? (
            <InlineAlert tone="danger" title="Consent was not saved">
              {consentMutation.error instanceof Error
                ? consentMutation.error.message
                : "The backend rejected the consent record."}
            </InlineAlert>
          ) : null}
        </form>
      </Dialog>

      <Dialog
        open={profileOpen}
        onClose={() => setProfileOpen(false)}
        title="Edit voice profile"
        description="These fields affect the voice workflow, not the canonical lead record."
        size="lg"
        footer={
          <>
            <Button variant="outline" onClick={() => setProfileOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => profileMutation.mutate()}
              loading={profileMutation.isPending}
            >
              Save profile
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Preferred language" htmlFor="profile-language">
              <Select
                id="profile-language"
                value={profileForm.language[0]}
                onChange={(event) => profileForm.language[1](event.target.value)}
              >
                <option value="">Use agent default</option>
                {(business.supported_languages || ["en"]).map((code) => (
                  <option key={code} value={code}>
                    {LANGUAGE_NAMES[code] || code}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Timezone" htmlFor="profile-timezone">
              <Select
                id="profile-timezone"
                value={profileForm.timezone[0]}
                onChange={(event) => profileForm.timezone[1](event.target.value)}
              >
                <option value="">Use business default</option>
                {TIMEZONES.map((timezone) => (
                  <option key={timezone} value={timezone}>
                    {timezone}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <Field label="Internal notes" htmlFor="profile-notes">
            <Textarea
              id="profile-notes"
              rows={4}
              placeholder="Context an operator should know before calling."
              value={profileForm.notes[0]}
              onChange={(event) => profileForm.notes[1](event.target.value)}
            />
          </Field>
          <label className="flex items-start gap-2 rounded-lg border border-slate-200 p-3 text-sm">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 rounded border-slate-300 text-accent-600"
              checked={profileForm.doNotCall[0]}
              onChange={(event) => profileForm.doNotCall[1](event.target.checked)}
            />
            <span>
              <span className="block font-medium text-slate-800">Operational do not call</span>
              <span className="mt-0.5 block text-xs text-slate-500">
                Blocks calls through the operational flag. Use the DND registry for a
                customer-requested global suppression.
              </span>
            </span>
          </label>
          {profileMutation.error ? (
            <InlineAlert tone="danger" title="Profile was not saved">
              {profileMutation.error instanceof Error
                ? profileMutation.error.message
                : "The backend rejected the profile update."}
            </InlineAlert>
          ) : null}
        </div>
      </Dialog>

      <Dialog
        open={dndOpen}
        onClose={() => setDndOpen(false)}
        title="Add this number to the DND registry?"
        description="The compliance gate will block every outbound call to this number until the entry is deactivated."
        size="sm"
        footer={
          <>
            <Button variant="outline" onClick={() => setDndOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={dndMutation.isPending}
              onClick={() => dndMutation.mutate("Added from lead dashboard")}
            >
              <ShieldAlert className="h-4 w-4" aria-hidden /> Add to DND
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-slate-600">
          <p>
            Number: <span className="font-mono text-slate-900">{maskPhone(lead.phone)}</span>
          </p>
          <InlineAlert tone="warning" title="This is a compliance action">
            Adding an entry is audited. Do not remove it unless the customer explicitly asks
            to be contacted again.
          </InlineAlert>
        </div>
      </Dialog>

      <CallbackDialog
        open={callbackOpen}
        onClose={() => setCallbackOpen(false)}
        loading={callbackMutation.isPending}
        error={
          callbackMutation.error instanceof Error ? callbackMutation.error.message : null
        }
        onSubmit={(values) => callbackMutation.mutate(values)}
        defaultTarget={business.handoff_target_number || ""}
      />
    </div>
  );
}

function CallbackDialog({
  open,
  onClose,
  onSubmit,
  loading,
  error,
  defaultTarget,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (values: { reason: string; briefing: string; target: string }) => void;
  loading: boolean;
  error: string | null;
  defaultTarget: string;
}) {
  const [reason, setReason] = useState("Prospect requested a callback");
  const [briefing, setBriefing] = useState("");
  const [target, setTarget] = useState(defaultTarget);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Request a callback"
      description="Creates a human handoff in the queue. It does not dial anyone."
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={loading}>
            Cancel
          </Button>
          <Button
            onClick={() => onSubmit({ reason, briefing, target })}
            loading={loading}
            disabled={!reason.trim()}
          >
            <CalendarClock className="h-4 w-4" aria-hidden /> Queue callback
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Reason" htmlFor="callback-reason" required>
          <Input
            id="callback-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </Field>
        <Field
          label="Briefing"
          htmlFor="callback-briefing"
          hint="Context the human agent needs. Never include secrets."
        >
          <Textarea
            id="callback-briefing"
            rows={5}
            placeholder="Interested in the enterprise plan. Wants a call after 4pm."
            value={briefing}
            onChange={(event) => setBriefing(event.target.value)}
          />
        </Field>
        <Field
          label="Target number"
          htmlFor="callback-target"
          hint="Leave blank to use the business handoff target."
        >
          <Input
            id="callback-target"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
            placeholder="+91…"
          />
        </Field>
        {error ? (
          <InlineAlert tone="danger" title="Callback was not queued">
            {error}
          </InlineAlert>
        ) : null}
      </div>
    </Dialog>
  );
}

function Detail({
  label,
  value,
  icon: Icon,
  mono,
}: {
  label: string;
  value: ReactNode;
  icon?: ComponentType<{ className?: string }>;
  mono?: boolean;
}) {
  return (
    <div>
      <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-slate-500">
        {Icon ? <Icon className="h-3.5 w-3.5 text-slate-400" aria-hidden /> : null}
        {label}
      </p>
      <div
        className={
          mono
            ? "mt-1 break-all font-mono text-xs text-slate-800"
            : "mt-1 text-sm text-slate-800"
        }
      >
        {value}
      </div>
    </div>
  );
}

function ComplianceRow({
  label,
  value,
  ok,
}: {
  label: string;
  value: ReactNode;
  ok: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="flex items-center gap-1.5 text-sm text-slate-600">
        {ok ? (
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" aria-hidden />
        ) : (
          <AlertTriangle className="h-3.5 w-3.5 text-amber-500" aria-hidden />
        )}
        {label}
      </span>
      {value}
    </div>
  );
}
