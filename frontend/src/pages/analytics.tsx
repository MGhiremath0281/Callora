import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  CalendarRange,
  CircleDollarSign,
  PhoneCall,
  ShieldAlert,
  Sparkles,
  Target,
  TrendingUp,
} from "lucide-react";

import {
  HorizontalBars,
  OutcomePieChart,
  VolumeBarChart,
  type SeriesPoint,
} from "@/components/domain/charts";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Select } from "@/components/ui/form";
import { PageHeader } from "@/components/ui/page-header";
import { StatCard } from "@/components/ui/stat-card";
import {
  EmptyState,
  ErrorState,
  InlineAlert,
  Skeleton,
} from "@/components/ui/states";
import { useBusiness } from "@/app/auth";
import {
  formatCurrency,
  formatDateTime,
  formatNumber,
  formatPercent,
  titleCase,
} from "@/lib/utils";
import {
  getFunnel,
  getLeakage,
  getRecommendationAnalytics,
  getRoi,
  getVoiceStats,
  listCalls,
  listWorkflowRuns,
} from "@/services/callora";

const WINDOWS = [
  { value: 7, label: "Last 7 days" },
  { value: 30, label: "Last 30 days" },
  { value: 90, label: "Last 90 days" },
];

export default function AnalyticsPage() {
  const business = useBusiness();
  const [windowDays, setWindowDays] = useState(30);

  const statsQuery = useQuery({
    queryKey: ["voice-stats"],
    queryFn: getVoiceStats,
    refetchInterval: 60_000,
  });

  const funnelQuery = useQuery({
    queryKey: ["funnel", windowDays],
    queryFn: () => getFunnel({ window_days: windowDays }),
  });

  const leakageQuery = useQuery({
    queryKey: ["leakage", windowDays],
    queryFn: () => getLeakage({ window_days: windowDays }),
  });

  const roiQuery = useQuery({
    queryKey: ["roi", windowDays],
    queryFn: () => getRoi({ window_days: windowDays }),
  });

  const recommendationsQuery = useQuery({
    queryKey: ["recommendation-analytics"],
    queryFn: getRecommendationAnalytics,
  });

  const callsQuery = useQuery({
    queryKey: ["analytics-calls"],
    queryFn: () => listCalls({ limit: 200 }),
    staleTime: 30_000,
  });

  const runsQuery = useQuery({
    queryKey: ["analytics-runs"],
    queryFn: () => listWorkflowRuns({ limit: 100 }),
    staleTime: 60_000,
  });

  const stats = statsQuery.data;
  const funnel = funnelQuery.data;
  const leakage = leakageQuery.data;
  const roi = roiQuery.data;
  const recommendations = recommendationsQuery.data;
  const calls = callsQuery.data?.items ?? [];
  const runs = runsQuery.data?.items ?? [];

  const volumeSeries = useMemo<SeriesPoint[]>(() => {
    const days: { key: string; label: string }[] = [];
    const now = new Date();
    for (let index = windowDays - 1; index >= 0; index -= 1) {
      const date = new Date(now);
      date.setDate(now.getDate() - index);
      days.push({
        key: date.toISOString().slice(0, 10),
        label: new Intl.DateTimeFormat("en-IN", {
          day: "2-digit",
          month: "short",
        }).format(date),
      });
    }
    const counts = new Map<string, number>();
    for (const call of calls) {
      if (!call.created_at) continue;
      const key = new Date(call.created_at).toISOString().slice(0, 10);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    return days.map((day) => ({
      label: day.label,
      value: counts.get(day.key) || 0,
    }));
  }, [calls, windowDays]);

  const statusDistribution = useMemo<SeriesPoint[]>(() => {
    const counts = new Map<string, number>();
    for (const call of calls) {
      counts.set(call.status, (counts.get(call.status) || 0) + 1);
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([label, value]) => ({ label: titleCase(label), value }));
  }, [calls]);

  const runStatuses = useMemo<SeriesPoint[]>(() => {
    const counts = new Map<string, number>();
    for (const run of runs) {
      counts.set(run.status, (counts.get(run.status) || 0) + 1);
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([label, value]) => ({ label, value }));
  }, [runs]);

  const leakageFlags = (leakage?.flags ?? []).map((flag) => ({
    label: flag.flag,
    value: flag.count,
  }));

  const callsBreakdown = useMemo(
    () =>
      Object.entries(stats?.calls_by_status ?? {})
        .sort((a, b) => b[1] - a[1])
        .map(([label, value]) => ({ label, value })),
    [stats],
  );

  return (
    <div>
      <PageHeader
        title="Analytics"
        description="Outcome analytics computed by the backend from stored calls, workflow runs and verified revenue. Where the sample is too small, Callora says so instead of showing a misleading rate."
        actions={
          <div className="flex items-center gap-2">
            <CalendarRange className="h-4 w-4 text-slate-400" aria-hidden />
            <Select
              aria-label="Analytics window"
              value={windowDays}
              onChange={(event) => setWindowDays(Number(event.target.value))}
              className="h-9 w-40"
            >
              {WINDOWS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </Select>
          </div>
        }
        meta={
          <>
            <Badge tone="neutral">{business.name}</Badge>
            <Badge tone="accent">{windowDays} day window</Badge>
            <span className="text-xs text-slate-400">
              Rates use backend sample thresholds; below them the API returns null.
            </span>
          </>
        }
      />

      {statsQuery.error || funnelQuery.error || leakageQuery.error || roiQuery.error ? (
        <div className="mb-4">
          <InlineAlert tone="warning" title="Some analytics sections are unavailable">
            {[
              statsQuery.error ? "operations stats" : null,
              funnelQuery.error ? "funnel" : null,
              leakageQuery.error ? "leakage" : null,
              roiQuery.error ? "ROI" : null,
            ]
              .filter(Boolean)
              .join(", ")}{" "}
            could not be loaded. Other sections still show real API data.
          </InlineAlert>
        </div>
      ) : null}

      <section aria-label="Headline metrics" className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {statsQuery.isPending || funnelQuery.isPending ? (
          Array.from({ length: 4 }).map((_, index) => (
            <Card key={index} className="p-4">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="mt-3 h-7 w-24" />
              <Skeleton className="mt-3 h-3 w-32" />
            </Card>
          ))
        ) : (
          <>
            <StatCard
              label="Calls in window"
              value={formatNumber(funnel?.calls ?? 0)}
              hint={`${formatNumber(stats?.calls_today ?? 0)} today · ${formatNumber(stats?.calls_total ?? 0)} all time`}
              icon={PhoneCall}
              tone="accent"
            />
            <StatCard
              label="Connected"
              value={
                funnel?.connect_rate_percent === null ||
                funnel?.connect_rate_percent === undefined
                  ? "Insufficient"
                  : formatPercent(funnel.connect_rate_percent)
              }
              hint={`${formatNumber(funnel?.connected ?? 0)} connected of ${formatNumber(funnel?.calls ?? 0)} calls`}
              icon={TrendingUp}
              tone="success"
            />
            <StatCard
              label="Qualified"
              value={
                funnel?.qualification_rate_percent === null ||
                funnel?.qualification_rate_percent === undefined
                  ? "Insufficient"
                  : formatPercent(funnel.qualification_rate_percent)
              }
              hint={`${formatNumber(funnel?.qualified ?? 0)} qualified · ${formatNumber(funnel?.refused ?? 0)} refused`}
              icon={Target}
            />
            <StatCard
              label="Blocked by compliance"
              value={formatNumber(funnel?.blocked ?? 0)}
              hint={`${formatNumber(stats?.blocked_today ?? 0)} blocked today`}
              icon={ShieldAlert}
              tone={funnel?.blocked ? "warning" : "default"}
            />
          </>
        )}
      </section>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader
            title="Conversion funnel"
            description="Counts come from stored calls and post-call outcomes in the selected window."
            actions={
              funnel ? (
                <Badge tone={funnel.insufficient_data ? "warning" : "success"}>
                  {funnel.insufficient_data
                    ? `Minimum sample: ${funnel.min_sample_per_window}`
                    : "Sufficient sample"}
                </Badge>
              ) : null
            }
          />
          <CardBody>
            {funnelQuery.isPending ? (
              <Skeleton className="h-48 w-full" />
            ) : funnelQuery.error || !funnel ? (
              <ErrorState
                error={funnelQuery.error}
                title="Funnel unavailable"
                onRetry={() => void funnelQuery.refetch()}
              />
            ) : (
              <div className="grid gap-6 lg:grid-cols-2">
                <HorizontalBars
                  data={[
                    { label: "Calls", value: funnel.calls },
                    { label: "Connected", value: funnel.connected },
                    { label: "Completed", value: funnel.completed },
                    { label: "Outcomes", value: funnel.outcomes },
                    { label: "Qualified", value: funnel.qualified },
                    { label: "Refused", value: funnel.refused },
                    { label: "Blocked", value: funnel.blocked },
                  ]}
                />
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <MiniMetric
                      label="Connect rate"
                      value={formatPercent(funnel.connect_rate_percent)}
                    />
                    <MiniMetric
                      label="Qualification rate"
                      value={formatPercent(funnel.qualification_rate_percent)}
                    />
                    <MiniMetric label="Calls" value={formatNumber(funnel.calls)} />
                    <MiniMetric
                      label="Blocked"
                      value={formatNumber(funnel.blocked)}
                    />
                  </div>
                  {funnel.insufficient_data ? (
                    <InlineAlert tone="warning" title="Small sample">
                      {funnel.note ||
                        `Rates are withheld below ${funnel.min_sample_per_window} calls. Counts are still shown.`}
                    </InlineAlert>
                  ) : (
                    <p className="text-xs leading-relaxed text-slate-500">
                      {funnel.note ||
                        "Rates are calculated by the backend only when the sample threshold is met."}
                    </p>
                  )}
                </div>
              </div>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Call status mix"
            description="All stored calls returned by the API, not just this window."
          />
          <CardBody>
            {callsQuery.isPending ? (
              <Skeleton className="h-48 w-full" />
            ) : callsQuery.error ? (
              <ErrorState
                error={callsQuery.error}
                title="Call mix unavailable"
                onRetry={() => void callsQuery.refetch()}
              />
            ) : (
              <OutcomePieChart data={statusDistribution} />
            )}
          </CardBody>
        </Card>

        <Card className="xl:col-span-2">
          <CardHeader
            title="Call volume"
            description={`Daily calls for the last ${windowDays} days, computed from up to 200 recent call records.`}
          />
          <CardBody>
            {callsQuery.isPending ? (
              <Skeleton className="h-56 w-full" />
            ) : callsQuery.error ? (
              <ErrorState
                error={callsQuery.error}
                title="Volume unavailable"
                onRetry={() => void callsQuery.refetch()}
              />
            ) : (
              <>
                <VolumeBarChart data={volumeSeries} height={240} />
                <p className="mt-2 text-[11px] text-slate-400">
                  The call list endpoint does not expose a date-range aggregate, so this chart
                  is a client-side count over the most recent 200 records.
                </p>
              </>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Recovery automation"
            description="Workflow runs recorded by the recovery module."
          />
          <CardBody>
            {runsQuery.isPending ? (
              <Skeleton className="h-40 w-full" />
            ) : runsQuery.error ? (
              <ErrorState
                error={runsQuery.error}
                title="Workflow analytics unavailable"
                onRetry={() => void runsQuery.refetch()}
              />
            ) : runs.length === 0 ? (
              <EmptyState
                icon={<Sparkles className="h-5 w-5" aria-hidden />}
                title="No workflow runs"
                description="Recovery workflows have not run yet. They are triggered by call outcomes and rules."
                className="py-8"
              />
            ) : (
              <>
                <div className="grid grid-cols-3 gap-2">
                  <MiniMetric label="Runs" value={formatNumber(runs.length)} />
                  <MiniMetric
                    label="Completed"
                    value={formatNumber(
                      runs.filter((run) => run.status === "completed").length,
                    )}
                  />
                  <MiniMetric
                    label="Needs attention"
                    value={formatNumber(
                      runs.filter((run) =>
                        ["failed", "cancelled", "needs_approval"].includes(run.status),
                      ).length,
                    )}
                  />
                </div>
                <div className="mt-4">
                  <HorizontalBars data={runStatuses} color="#38bdf8" />
                </div>
                <p className="mt-3 text-[11px] text-slate-400">
                  Based on the latest {runs.length} workflow runs returned by the API.
                </p>
              </>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Leakage signals"
            description="Calls flagged by the recovery analysis in this window."
            actions={
              leakage ? (
                <Badge tone={leakage.insufficient_data ? "warning" : "accent"}>
                  {leakage.flagged_calls}/{leakage.analysed_calls} analysed
                </Badge>
              ) : null
            }
          />
          <CardBody>
            {leakageQuery.isPending ? (
              <Skeleton className="h-40 w-full" />
            ) : leakageQuery.error || !leakage ? (
              <ErrorState
                error={leakageQuery.error}
                title="Leakage report unavailable"
                onRetry={() => void leakageQuery.refetch()}
              />
            ) : leakage.insufficient_data ? (
              <InlineAlert tone="warning" title="Not enough analysed calls">
                {leakage.note ||
                  `Leakage percentage is withheld below ${leakage.min_calls_for_leakage} analysed calls.`}
              </InlineAlert>
            ) : leakageFlags.length === 0 ? (
              <EmptyState
                title="No leakage signals"
                description="No calls in this window were flagged by the current rules."
                className="py-8"
              />
            ) : (
              <>
                <div className="mb-3 flex items-baseline gap-2">
                  <span className="text-2xl font-semibold tabular-nums text-slate-900">
                    {formatPercent(leakage.leakage_percent)}
                  </span>
                  <span className="text-xs text-slate-500">of analysed calls flagged</span>
                </div>
                <HorizontalBars data={leakageFlags} color="#fbbf24" />
                {leakage.note ? (
                  <p className="mt-3 text-xs text-slate-500">{leakage.note}</p>
                ) : null}
              </>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Revenue and ROI"
            description="Only verified revenue counts toward ROI; unverified value is shown as pipeline."
            actions={<CircleDollarSign className="h-4 w-4 text-slate-400" aria-hidden />}
          />
          <CardBody>
            {roiQuery.isPending ? (
              <Skeleton className="h-40 w-full" />
            ) : roiQuery.error || !roi ? (
              <ErrorState
                error={roiQuery.error}
                title="ROI unavailable"
                onRetry={() => void roiQuery.refetch()}
              />
            ) : (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <MiniMetric
                    label="Verified revenue"
                    value={formatCurrency(roi.verified_revenue)}
                  />
                  <MiniMetric
                    label="Unverified pipeline"
                    value={formatCurrency(roi.unverified_pipeline)}
                  />
                  <MiniMetric label="Total costs" value={formatCurrency(roi.total_costs)} />
                  <MiniMetric
                    label="ROI"
                    value={
                      roi.roi === null ? "Unavailable" : `${roi.roi.toFixed(2)}×`
                    }
                  />
                </div>
                {roi.verified_revenue_rows === 0 ? (
                  <InlineAlert tone="warning" title="No verified revenue yet">
                    {roi.note ||
                      "ROI is withheld until revenue is verified. Unverified pipeline is reported separately and never counted as revenue."}
                  </InlineAlert>
                ) : (
                  <p className="text-xs leading-relaxed text-slate-500">
                    {roi.note ||
                      `${roi.verified_revenue_rows} verified revenue row${roi.verified_revenue_rows === 1 ? "" : "s"} across ${roi.cost_rows} cost row${roi.cost_rows === 1 ? "" : "s"}.`}
                  </p>
                )}
              </div>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Recommendation impact"
            description="The recommendation engine's recorded adoption metrics."
            actions={
              recommendations ? (
                <Badge tone="accent">
                  {formatNumber(recommendations.total_recommendations)} total
                </Badge>
              ) : null
            }
          />
          <CardBody>
            {recommendationsQuery.isPending ? (
              <Skeleton className="h-40 w-full" />
            ) : recommendationsQuery.error || !recommendations ? (
              <ErrorState
                error={recommendationsQuery.error}
                title="Recommendations unavailable"
                onRetry={() => void recommendationsQuery.refetch()}
              />
            ) : recommendations.total_recommendations === 0 ? (
              <EmptyState
                icon={<Sparkles className="h-5 w-5" aria-hidden />}
                title="No recommendations yet"
                description="The engine creates recommendations after enough call outcomes exist."
                className="py-8"
              />
            ) : (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <MiniMetric label="New" value={formatNumber(recommendations.new_recommendations)} />
                  <MiniMetric label="P1" value={formatNumber(recommendations.p1_count)} />
                  <MiniMetric label="P2" value={formatNumber(recommendations.p2_count)} />
                  <MiniMetric label="P3" value={formatNumber(recommendations.p3_count)} />
                  {recommendations.implemented_count !== undefined ? (
                    <MiniMetric
                      label="Implemented"
                      value={formatNumber(recommendations.implemented_count)}
                    />
                  ) : null}
                  {recommendations.evaluated_count !== undefined ? (
                    <MiniMetric
                      label="Evaluated"
                      value={formatNumber(recommendations.evaluated_count)}
                    />
                  ) : null}
                </div>
                <p className="mt-3 text-[11px] text-slate-400">
                  Fields not returned by the API are omitted rather than shown as zero.
                </p>
              </>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Operations snapshot"
            description="All-time counters from the operations endpoint."
            actions={
              stats ? (
                <span className="text-[11px] text-slate-400">Live</span>
              ) : null
            }
          />
          <CardBody>
            {statsQuery.isPending ? (
              <Skeleton className="h-40 w-full" />
            ) : statsQuery.error || !stats ? (
              <ErrorState
                error={statsQuery.error}
                title="Operations stats unavailable"
                onRetry={() => void statsQuery.refetch()}
              />
            ) : (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <MiniMetric label="Calls today" value={formatNumber(stats.calls_today)} />
                  <MiniMetric label="Blocked today" value={formatNumber(stats.blocked_today)} />
                  <MiniMetric label="Open handoffs" value={formatNumber(stats.handoffs_open)} />
                  <MiniMetric label="DND entries" value={formatNumber(stats.dnd_entries)} />
                  <MiniMetric label="Jobs pending" value={formatNumber(stats.jobs_pending)} />
                  <MiniMetric
                    label="Jobs failed"
                    value={formatNumber(stats.jobs_failed)}
                  />
                </div>
                {callsBreakdown.length ? (
                  <div className="border-t border-slate-100 pt-3">
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                      All-time call status
                    </p>
                    <HorizontalBars data={callsBreakdown.slice(0, 6)} />
                  </div>
                ) : null}
                <p className="text-[11px] text-slate-400">
                  Cost recorded by the provider:{" "}
                  {formatCurrency(stats.total_cost, stats.currency)}.
                </p>
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      <div className="mt-4">
        <Card>
          <CardBody className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-2 text-sm text-slate-600">
              <BarChart3 className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" aria-hidden />
              <p>
                Last refreshed{" "}
                <span className="font-medium text-slate-800">
                  {formatDateTime(
                    new Date(
                      Math.max(
                        statsQuery.dataUpdatedAt,
                        funnelQuery.dataUpdatedAt,
                        leakageQuery.dataUpdatedAt,
                        roiQuery.dataUpdatedAt,
                      ),
                    ).toISOString(),
                  )}
                </span>
                . Rates other than client-side chart counts are backend-calculated.
              </p>
            </div>
            <Badge tone="neutral">
              Min sample {funnel?.min_sample_per_window ?? "—"} calls
            </Badge>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50/50 p-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
        {label}
      </p>
      <p className="mt-0.5 text-base font-semibold tabular-nums text-slate-900">
        {value}
      </p>
    </div>
  );
}
