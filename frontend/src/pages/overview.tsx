import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  CalendarClock,
  CheckCircle2,
  ClipboardList,
  Clock,
  PhoneCall,
  PhoneOutgoing,
  ShieldAlert,
  Sparkles,
  TrendingUp,
  Users,
} from "lucide-react";

import { useBusiness } from "@/app/auth";
import { HorizontalBars, OutcomePieChart, VolumeBarChart } from "@/components/domain/charts";
import { LeadName } from "@/components/domain/lead-name";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { ButtonLink } from "@/components/ui/button-link";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { StatCard } from "@/components/ui/stat-card";
import { EmptyState, ErrorState, InlineAlert, Skeleton } from "@/components/ui/states";
import {
  Table,
  TableWrap,
  TBody,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui/table";
import {
  formatDateTime,
  formatDuration,
  formatNumber,
  formatPercent,
  titleCase,
} from "@/lib/utils";
import {
  getFunnel,
  getRecommendationAnalytics,
  getVoiceStats,
  listCalls,
  listComplianceDecisions,
  listHandoffs,
  listJobs,
} from "@/services/callora";

const RANGES = [
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

function dayKey(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toISOString().slice(0, 10);
}

export default function OverviewPage() {
  const business = useBusiness();
  const [days, setDays] = useState(30);

  const statsQuery = useQuery({
    queryKey: ["voice-stats"],
    queryFn: getVoiceStats,
    refetchInterval: 30_000,
  });

  const callsQuery = useQuery({
    queryKey: ["overview-calls"],
    queryFn: () => listCalls({ limit: 200 }),
    refetchInterval: 30_000,
  });

  const funnelQuery = useQuery({
    queryKey: ["funnel", days],
    queryFn: () => getFunnel({ days }),
  });

  const handoffsQuery = useQuery({
    queryKey: ["overview-handoffs"],
    queryFn: () => listHandoffs({ status: "requested", limit: 5 }),
  });

  const jobsQuery = useQuery({
    queryKey: ["overview-failed-jobs"],
    queryFn: () => listJobs({ status: "failed", limit: 5 }),
  });

  const decisionsQuery = useQuery({
    queryKey: ["overview-decisions"],
    queryFn: () => listComplianceDecisions({ limit: 5 }),
  });

  const recommendationsQuery = useQuery({
    queryKey: ["recommendation-analytics"],
    queryFn: getRecommendationAnalytics,
    retry: false,
  });

  const calls = callsQuery.data?.items ?? [];
  const stats = statsQuery.data;
  const funnel = funnelQuery.data;

  const filteredCalls = useMemo(() => {
    const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
    return calls.filter((call) => {
      if (!call.created_at) return false;
      return new Date(call.created_at).getTime() >= cutoff;
    });
  }, [calls, days]);

  const volume = useMemo(() => {
    const buckets = new Map<string, number>();
    for (let offset = days - 1; offset >= 0; offset -= 1) {
      const date = new Date();
      date.setDate(date.getDate() - offset);
      buckets.set(dayKey(date.toISOString()), 0);
    }
    for (const call of filteredCalls) {
      const key = call.created_at ? dayKey(call.created_at) : "";
      if (buckets.has(key)) buckets.set(key, (buckets.get(key) || 0) + 1);
    }
    return Array.from(buckets.entries()).map(([key, value]) => ({
      label: new Date(`${key}T00:00:00`).toLocaleDateString("en-IN", {
        day: "2-digit",
        month: "short",
      }),
      value,
    }));
  }, [filteredCalls, days]);

  const outcomes = useMemo(() => {
    const counts = new Map<string, number>();
    for (const call of filteredCalls) {
      counts.set(call.status, (counts.get(call.status) || 0) + 1);
    }
    return Array.from(counts.entries())
      .map(([label, value]) => ({ label, value }))
      .sort((a, b) => b.value - a.value);
  }, [filteredCalls]);

  const funnelStages = useMemo(() => {
    if (!funnel) return [];
    return [
      { label: "calls", value: funnel.calls },
      { label: "connected", value: funnel.connected },
      { label: "completed", value: funnel.completed },
      { label: "qualified", value: funnel.qualified },
    ];
  }, [funnel]);

  const recentCalls = calls.slice(0, 6);
  const handoffs = handoffsQuery.data?.items ?? [];
  const failedJobs = jobsQuery.data?.items ?? [];
  const decisions = decisionsQuery.data?.items ?? [];

  const statsPending = statsQuery.isPending;
  const statsError = statsQuery.error;

  return (
    <div>
      <PageHeader
        title={`Good day, ${business.name}`}
        description="A live view of outbound conversations, qualification, human handoffs and compliance. Every number comes from the Callora backend."
        actions={
          <>
            <ButtonLink to="/calls?new=1" variant="outline">
              <PhoneOutgoing className="h-4 w-4" aria-hidden /> New call
            </ButtonLink>
            <ButtonLink to="/calls">
              View calls <ArrowRight className="h-4 w-4" aria-hidden />
            </ButtonLink>
          </>
        }
        meta={
          <>
            <Badge tone={business.outbound_calls_enabled ? "accent" : "warning"}>
              Outbound {business.outbound_calls_enabled ? "enabled" : "disabled"}
            </Badge>
            <Badge tone="neutral">{business.timezone}</Badge>
            <span className="text-xs text-slate-400">
              Refresh every 30 seconds · API-supplied data only
            </span>
          </>
        }
      />

      {statsError ? (
        <Card className="mb-5">
          <ErrorState
            error={statsError}
            title="Voice metrics are unavailable"
            onRetry={() => void statsQuery.refetch()}
          />
        </Card>
      ) : null}

      {statsQuery.isSuccess && stats && stats.calls_total === 0 ? (
        <div className="mb-5">
          <InlineAlert tone="info" title="No calls yet">
            The workspace is connected, but no outbound calls have been placed. Add leads,
            then start a call from Calls → New call. Activation and compliance checks are
            enforced by the backend.
          </InlineAlert>
        </div>
      ) : null}

      <section aria-label="Key metrics" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
        <StatCard
          label="Calls attempted"
          value={
            statsPending ? (
              <Skeleton className="h-7 w-16" />
            ) : (
              formatNumber(stats?.calls_total)
            )
          }
          hint={`${formatNumber(stats?.calls_today)} today · all time`}
          icon={PhoneCall}
          tone="accent"
        />
        <StatCard
          label="Answered"
          value={
            funnelQuery.isPending ? (
              <Skeleton className="h-7 w-16" />
            ) : funnel?.insufficient_data ? (
              "—"
            ) : (
              formatNumber(funnel?.connected)
            )
          }
          hint={`Connected · last ${days} days`}
          icon={CheckCircle2}
          tone="success"
        />
        <StatCard
          label="Qualified leads"
          value={
            funnelQuery.isPending ? (
              <Skeleton className="h-7 w-16" />
            ) : funnel?.insufficient_data ? (
              "—"
            ) : (
              formatNumber(funnel?.qualified)
            )
          }
          hint={`Last ${days} days · not converted customers`}
          icon={Users}
        />
        <StatCard
          label="Qualification rate"
          value={
            funnelQuery.isPending ? (
              <Skeleton className="h-7 w-16" />
            ) : (
              formatPercent(funnel?.qualification_rate_percent)
            )
          }
          hint="Qualified ÷ completed calls"
          icon={TrendingUp}
        />
        <StatCard
          label="Open handoffs"
          value={
            statsPending ? (
              <Skeleton className="h-7 w-16" />
            ) : (
              formatNumber(stats?.handoffs_open)
            )
          }
          hint="Awaiting a sales representative"
          icon={CalendarClock}
          tone={(stats?.handoffs_open || 0) > 0 ? "warning" : "default"}
        />
        <StatCard
          label="Compliance blocks"
          value={
            funnelQuery.isPending ? (
              <Skeleton className="h-7 w-16" />
            ) : (
              formatNumber(funnel?.blocked)
            )
          }
          hint={`Last ${days} days · ${formatNumber(stats?.blocked_today)} today`}
          icon={ShieldAlert}
          tone={(funnel?.blocked || 0) > 0 ? "danger" : "default"}
        />
      </section>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
          Trend window
        </span>
        <div className="flex rounded-lg border border-slate-200 bg-white p-0.5">
          {RANGES.map((range) => (
            <button
              key={range.days}
              type="button"
              onClick={() => setDays(range.days)}
              aria-pressed={days === range.days}
              className={
                days === range.days
                  ? "rounded-md bg-navy-900 px-3 py-1 text-xs font-medium text-white"
                  : "rounded-md px-3 py-1 text-xs font-medium text-slate-500 hover:text-slate-800"
              }
            >
              {range.label}
            </button>
          ))}
        </div>
        <span className="text-xs text-slate-400">
          Charts use the {calls.length} most recent calls returned by the API.
        </span>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader
            title="Call volume"
            description={`Daily attempts over the last ${days} days, from the recent call feed.`}
            actions={
              <ButtonLink to="/calls" variant="ghost" size="sm">
                Calls
              </ButtonLink>
            }
          />
          <CardBody className="pt-3">
            <VolumeBarChart data={volume} height={240} />
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Outcome distribution"
            description={`Status of ${filteredCalls.length} calls in this window.`}
          />
          <CardBody className="pt-3">
            <OutcomePieChart data={outcomes} height={240} />
          </CardBody>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader
            title="Qualification funnel"
            description={
              funnel?.insufficient_data
                ? `At least ${funnel.min_sample_per_window} calls are required before a qualification rate is reported.`
                : funnel?.note || "Calls, connected, completed and qualified in the window."
            }
            actions={
              <ButtonLink to="/analytics" variant="ghost" size="sm">
                Analytics
              </ButtonLink>
            }
          />
          <CardBody>
            {funnelQuery.isPending ? (
              <Skeleton className="h-32 w-full" />
            ) : funnelQuery.error ? (
              <ErrorState
                error={funnelQuery.error}
                title="Funnel analytics unavailable"
                onRetry={() => void funnelQuery.refetch()}
              />
            ) : funnelStages.length ? (
              <HorizontalBars data={funnelStages} />
            ) : (
              <EmptyState title="No funnel data" description="Place calls to populate the funnel." />
            )}
            <p className="mt-4 text-xs leading-relaxed text-slate-400">
              Definition: qualification rate = qualified ÷ completed calls. Qualified does not
              imply converted; revenue is only counted when the backend verifies it.
            </p>
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Attention required"
            description="Operational alerts derived from the API."
          />
          <CardBody className="space-y-3">
            <AlertRow
              tone={(stats?.jobs_failed || 0) > 0 ? "danger" : "success"}
              label="Failed background jobs"
              value={stats ? formatNumber(stats.jobs_failed) : "—"}
              to="/compliance?tab=jobs"
            />
            <AlertRow
              tone={(stats?.consent_missing || 0) > 0 ? "warning" : "success"}
              label="Leads without consent"
              value={stats ? formatNumber(stats.consent_missing) : "—"}
              to="/compliance?tab=overview"
            />
            <AlertRow
              tone={(stats?.blocked_today || 0) > 0 ? "warning" : "success"}
              label="Calls blocked today"
              value={stats ? formatNumber(stats.blocked_today) : "—"}
              to="/compliance?tab=decisions"
            />
            <AlertRow
              tone="info"
              label="DND registry entries"
              value={stats ? formatNumber(stats.dnd_entries) : "—"}
              to="/compliance?tab=dnd"
            />
            {failedJobs.length > 0 ? (
              <div className="rounded-lg border border-rose-100 bg-rose-50/60 p-3">
                <p className="text-xs font-semibold text-rose-700">Latest failed job</p>
                <p className="mt-1 truncate text-xs text-rose-600">
                  {failedJobs[0].type} · {failedJobs[0].last_error || "No error message"}
                </p>
              </div>
            ) : null}
          </CardBody>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader
            title="Recent calls"
            description="The latest records returned by the call API."
            actions={
              <ButtonLink to="/calls" variant="outline" size="sm">
                View all
              </ButtonLink>
            }
          />
          {callsQuery.isPending ? (
            <CardBody>
              <Skeleton className="h-40 w-full" />
            </CardBody>
          ) : callsQuery.error ? (
            <ErrorState
              error={callsQuery.error}
              onRetry={() => void callsQuery.refetch()}
            />
          ) : recentCalls.length === 0 ? (
            <EmptyState
              title="No calls yet"
              description="Start a compliant outbound call from the Calls page."
              action={
                <ButtonLink to="/calls?new=1">Start a call</ButtonLink>
              }
            />
          ) : (
            <TableWrap>
              <Table>
                <THead>
                  <TR>
                    <TH>Lead</TH>
                    <TH>Status</TH>
                    <TH>Language</TH>
                    <TH>Duration</TH>
                    <TH>Started</TH>
                  </TR>
                </THead>
                <TBody>
                  {recentCalls.map((call) => (
                    <TR key={call.id}>
                      <TD>
                        <Link
                          to={`/calls/${call.id}`}
                          className="font-medium text-slate-900 hover:text-accent-700"
                        >
                          <LeadName leadId={call.lead_id} />
                        </Link>
                        <p className="font-mono text-[11px] text-slate-400">{call.id}</p>
                      </TD>
                      <TD>
                        <StatusBadge status={call.status} />
                      </TD>
                      <TD className="text-xs uppercase text-slate-500">
                        {call.language || "—"}
                      </TD>
                      <TD>{formatDuration(call.duration_seconds)}</TD>
                      <TD className="whitespace-nowrap text-xs text-slate-500">
                        {formatDateTime(call.initiated_at || call.created_at)}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </TableWrap>
          )}
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader
              title="Open handoffs"
              description="Requested warm transfers and callbacks."
              actions={
                <ButtonLink to="/handoffs" variant="ghost" size="sm">
                  Queue
                </ButtonLink>
              }
            />
            {handoffsQuery.isPending ? (
              <CardBody>
                <Skeleton className="h-24 w-full" />
              </CardBody>
            ) : handoffs.length === 0 ? (
              <EmptyState
                title="Queue is clear"
                description="No handoff is waiting for a sales representative."
                className="py-8"
              />
            ) : (
              <ul className="divide-y divide-slate-100">
                {handoffs.map((handoff) => (
                  <li key={handoff.id} className="px-5 py-3">
                    <Link to="/handoffs" className="block hover:opacity-80">
                      <div className="flex items-center justify-between gap-2">
                        <p className="truncate text-sm font-medium text-slate-900">
                          {handoff.lead_name || <LeadName leadId={handoff.lead_id} />}
                        </p>
                        <StatusBadge status={handoff.status} />
                      </div>
                      <p className="mt-0.5 truncate text-xs text-slate-500">
                        {titleCase(handoff.kind)} · {handoff.reason || "No reason supplied"}
                      </p>
                      <p className="mt-0.5 text-[11px] text-slate-400">
                        {formatDateTime(handoff.created_at)}
                      </p>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            <CardHeader
              title="AI recommendations"
              description="Aggregated recommendation outcomes from analysed calls."
              actions={<Sparkles className="h-4 w-4 text-accent-500" aria-hidden />}
            />
            <CardBody>
              {recommendationsQuery.isPending ? (
                <Skeleton className="h-16 w-full" />
              ) : recommendationsQuery.error ? (
                <p className="text-xs leading-relaxed text-slate-500">
                  Recommendation analytics are not available on this deployment. The page
                  stays honest instead of substituting sample data.
                </p>
              ) : (
                <div className="space-y-2 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="text-slate-500">Total recommendations</span>
                    <span className="font-semibold tabular-nums">
                      {formatNumber(recommendationsQuery.data.total_recommendations)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-slate-500">New</span>
                    <Badge tone="accent">
                      {formatNumber(recommendationsQuery.data.new_recommendations)}
                    </Badge>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-slate-500">P1 items</span>
                    <span className="font-semibold tabular-nums">
                      {formatNumber(recommendationsQuery.data.p1_count)}
                    </span>
                  </div>
                </div>
              )}
            </CardBody>
          </Card>
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader
            title="Recent compliance decisions"
            description="Why the backend allowed or blocked each attempt."
            actions={
              <ButtonLink to="/compliance?tab=decisions" variant="ghost" size="sm">
                Audit
              </ButtonLink>
            }
          />
          {decisionsQuery.isPending ? (
            <CardBody>
              <Skeleton className="h-24 w-full" />
            </CardBody>
          ) : decisions.length === 0 ? (
            <EmptyState
              title="No decisions recorded"
              description="Compliance decisions are written when a call is requested."
              className="py-8"
            />
          ) : (
            <ul className="divide-y divide-slate-100">
              {decisions.map((decision) => (
                <li key={decision.id} className="flex items-start gap-3 px-5 py-3">
                  <Clock className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" aria-hidden />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <StatusBadge status={decision.decision} />
                      <span className="truncate font-mono text-[11px] text-slate-500">
                        {decision.reason_code}
                      </span>
                    </div>
                    <p className="mt-0.5 truncate text-xs text-slate-500">
                      {decision.reason || "No reason supplied"}
                    </p>
                  </div>
                  <span className="shrink-0 text-[11px] text-slate-400">
                    {formatDateTime(decision.created_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader
            title="Campaign performance"
            description="Campaigns are not exposed by the current backend contract."
          />
          <CardBody>
            <InlineAlert tone="warning" title="Backend dependency">
              Campaigns, per-campaign progress and launch controls require a campaign API
              that is not present in this deployment. Rather than fabricate performance,
              Callora links campaign work to the available call and lead records.
            </InlineAlert>
            <ButtonLink to="/campaigns" variant="outline" size="sm" className="mt-4">
              See campaign requirements <ArrowRight className="h-3.5 w-3.5" />
            </ButtonLink>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function AlertRow({
  label,
  value,
  tone,
  to,
}: {
  label: string;
  value: string;
  tone: "success" | "warning" | "danger" | "info";
  to: string;
}) {
  const dot = {
    success: "bg-emerald-500",
    warning: "bg-amber-500",
    danger: "bg-rose-500",
    info: "bg-sky-500",
  }[tone];
  return (
    <Link
      to={to}
      className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 px-3 py-2 transition hover:border-slate-300 hover:bg-slate-50"
    >
      <span className="flex min-w-0 items-center gap-2 text-sm text-slate-600">
        <span className={`h-2 w-2 shrink-0 rounded-full ${dot}`} />
        <span className="truncate">{label}</span>
      </span>
      <span className="flex items-center gap-1 text-sm font-semibold tabular-nums text-slate-900">
        {value}
        <ClipboardList className="h-3 w-3 text-slate-300" aria-hidden />
      </span>
    </Link>
  );
}
