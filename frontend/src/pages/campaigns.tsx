import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Bot,
  CalendarClock,
  CheckCircle2,
  ChevronRight,
  CircleSlash,
  Clock,
  Eye,
  Megaphone,
  Pause,
  PhoneCall,
  Play,
  Plus,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  StopCircle,
  Trash2,
  Upload,
  UserCheck,
  Users,
  XCircle,
} from "lucide-react";

import { useBusiness } from "@/app/auth";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ButtonLink } from "@/components/ui/button-link";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ConfirmDialog, Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Input, Select, Switch, Textarea } from "@/components/ui/form";
import { PageHeader } from "@/components/ui/page-header";
import { StatCard } from "@/components/ui/stat-card";
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
import { CALLING_DAYS, LANGUAGE_NAMES } from "@/lib/constants";
import { formatDateTime, formatNumber, formatPercent, maskPhone, truncate } from "@/lib/utils";
import { listCalls, listLeads } from "@/services/callora";
import {
  createCampaign,
  deleteCampaign,
  getCampaignById,
  getCampaigns,
  preflightLeadsEligibility,
  runCampaignBatch,
  updateCampaign,
  type Campaign,
  type CampaignLeadItem,
  type CampaignStatus,
} from "@/services/campaigns";

export default function CampaignsPage() {
  const { campaignId: urlCampaignId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const business = useBusiness();
  const queryClient = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();

  const [activeCampaignId, setActiveCampaignId] = useState<string | null>(
    urlCampaignId || searchParams.get("id") || null,
  );
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [createWizardOpen, setCreateWizardOpen] = useState(searchParams.get("new") === "1");
  const [launchConfirmOpen, setLaunchConfirmOpen] = useState(false);
  const [campaignToLaunch, setCampaignToLaunch] = useState<Campaign | null>(null);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
  const [campaignToDelete, setCampaignToDelete] = useState<Campaign | null>(null);

  // Sync with URL params
  useEffect(() => {
    if (urlCampaignId) {
      setActiveCampaignId(urlCampaignId);
    }
  }, [urlCampaignId]);

  // Load campaigns query
  const campaignsQuery = useQuery({
    queryKey: ["campaigns", business.business_id],
    queryFn: () => getCampaigns(business.business_id, business.name),
  });

  const campaigns = campaignsQuery.data ?? [];

  // Filtered campaigns
  const filteredCampaigns = useMemo(() => {
    return campaigns.filter((c) => {
      const matchSearch =
        search.trim() === "" ||
        c.name.toLowerCase().includes(search.toLowerCase()) ||
        c.objective.toLowerCase().includes(search.toLowerCase()) ||
        (c.agent_name && c.agent_name.toLowerCase().includes(search.toLowerCase()));

      const matchStatus = statusFilter === "all" || c.status === statusFilter;
      return matchSearch && matchStatus;
    });
  }, [campaigns, search, statusFilter]);

  // Selected campaign for detail view
  const activeCampaign = useMemo(() => {
    if (!activeCampaignId) return null;
    return campaigns.find((c) => c.id === activeCampaignId) || null;
  }, [campaigns, activeCampaignId]);

  // Update status mutation
  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: CampaignStatus }) =>
      updateCampaign(business.business_id, id, { status }),
    onSuccess: (updated) => {
      toast.success(
        `Campaign ${updated.status}`,
        `Campaign "${updated.name}" is now ${updated.status}.`,
      );
      void queryClient.invalidateQueries({ queryKey: ["campaigns", business.business_id] });
    },
    onError: (error) => {
      toast.error(
        "Status update failed",
        error instanceof Error ? error.message : undefined,
      );
    },
  });

  // Launch campaign mutation (with optional batch execution)
  const launchMutation = useMutation({
    mutationFn: async ({ campaign, dryRun }: { campaign: Campaign; dryRun: boolean }) => {
      // 1. Update status to running
      await updateCampaign(business.business_id, campaign.id, {
        status: "running",
        started_at: new Date().toISOString(),
        scheduling: { ...campaign.scheduling, is_dry_run: dryRun },
      });
      // 2. Dispatch initial outreach batch
      const result = await runCampaignBatch(campaign, { dryRun, limit: 3 });
      return { result, dryRun };
    },
    onSuccess: ({ result, dryRun }) => {
      toast.success(
        dryRun ? "Campaign launched in Dry Run mode" : "Campaign launched live",
        `Compliance gate evaluated. Dispatched ${result.successful} initial calls out of ${result.attempted} eligible leads.`,
      );
      setLaunchConfirmOpen(false);
      setCampaignToLaunch(null);
      void queryClient.invalidateQueries({ queryKey: ["campaigns", business.business_id] });
      void queryClient.invalidateQueries({ queryKey: ["calls"] });
      void queryClient.invalidateQueries({ queryKey: ["voice-stats"] });
    },
    onError: (error) => {
      toast.error(
        "Campaign launch failed",
        error instanceof Error ? error.message : "Failed to initiate campaign calls",
      );
    },
  });

  // Delete campaign mutation
  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteCampaign(business.business_id, id),
    onSuccess: () => {
      toast.success("Campaign deleted", "The campaign configuration was removed.");
      setDeleteConfirmOpen(false);
      setCampaignToDelete(null);
      if (activeCampaignId === campaignToDelete?.id) {
        setActiveCampaignId(null);
        navigate("/campaigns");
      }
      void queryClient.invalidateQueries({ queryKey: ["campaigns", business.business_id] });
    },
  });

  // Summary KPIs across campaigns
  const metrics = useMemo(() => {
    let totalTargeted = 0;
    let totalEligible = 0;
    let totalBlocked = 0;
    let totalCalls = 0;
    let totalQualified = 0;
    let runningCount = 0;

    for (const c of campaigns) {
      totalTargeted += c.total_leads || 0;
      totalEligible += c.eligible_leads || 0;
      totalBlocked += c.blocked_leads || 0;
      totalCalls += c.calls_completed || 0;
      totalQualified += c.qualified_leads || 0;
      if (c.status === "running") runningCount += 1;
    }

    return {
      totalTargeted,
      totalEligible,
      totalBlocked,
      totalCalls,
      totalQualified,
      runningCount,
      totalCampaigns: campaigns.length,
    };
  }, [campaigns]);

  // Handle launch request
  const handleRequestLaunch = (campaign: Campaign) => {
    setCampaignToLaunch(campaign);
    setLaunchConfirmOpen(true);
  };

  // If in detail mode, render detail view
  if (activeCampaign) {
    return (
      <CampaignDetailView
        campaign={activeCampaign}
        onBack={() => {
          setActiveCampaignId(null);
          navigate("/campaigns");
        }}
        onLaunch={() => handleRequestLaunch(activeCampaign)}
        onPause={() =>
          statusMutation.mutate({ id: activeCampaign.id, status: "paused" })
        }
        onResume={() =>
          statusMutation.mutate({ id: activeCampaign.id, status: "running" })
        }
        onStop={() =>
          statusMutation.mutate({ id: activeCampaign.id, status: "stopped" })
        }
        onDelete={() => {
          setCampaignToDelete(activeCampaign);
          setDeleteConfirmOpen(true);
        }}
      />
    );
  }

  return (
    <div>
      <PageHeader
        title="Campaigns"
        description="Coordinate targeted outbound calling to qualified lead lists. Callora strictly enforces server-side compliance, consent verification, and DND checks before every attempt."
        meta={
          <>
            <Badge tone="accent">{business.name}</Badge>
            <Badge tone={metrics.runningCount > 0 ? "success" : "neutral"}>
              {metrics.runningCount} Active {metrics.runningCount === 1 ? "Campaign" : "Campaigns"}
            </Badge>
            <span className="text-xs text-slate-400">
              Server-authoritative eligibility gate
            </span>
          </>
        }
        actions={
          <>
            <ButtonLink to="/leads" variant="outline">
              <Users className="h-4 w-4" aria-hidden /> Manage Leads
            </ButtonLink>
            <Button onClick={() => setCreateWizardOpen(true)}>
              <Plus className="h-4 w-4" aria-hidden /> Create Campaign
            </Button>
          </>
        }
      />

      {/* Headline Metric Cards */}
      <section
        aria-label="Campaign metrics"
        className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4"
      >
        <StatCard
          label="Total Campaigns"
          value={campaignsQuery.isPending ? <Skeleton className="h-7 w-12" /> : formatNumber(metrics.totalCampaigns)}
          hint={`${metrics.runningCount} running · ${campaigns.filter((c) => c.status === "completed").length} completed`}
          icon={Megaphone}
          tone="accent"
        />
        <StatCard
          label="Leads Targeted"
          value={campaignsQuery.isPending ? <Skeleton className="h-7 w-16" /> : formatNumber(metrics.totalTargeted)}
          hint={`${formatNumber(metrics.totalEligible)} eligible · ${formatNumber(metrics.totalBlocked)} blocked`}
          icon={Users}
          tone="default"
        />
        <StatCard
          label="Calls Completed"
          value={campaignsQuery.isPending ? <Skeleton className="h-7 w-16" /> : formatNumber(metrics.totalCalls)}
          hint={`${metrics.totalEligible > 0 ? formatPercent((metrics.totalCalls / metrics.totalEligible) * 100) : "0%"} progress`}
          icon={PhoneCall}
          tone="success"
        />
        <StatCard
          label="Qualified Leads"
          value={campaignsQuery.isPending ? <Skeleton className="h-7 w-12" /> : formatNumber(metrics.totalQualified)}
          hint={`${metrics.totalCalls > 0 ? formatPercent((metrics.totalQualified / metrics.totalCalls) * 100) : "—"} qualification rate`}
          icon={UserCheck}
          tone="accent"
        />
      </section>

      {/* Filter and Search Bar */}
      <Card className="mb-4">
        <div className="flex flex-col gap-3 p-3.5 sm:flex-row sm:items-center">
          <div className="relative flex-1">
            <Search
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
              aria-hidden
            />
            <Input
              aria-label="Search campaigns"
              placeholder="Search by campaign name, objective, or agent…"
              className="pl-9 text-sm"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-slate-500">Status:</span>
            <Select
              aria-label="Filter status"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="w-36 text-xs"
            >
              <option value="all">All statuses</option>
              <option value="running">Running</option>
              <option value="draft">Draft</option>
              <option value="paused">Paused</option>
              <option value="completed">Completed</option>
              <option value="stopped">Stopped</option>
            </Select>
            {(search || statusFilter !== "all") && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSearch("");
                  setStatusFilter("all");
                }}
              >
                Clear
              </Button>
            )}
          </div>
        </div>
      </Card>

      {/* Campaign Listing Table */}
      <Card>
        <CardHeader
          title="All Outbound Campaigns"
          description="Campaigns coordinate multilingual voice calling with server-side compliance preflight and real-time outcome tracking."
          actions={
            <Button
              variant="outline"
              size="sm"
              onClick={() => void campaignsQuery.refetch()}
              loading={campaignsQuery.isFetching}
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden /> Refresh
            </Button>
          }
        />
        {campaignsQuery.isPending ? (
          <TableSkeleton rows={5} columns={7} />
        ) : campaignsQuery.error ? (
          <ErrorState
            error={campaignsQuery.error}
            title="Failed to load campaigns"
            onRetry={() => void campaignsQuery.refetch()}
          />
        ) : filteredCampaigns.length === 0 ? (
          <EmptyState
            icon={<Megaphone className="h-8 w-8 text-slate-400" />}
            title={search || statusFilter !== "all" ? "No matching campaigns" : "No campaigns yet"}
            description={
              search || statusFilter !== "all"
                ? "Try adjusting your search query or status filter."
                : "Create your first outbound campaign to qualify leads with your AI voice agent."
            }
            action={
              <Button onClick={() => setCreateWizardOpen(true)}>
                <Plus className="h-4 w-4" aria-hidden /> Create Campaign
              </Button>
            }
          />
        ) : (
          <TableWrap>
            <Table>
              <THead>
                <TR>
                  <TH>Campaign</TH>
                  <TH>Agent</TH>
                  <TH>Audience</TH>
                  <TH>Progress</TH>
                  <TH>Qualified</TH>
                  <TH>Status</TH>
                  <TH className="text-right">Actions</TH>
                </TR>
              </THead>
              <TBody>
                {filteredCampaigns.map((c) => {
                  const progressPct =
                    c.eligible_leads > 0
                      ? Math.min(100, Math.round((c.calls_completed / c.eligible_leads) * 100))
                      : 0;

                  return (
                    <TR key={c.id}>
                      <TD>
                        <div className="min-w-[180px]">
                          <button
                            type="button"
                            onClick={() => {
                              setActiveCampaignId(c.id);
                              navigate(`/campaigns?id=${c.id}`);
                            }}
                            className="text-left font-semibold text-slate-900 hover:text-accent-700"
                          >
                            {c.name}
                          </button>
                          <p className="mt-0.5 line-clamp-1 text-xs text-slate-500">
                            {c.objective}
                          </p>
                          <span className="font-mono text-[11px] text-slate-400">{c.id}</span>
                        </div>
                      </TD>
                      <TD>
                        <div className="flex items-center gap-1.5 text-xs text-slate-700">
                          <Bot className="h-3.5 w-3.5 text-accent-600" aria-hidden />
                          <span className="truncate">{c.agent_name || business.name}</span>
                        </div>
                      </TD>
                      <TD>
                        <div className="text-xs">
                          <div className="font-medium text-slate-900">
                            {formatNumber(c.total_leads)} total
                          </div>
                          <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-slate-500">
                            <span className="text-emerald-700 font-medium">{c.eligible_leads} eligible</span>
                            <span>·</span>
                            <span className="text-amber-700 font-medium">{c.blocked_leads} blocked</span>
                          </div>
                        </div>
                      </TD>
                      <TD>
                        <div className="w-36">
                          <div className="flex items-center justify-between text-xs">
                            <span className="font-medium text-slate-700">{progressPct}%</span>
                            <span className="text-slate-400">
                              {c.calls_completed} / {c.eligible_leads}
                            </span>
                          </div>
                          <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                            <div
                              className="h-full rounded-full bg-accent-600 transition-all duration-300"
                              style={{ width: `${progressPct}%` }}
                            />
                          </div>
                        </div>
                      </TD>
                      <TD>
                        <div className="text-xs">
                          <div className="font-semibold text-slate-900">
                            {formatNumber(c.qualified_leads)}
                          </div>
                          <div className="text-[11px] text-slate-500">
                            {c.calls_completed > 0
                              ? formatPercent((c.qualified_leads / c.calls_completed) * 100)
                              : "—"}
                          </div>
                        </div>
                      </TD>
                      <TD>
                        <CampaignBadge status={c.status} />
                      </TD>
                      <TD className="text-right">
                        <div className="flex items-center justify-end gap-1.5">
                          {c.status === "draft" && (
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() => handleRequestLaunch(c)}
                              title="Start campaign"
                            >
                              <Play className="h-3.5 w-3.5" aria-hidden /> Start
                            </Button>
                          )}
                          {c.status === "running" && (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() =>
                                statusMutation.mutate({ id: c.id, status: "paused" })
                              }
                              title="Pause campaign"
                            >
                              <Pause className="h-3.5 w-3.5" aria-hidden /> Pause
                            </Button>
                          )}
                          {c.status === "paused" && (
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() =>
                                statusMutation.mutate({ id: c.id, status: "running" })
                              }
                              title="Resume campaign"
                            >
                              <Play className="h-3.5 w-3.5" aria-hidden /> Resume
                            </Button>
                          )}
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => {
                              setActiveCampaignId(c.id);
                              navigate(`/campaigns?id=${c.id}`);
                            }}
                          >
                            <Eye className="h-3.5 w-3.5" aria-hidden /> Details
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => {
                              setCampaignToDelete(c);
                              setDeleteConfirmOpen(true);
                            }}
                          >
                            <Trash2 className="h-3.5 w-3.5 text-rose-500" aria-hidden />
                          </Button>
                        </div>
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          </TableWrap>
        )}
      </Card>

      {/* Campaign Creation Wizard */}
      {createWizardOpen && (
        <CampaignCreationWizard
          isOpen={createWizardOpen}
          onClose={() => setCreateWizardOpen(false)}
          onSuccess={(newCampaign) => {
            setCreateWizardOpen(false);
            void queryClient.invalidateQueries({ queryKey: ["campaigns", business.business_id] });
            toast.success(
              "Campaign Created",
              `Campaign "${newCampaign.name}" saved as draft. You can review preflight results and start it.`,
            );
          }}
        />
      )}

      {/* Explicit Campaign Launch Confirmation Screen (PRD 6.4 mandatory requirement) */}
      {launchConfirmOpen && campaignToLaunch && (
        <CampaignLaunchConfirmationModal
          isOpen={launchConfirmOpen}
          campaign={campaignToLaunch}
          onClose={() => {
            setLaunchConfirmOpen(false);
            setCampaignToLaunch(null);
          }}
          onConfirm={(dryRun) => {
            launchMutation.mutate({ campaign: campaignToLaunch, dryRun });
          }}
          loading={launchMutation.isPending}
        />
      )}

      {/* Delete Confirmation Dialog */}
      {deleteConfirmOpen && campaignToDelete && (
        <ConfirmDialog
          open={deleteConfirmOpen}
          onClose={() => {
            setDeleteConfirmOpen(false);
            setCampaignToDelete(null);
          }}
          title="Delete campaign"
          description={`Are you sure you want to delete campaign "${campaignToDelete.name}"? Historical calls will remain intact, but campaign coordination will be stopped.`}
          confirmLabel="Delete Campaign"
          tone="danger"
          onConfirm={() => deleteMutation.mutate(campaignToDelete.id)}
          loading={deleteMutation.isPending}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Campaign Status Badge                                               */
/* ------------------------------------------------------------------ */

function CampaignBadge({ status }: { status: CampaignStatus }) {
  switch (status) {
    case "running":
      return (
        <Badge tone="success" className="animate-pulse">
          <Play className="h-3 w-3" /> Running
        </Badge>
      );
    case "paused":
      return (
        <Badge tone="warning">
          <Pause className="h-3 w-3" /> Paused
        </Badge>
      );
    case "completed":
      return (
        <Badge tone="accent">
          <CheckCircle2 className="h-3 w-3" /> Completed
        </Badge>
      );
    case "stopped":
      return (
        <Badge tone="danger">
          <StopCircle className="h-3 w-3" /> Stopped
        </Badge>
      );
    case "scheduled":
      return (
        <Badge tone="neutral">
          <Clock className="h-3 w-3" /> Scheduled
        </Badge>
      );
    case "draft":
    default:
      return <Badge tone="neutral">Draft</Badge>;
  }
}

/* ------------------------------------------------------------------ */
/* Campaign Creation Wizard (Multi-step matching PRD 6.4)              */
/* ------------------------------------------------------------------ */

function CampaignCreationWizard({
  isOpen,
  onClose,
  onSuccess,
}: {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (campaign: Campaign) => void;
}) {
  const business = useBusiness();
  const [step, setStep] = useState<1 | 2 | 3 | 4 | 5 | 6>(1);

  // Form State
  const [name, setName] = useState("");
  const [objective, setObjective] = useState("");
  const [selectedAgentId, setSelectedAgentId] = useState(business.business_id);
  const [selectedLeadIds, setSelectedLeadIds] = useState<string[]>([]);
  const [startHour, setStartHour] = useState(business.calling_window_start ?? 9);
  const [endHour, setEndHour] = useState(business.calling_window_end ?? 20);
  const [callingDays, setCallingDays] = useState<number[]>(
    business.calling_days?.length ? business.calling_days : [0, 1, 2, 3, 4],
  );
  const [maxRetries, setMaxRetries] = useState(2);
  const [retryInterval, setRetryInterval] = useState(60);
  const [isDryRun, setIsDryRun] = useState(true);

  // Preflight results
  const [isPreflighting, setIsPreflighting] = useState(false);
  const [preflightData, setPreflightData] = useState<{
    eligible: CampaignLeadItem[];
    blocked: CampaignLeadItem[];
    summary: { total: number; eligible_count: number; blocked_count: number; reasons: Record<string, number> };
  } | null>(null);

  // Fetch leads for audience selection
  const leadsQuery = useQuery({
    queryKey: ["wizard-leads"],
    queryFn: () => listLeads({ page_size: 100 }),
    enabled: isOpen,
  });

  const availableLeads = leadsQuery.data?.items ?? [];

  // Toggle single lead selection
  const toggleLead = (id: string) => {
    setSelectedLeadIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id],
    );
  };

  // Select all leads
  const selectAllLeads = () => {
    if (selectedLeadIds.length === availableLeads.length) {
      setSelectedLeadIds([]);
    } else {
      setSelectedLeadIds(availableLeads.map((l) => l.id));
    }
  };

  // Run server-side preflight when entering step 4
  const triggerPreflight = async () => {
    if (selectedLeadIds.length === 0) return;
    setIsPreflighting(true);
    try {
      const targetLeads = availableLeads.filter((l) => selectedLeadIds.includes(l.id));
      const res = await preflightLeadsEligibility(targetLeads);
      setPreflightData(res);
    } catch (e) {
      console.error("Preflight failed", e);
    } finally {
      setIsPreflighting(false);
    }
  };

  const handleNext = async () => {
    if (step === 3) {
      setStep(4);
      await triggerPreflight();
    } else if (step < 6) {
      setStep((prev) => (prev + 1) as 1 | 2 | 3 | 4 | 5 | 6);
    }
  };

  const handleSave = async (status: CampaignStatus = "draft") => {
    const eligibleCount = preflightData?.summary.eligible_count ?? selectedLeadIds.length;
    const blockedCount = preflightData?.summary.blocked_count ?? 0;

    const campaign = await createCampaign(business.business_id, {
      name: name.trim() || "Untitled Campaign",
      objective: objective.trim() || "Outbound qualification outreach",
      business_id: business.business_id,
      agent_id: selectedAgentId,
      agent_name: `${business.name} Sales Agent`,
      status,
      lead_ids: selectedLeadIds,
      leads_cache: [
        ...(preflightData?.eligible || []),
        ...(preflightData?.blocked || []),
      ],
      total_leads: selectedLeadIds.length,
      eligible_leads: eligibleCount,
      blocked_leads: blockedCount,
      calls_completed: 0,
      qualified_leads: 0,
      scheduling: {
        start_hour: startHour,
        end_hour: endHour,
        days: callingDays,
        max_retries: maxRetries,
        retry_interval_minutes: retryInterval,
        is_dry_run: isDryRun,
      },
      eligibility_summary: preflightData?.summary
        ? {
            checked_count: preflightData.summary.total,
            eligible_count: preflightData.summary.eligible_count,
            blocked_count: preflightData.summary.blocked_count,
            reasons: preflightData.summary.reasons,
          }
        : undefined,
    });

    onSuccess(campaign);
  };

  return (
    <Dialog open={isOpen} onClose={onClose} title="Create Outbound Campaign" size="xl">
      <div className="p-2">
        {/* Wizard Header */}
        <div className="flex items-center justify-between border-b border-slate-100 pb-3 mb-4">
          <div>
            <p className="text-xs text-slate-500">
              Step {step} of 6 · Configure audience, active agent, compliance checks and scheduling
            </p>
          </div>
          <Badge tone="accent">Step {step} / 6</Badge>
        </div>

        {/* Step Indicator */}
        <div className="mt-4 flex items-center justify-between gap-1 overflow-x-auto pb-2 text-xs">
          {[
            { num: 1, label: "Objective" },
            { num: 2, label: "Agent" },
            { num: 3, label: "Audience" },
            { num: 4, label: "Preflight" },
            { num: 5, label: "Scheduling" },
            { num: 6, label: "Review" },
          ].map((item) => (
            <div
              key={item.num}
              className={`flex items-center gap-1.5 px-2 py-1 rounded-md transition ${
                step === item.num
                  ? "bg-navy-900 text-white font-medium"
                  : step > item.num
                  ? "text-emerald-700 bg-emerald-50"
                  : "text-slate-400 bg-slate-50"
              }`}
            >
              <span className="flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-bold">
                {step > item.num ? "✓" : item.num}
              </span>
              <span>{item.label}</span>
            </div>
          ))}
        </div>

        {/* Wizard Step Content */}
        <div className="mt-6 min-h-[300px]">
          {/* STEP 1: Basic Info */}
          {step === 1 && (
            <div className="space-y-4">
              <Field label="Campaign Name" required hint="A descriptive title for this calling surge">
                <Input
                  placeholder="e.g. Bangalore Founders Q4 Demo Drive"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoFocus
                />
              </Field>
              <Field
                label="Sales Objective"
                required
                hint="Explain what the AI agent aims to accomplish during the call"
              >
                <Textarea
                  rows={4}
                  placeholder="e.g. Call mid-market logistics leads in English & Hindi, qualify budget and team size, and transfer interested prospects to an account executive."
                  value={objective}
                  onChange={(e) => setObjective(e.target.value)}
                />
              </Field>
            </div>
          )}

          {/* STEP 2: Agent Selection */}
          {step === 2 && (
            <div className="space-y-4">
              <p className="text-sm text-slate-600">
                Select the voice sales agent to assign to this campaign. The agent's instructions,
                voice synthesis, and qualification questions will guide the outreach.
              </p>
              <div className="rounded-xl border border-accent-300 bg-accent-50/50 p-4">
                <div className="flex items-start gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent-600 text-white">
                    <Bot className="h-5 w-5" aria-hidden />
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <h4 className="text-sm font-semibold text-slate-900">{business.name} Voice Agent</h4>
                      <Badge tone="success">Active</Badge>
                    </div>
                    <p className="mt-1 text-xs text-slate-600">
                      {truncate(business.agent_instructions || "Sales qualification agent", 120)}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2 text-xs">
                      <Badge tone="neutral">
                        Languages: {business.supported_languages?.map((c) => LANGUAGE_NAMES[c] || c).join(", ") || "English"}
                      </Badge>
                      <Badge tone="neutral">
                        Primary: {LANGUAGE_NAMES[business.default_language] || business.default_language}
                      </Badge>
                      <Badge tone="neutral">Timezone: {business.timezone}</Badge>
                    </div>
                  </div>
                </div>
              </div>
              <InlineAlert tone="info" title="Configured Voice Agent">
                Campaign outreach utilizes the workspace's authoritative agent profile. To adjust qualification questions or greetings, visit Agent Studio.
              </InlineAlert>
            </div>
          )}

          {/* STEP 3: Audience Selection */}
          {step === 3 && (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-semibold text-slate-900">Select Leads for Outreach</p>
                  <p className="text-xs text-slate-500">
                    Selected: {selectedLeadIds.length} of {availableLeads.length} leads
                  </p>
                </div>
                <Button variant="outline" size="sm" onClick={selectAllLeads}>
                  {selectedLeadIds.length === availableLeads.length ? "Deselect All" : "Select All Leads"}
                </Button>
              </div>

              {availableLeads.length === 0 ? (
                <div className="rounded-lg border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">
                  <Users className="mx-auto h-8 w-8 text-slate-400" />
                  <p className="mt-2 font-medium text-slate-700">No leads available</p>
                  <p className="text-xs text-slate-500">Upload a CSV or add leads in the Leads section first.</p>
                </div>
              ) : (
                <div className="max-h-60 overflow-y-auto rounded-lg border border-slate-200">
                  <table className="w-full text-left text-xs">
                    <thead className="bg-slate-50 text-slate-600 sticky top-0">
                      <tr>
                        <th className="p-2.5 w-10">
                          <input
                            type="checkbox"
                            checked={selectedLeadIds.length === availableLeads.length && availableLeads.length > 0}
                            onChange={selectAllLeads}
                            aria-label="Select all"
                          />
                        </th>
                        <th className="p-2.5 font-medium">Lead Name</th>
                        <th className="p-2.5 font-medium">Phone</th>
                        <th className="p-2.5 font-medium">Source</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {availableLeads.map((lead) => {
                        const isSelected = selectedLeadIds.includes(lead.id);
                        return (
                          <tr
                            key={lead.id}
                            className={`cursor-pointer hover:bg-slate-50 ${isSelected ? "bg-accent-50/40" : ""}`}
                            onClick={() => toggleLead(lead.id)}
                          >
                            <td className="p-2.5">
                              <input
                                type="checkbox"
                                checked={isSelected}
                                onChange={() => toggleLead(lead.id)}
                                onClick={(e) => e.stopPropagation()}
                              />
                            </td>
                            <td className="p-2.5 font-medium text-slate-900">{lead.name}</td>
                            <td className="p-2.5 text-slate-500 font-mono">{maskPhone(lead.phone)}</td>
                            <td className="p-2.5">
                              <Badge tone="neutral">{lead.source || "lead"}</Badge>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* STEP 4: Server-Side Preflight Review */}
          {step === 4 && (
            <div className="space-y-4">
              <div>
                <h4 className="text-sm font-semibold text-slate-900">Server-Side Compliance Preflight</h4>
                <p className="text-xs text-slate-500">
                  Every selected lead is verified through the backend eligibility gate (/workflow/eligibility).
                </p>
              </div>

              {isPreflighting ? (
                <div className="flex flex-col items-center justify-center p-8 space-y-3">
                  <RefreshCw className="h-6 w-6 animate-spin text-accent-600" />
                  <p className="text-sm text-slate-600">Evaluating consent, DND status, and calling windows…</p>
                </div>
              ) : preflightData ? (
                <div className="space-y-4">
                  <div className="grid grid-cols-3 gap-3">
                    <div className="rounded-lg border border-slate-200 bg-white p-3 text-center">
                      <div className="text-xl font-bold text-slate-900">{preflightData.summary.total}</div>
                      <div className="text-xs text-slate-500">Total Checked</div>
                    </div>
                    <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-center">
                      <div className="text-xl font-bold text-emerald-700">{preflightData.summary.eligible_count}</div>
                      <div className="text-xs text-emerald-700 font-medium">Eligible Leads</div>
                    </div>
                    <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-center">
                      <div className="text-xl font-bold text-amber-700">{preflightData.summary.blocked_count}</div>
                      <div className="text-xs text-amber-700 font-medium">Blocked by Gate</div>
                    </div>
                  </div>

                  {preflightData.summary.blocked_count > 0 && (
                    <InlineAlert tone="warning" title="Compliant Outreach Rule">
                      {preflightData.summary.blocked_count} leads were rejected by the server compliance gate (e.g. DND or missing consent). The platform will automatically skip them during outreach.
                    </InlineAlert>
                  )}

                  <div className="rounded-lg border border-slate-200 p-3">
                    <p className="text-xs font-semibold text-slate-700 mb-2">Gate Decisions Breakdown:</p>
                    <div className="space-y-1.5 text-xs">
                      {Object.entries(preflightData.summary.reasons).map(([reason, count]) => (
                        <div key={reason} className="flex items-center justify-between">
                          <span className="text-slate-600 flex items-center gap-1.5">
                            <ShieldAlert className="h-3.5 w-3.5 text-amber-500" /> {reason}
                          </span>
                          <span className="font-semibold text-slate-800">{count} leads</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <InlineAlert tone="info" title="Preflight ready">
                  Click 'Next' to run the server-side eligibility check.
                </InlineAlert>
              )}
            </div>
          )}

          {/* STEP 5: Scheduling & Retry Controls */}
          {step === 5 && (
            <div className="space-y-4">
              <h4 className="text-sm font-semibold text-slate-900">Calling Window & Retry Rules</h4>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Daily Start Hour (24h)">
                  <Select value={startHour} onChange={(e) => setStartHour(Number(e.target.value))}>
                    {Array.from({ length: 24 }).map((_, h) => (
                      <option key={h} value={h}>
                        {String(h).padStart(2, "0")}:00
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Daily End Hour (24h)">
                  <Select value={endHour} onChange={(e) => setEndHour(Number(e.target.value))}>
                    {Array.from({ length: 24 }).map((_, h) => (
                      <option key={h} value={h}>
                        {String(h).padStart(2, "0")}:00
                      </option>
                    ))}
                  </Select>
                </Field>
              </div>

              <div>
                <p className="text-xs font-medium text-slate-700 mb-1.5">Allowed Calling Days</p>
                <div className="flex flex-wrap gap-1.5">
                  {CALLING_DAYS.map((d) => {
                    const isSelected = callingDays.includes(d.value);
                    return (
                      <button
                        key={d.value}
                        type="button"
                        onClick={() => {
                          setCallingDays((prev) =>
                            prev.includes(d.value)
                              ? prev.filter((val) => val !== d.value)
                              : [...prev, d.value].sort(),
                          );
                        }}
                        className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${
                          isSelected
                            ? "border-accent-300 bg-accent-50 text-accent-700"
                            : "border-slate-200 bg-white text-slate-500"
                        }`}
                      >
                        {d.label.slice(0, 3)}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Max Retries on Busy / Unanswered">
                  <Input
                    type="number"
                    min={0}
                    max={5}
                    value={maxRetries}
                    onChange={(e) => setMaxRetries(Number(e.target.value))}
                  />
                </Field>
                <Field label="Retry Interval (Minutes)">
                  <Input
                    type="number"
                    min={15}
                    max={1440}
                    value={retryInterval}
                    onChange={(e) => setRetryInterval(Number(e.target.value))}
                  />
                </Field>
              </div>

              <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                <Switch
                  checked={isDryRun}
                  onChange={setIsDryRun}
                  label="Dry Run Validation Mode"
                  description="When enabled, tests the full compliance flow and logs call records without placing audio calls to phones."
                />
              </div>
            </div>
          )}

          {/* STEP 6: Review & Confirmation */}
          {step === 6 && (
            <div className="space-y-4">
              <h4 className="text-sm font-semibold text-slate-900">Campaign Summary</h4>
              <div className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white text-xs">
                <div className="flex justify-between p-2.5">
                  <span className="text-slate-500">Campaign Name:</span>
                  <span className="font-semibold text-slate-900">{name || "Untitled"}</span>
                </div>
                <div className="flex justify-between p-2.5">
                  <span className="text-slate-500">Sales Objective:</span>
                  <span className="font-medium text-slate-800 max-w-xs text-right truncate">
                    {objective || "—"}
                  </span>
                </div>
                <div className="flex justify-between p-2.5">
                  <span className="text-slate-500">Assigned Agent:</span>
                  <span className="font-medium text-slate-900">{business.name} Voice Agent</span>
                </div>
                <div className="flex justify-between p-2.5">
                  <span className="text-slate-500">Audience:</span>
                  <span className="font-semibold text-slate-900">
                    {selectedLeadIds.length} Total ({preflightData?.summary.eligible_count ?? selectedLeadIds.length} Eligible,{" "}
                    {preflightData?.summary.blocked_count ?? 0} Blocked)
                  </span>
                </div>
                <div className="flex justify-between p-2.5">
                  <span className="text-slate-500">Calling Hours:</span>
                  <span className="text-slate-700">
                    {String(startHour).padStart(2, "0")}:00 – {String(endHour).padStart(2, "0")}:00 ({business.timezone})
                  </span>
                </div>
                <div className="flex justify-between p-2.5">
                  <span className="text-slate-500">Mode:</span>
                  <Badge tone={isDryRun ? "neutral" : "warning"}>
                    {isDryRun ? "Dry Run (Safe Validation)" : "Live Outbound Calling"}
                  </Badge>
                </div>
              </div>

              <InlineAlert tone="info" title="Ready to save or launch">
                Saving as draft allows inspecting preflight records before dialing. Launching initiates outbound calling for eligible leads.
              </InlineAlert>
            </div>
          )}
        </div>

        {/* Wizard Footer Controls */}
        <div className="mt-8 flex items-center justify-between border-t border-slate-100 pt-4">
          <Button
            variant="ghost"
            onClick={step === 1 ? onClose : () => setStep((prev) => (prev - 1) as 1 | 2 | 3 | 4 | 5 | 6)}
          >
            {step === 1 ? "Cancel" : "Back"}
          </Button>

          <div className="flex items-center gap-2">
            {step < 6 ? (
              <Button
                onClick={handleNext}
                disabled={step === 1 && !name.trim()}
              >
                Next <ChevronRight className="h-4 w-4" aria-hidden />
              </Button>
            ) : (
              <>
                <Button variant="outline" onClick={() => void handleSave("draft")}>
                  Save as Draft
                </Button>
                <Button onClick={() => void handleSave("draft")}>
                  <Play className="h-4 w-4" aria-hidden /> Save & Open Details
                </Button>
              </>
            )}
          </div>
        </div>
      </div>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ */
/* Campaign Launch Confirmation Modal (Mandatory PRD 6.4 requirement)  */
/* ------------------------------------------------------------------ */

function CampaignLaunchConfirmationModal({
  isOpen,
  campaign,
  onClose,
  onConfirm,
  loading,
}: {
  isOpen: boolean;
  campaign: Campaign;
  onClose: () => void;
  onConfirm: (dryRun: boolean) => void;
  loading: boolean;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [dryRunMode, setDryRunMode] = useState(campaign.scheduling.is_dry_run ?? true);

  return (
    <Dialog open={isOpen} onClose={onClose} title="Confirm Campaign Launch" size="lg">
      <div className="p-2">
        <div className="flex items-center gap-3 text-navy-950">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-accent-100 text-accent-700">
            <Megaphone className="h-5 w-5" aria-hidden />
          </div>
          <div>
            <p className="text-xs text-slate-500">
              Outbound Calling Pre-Execution Gate
            </p>
          </div>
        </div>

        <div className="mt-4 space-y-3.5">
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3.5 text-xs space-y-1.5">
            <div className="flex justify-between">
              <span className="text-slate-500">Campaign:</span>
              <span className="font-semibold text-slate-900">{campaign.name}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Target Audience:</span>
              <span className="font-medium text-slate-800">
                {campaign.total_leads} leads ({campaign.eligible_leads} eligible, {campaign.blocked_leads} blocked)
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Assigned Agent:</span>
              <span className="font-medium text-slate-800">{campaign.agent_name || "Sales Agent"}</span>
            </div>
          </div>

          <InlineAlert tone="warning" title="Server Compliance Assurance">
            The platform will only dial the <strong>{campaign.eligible_leads} eligible leads</strong>. Any lead with missing consent or registered on DND will be safely bypassed.
          </InlineAlert>

          <div className="rounded-lg border border-slate-200 p-3">
            <p className="text-xs font-semibold text-slate-800 mb-2">Execution Mode</p>
            <div className="space-y-2 text-xs">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="launch_mode"
                  checked={dryRunMode}
                  onChange={() => setDryRunMode(true)}
                />
                <div>
                  <span className="font-medium text-slate-800">Dry Run Mode (Recommended for testing)</span>
                  <p className="text-slate-500">Tests compliance, generates call traces, but does not ring client phones.</p>
                </div>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="launch_mode"
                  checked={!dryRunMode}
                  onChange={() => setDryRunMode(false)}
                />
                <div>
                  <span className="font-medium text-slate-800">Live Voice Outreach</span>
                  <p className="text-slate-500">Places real telephone calls via Bolna integration to verified prospects.</p>
                </div>
              </label>
            </div>
          </div>

          <label className="flex items-start gap-2.5 rounded-lg border border-slate-200 bg-white p-3 text-xs text-slate-700 cursor-pointer">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            <span>
              I confirm that this campaign complies with Indian telecommunications regulations, business calling hours, and that the audience has granted valid calling consent.
            </span>
          </label>
        </div>

        <div className="mt-6 flex items-center justify-end gap-2 border-t border-slate-100 pt-4">
          <Button variant="ghost" onClick={onClose} disabled={loading}>
            Cancel
          </Button>
          <Button
            onClick={() => onConfirm(dryRunMode)}
            disabled={!confirmed || loading}
            loading={loading}
          >
            <Play className="h-4 w-4" aria-hidden /> Confirm & Start Campaign
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ */
/* Campaign Details View (Tabs: Overview, Audience, Recent Calls, etc) */
/* ------------------------------------------------------------------ */

function CampaignDetailView({
  campaign,
  onBack,
  onLaunch,
  onPause,
  onResume,
  onStop,
  onDelete,
}: {
  campaign: Campaign;
  onBack: () => void;
  onLaunch: () => void;
  onPause: () => void;
  onResume: () => void;
  onStop: () => void;
  onDelete: () => void;
}) {
  const [tab, setTab] = useState<"overview" | "audience" | "calls" | "compliance" | "settings">(
    "overview",
  );

  // Calls linked to this campaign
  const callsQuery = useQuery({
    queryKey: ["campaign-calls", campaign.id],
    queryFn: () => listCalls({ limit: 100 }),
  });

  const campaignCalls = useMemo(() => {
    const all = callsQuery.data?.items ?? [];
    return all.filter(
      (c) =>
        (c.metadata as Record<string, unknown>)?.campaign_id === campaign.id ||
        campaign.lead_ids.includes(c.lead_id),
    );
  }, [callsQuery.data, campaign]);

  const progressPct =
    campaign.eligible_leads > 0
      ? Math.min(100, Math.round((campaign.calls_completed / campaign.eligible_leads) * 100))
      : 0;

  return (
    <div>
      <PageHeader
        title={campaign.name}
        description={campaign.objective}
        meta={
          <>
            <CampaignBadge status={campaign.status} />
            <Badge tone="neutral">{campaign.id}</Badge>
            <Badge tone="accent">{campaign.agent_name || "Sales Agent"}</Badge>
          </>
        }
        actions={
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={onBack}>
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> Back to Campaigns
            </Button>
            {campaign.status === "draft" && (
              <Button size="sm" onClick={onLaunch}>
                <Play className="h-3.5 w-3.5" aria-hidden /> Start Campaign
              </Button>
            )}
            {campaign.status === "running" && (
              <Button size="sm" variant="outline" onClick={onPause}>
                <Pause className="h-3.5 w-3.5" aria-hidden /> Pause
              </Button>
            )}
            {campaign.status === "paused" && (
              <Button size="sm" variant="outline" onClick={onResume}>
                <Play className="h-3.5 w-3.5" aria-hidden /> Resume
              </Button>
            )}
            {["running", "paused"].includes(campaign.status) && (
              <Button size="sm" variant="outline" onClick={onStop}>
                <StopCircle className="h-3.5 w-3.5 text-rose-500" aria-hidden /> Stop
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={onDelete}>
              <Trash2 className="h-3.5 w-3.5 text-rose-500" aria-hidden />
            </Button>
          </div>
        }
      />

      {/* KPI Tiles */}
      <section className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <StatCard
          label="Progress"
          value={`${progressPct}%`}
          hint={`${campaign.calls_completed} of ${campaign.eligible_leads} completed`}
          icon={PhoneCall}
          tone="accent"
        />
        <StatCard
          label="Eligible Audience"
          value={formatNumber(campaign.eligible_leads)}
          hint={`${campaign.total_leads} total leads`}
          icon={Users}
          tone="success"
        />
        <StatCard
          label="Compliance Blocked"
          value={formatNumber(campaign.blocked_leads)}
          hint="Excluded by compliance gate"
          icon={ShieldAlert}
          tone="warning"
        />
        <StatCard
          label="Qualified Leads"
          value={formatNumber(campaign.qualified_leads)}
          hint={`${campaign.calls_completed > 0 ? formatPercent((campaign.qualified_leads / campaign.calls_completed) * 100) : "0%"} conversion`}
          icon={UserCheck}
          tone="accent"
        />
        <StatCard
          label="Calling Window"
          value={`${String(campaign.scheduling.start_hour).padStart(2, "0")}:00 – ${String(campaign.scheduling.end_hour).padStart(2, "0")}:00`}
          hint={`${campaign.scheduling.days.length} days active`}
          icon={CalendarClock}
          tone="default"
        />
      </section>

      {/* Campaign Details Tabs */}
      <Tabs
        items={[
          { id: "overview", label: "Overview & Progress" },
          { id: "audience", label: `Audience & Preflight (${campaign.total_leads})` },
          { id: "calls", label: `Recent Calls (${campaignCalls.length})` },
          { id: "compliance", label: "Compliance & Blocks" },
          { id: "settings", label: "Scheduling & Retry" },
        ]}
        value={tab}
        onChange={(t) => setTab(t as typeof tab)}
      />

      <div className="mt-4">
        {/* TAB 1: OVERVIEW */}
        {tab === "overview" && (
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Execution Progress" description="Outbound voice qualification trajectory" />
              <CardBody className="space-y-4">
                <div>
                  <div className="flex justify-between text-sm font-medium text-slate-800 mb-1">
                    <span>Campaign Completion</span>
                    <span>{progressPct}%</span>
                  </div>
                  <div className="h-3 w-full rounded-full bg-slate-100 overflow-hidden">
                    <div
                      className="h-full bg-accent-600 rounded-full transition-all duration-500"
                      style={{ width: `${progressPct}%` }}
                    />
                  </div>
                  <div className="mt-2 flex justify-between text-xs text-slate-500">
                    <span>{campaign.calls_completed} calls made</span>
                    <span>{Math.max(0, campaign.eligible_leads - campaign.calls_completed)} remaining</span>
                  </div>
                </div>

                <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-3 space-y-2 text-xs">
                  <div className="flex justify-between">
                    <span className="text-slate-500">Status:</span>
                    <CampaignBadge status={campaign.status} />
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Mode:</span>
                    <span className="font-medium text-slate-800">
                      {campaign.scheduling.is_dry_run ? "Dry Run (Simulated)" : "Live Phone Outreach"}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Started At:</span>
                    <span className="text-slate-700">
                      {campaign.started_at ? formatDateTime(campaign.started_at) : "Not yet started"}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-500">Last Updated:</span>
                    <span className="text-slate-700">{formatDateTime(campaign.updated_at)}</span>
                  </div>
                </div>
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Campaign Objectives & Setup" description="Configuration parameters" />
              <CardBody className="space-y-3 text-xs">
                <div>
                  <p className="font-semibold text-slate-800">Objective</p>
                  <p className="mt-0.5 text-slate-600 leading-relaxed">{campaign.objective}</p>
                </div>
                <div>
                  <p className="font-semibold text-slate-800">Bound Sales Agent</p>
                  <p className="mt-0.5 text-slate-600 flex items-center gap-1.5">
                    <Bot className="h-4 w-4 text-accent-600" /> {campaign.agent_name || "Workspace Sales Agent"}
                  </p>
                </div>
                <div>
                  <p className="font-semibold text-slate-800">Max Retry Policy</p>
                  <p className="mt-0.5 text-slate-600">
                    {campaign.scheduling.max_retries} attempts on busy or unanswered (interval: {campaign.scheduling.retry_interval_minutes} mins)
                  </p>
                </div>
              </CardBody>
            </Card>
          </div>
        )}

        {/* TAB 2: AUDIENCE & PREFLIGHT */}
        {tab === "audience" && (
          <Card>
            <CardHeader
              title="Target Audience & Preflight Status"
              description="Each record is verified against the server-side compliance gate before placement."
            />
            {campaign.leads_cache && campaign.leads_cache.length > 0 ? (
              <TableWrap>
                <Table>
                  <THead>
                    <TR>
                      <TH>Lead Name</TH>
                      <TH>Phone</TH>
                      <TH>Source</TH>
                      <TH>Eligibility Status</TH>
                      <TH>Call Status</TH>
                      <TH className="text-right">Action</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {campaign.leads_cache.map((lead) => (
                      <TR key={lead.lead_id}>
                        <TD>
                          <span className="font-medium text-slate-900">{lead.name}</span>
                        </TD>
                        <TD>
                          <span className="font-mono text-xs text-slate-500">{maskPhone(lead.phone)}</span>
                        </TD>
                        <TD>
                          <Badge tone="neutral">{lead.source || "lead"}</Badge>
                        </TD>
                        <TD>
                          {lead.eligible ? (
                            <Badge tone="success">
                              <CheckCircle2 className="h-3 w-3" /> Eligible
                            </Badge>
                          ) : (
                            <Badge tone="warning">
                              <ShieldAlert className="h-3 w-3" /> {lead.block_reason || "Blocked"}
                            </Badge>
                          )}
                        </TD>
                        <TD>
                          {lead.call_status ? (
                            <StatusBadge status={lead.call_status} />
                          ) : (
                            <span className="text-slate-400 text-xs">Pending call</span>
                          )}
                        </TD>
                        <TD className="text-right">
                          <ButtonLink
                            to={`/leads/${lead.lead_id}`}
                            size="sm"
                            variant="ghost"
                          >
                            Inspect Lead
                          </ButtonLink>
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </TableWrap>
            ) : (
              <EmptyState
                icon={<Users className="h-6 w-6 text-slate-400" />}
                title="Audience cache not loaded"
                description="Lead identifiers are stored with the campaign. Open the Leads page to inspect specific contact profiles."
                action={
                  <ButtonLink to="/leads" variant="outline">
                    View Workspace Leads
                  </ButtonLink>
                }
              />
            )}
          </Card>
        )}

        {/* TAB 3: RECENT CALLS */}
        {tab === "calls" && (
          <Card>
            <CardHeader
              title="Recent Campaign Calls"
              description="Outbound call events and transcript outcomes recorded for this campaign."
            />
            {campaignCalls.length === 0 ? (
              <EmptyState
                icon={<PhoneCall className="h-6 w-6 text-slate-400" />}
                title="No calls recorded yet"
                description={
                  campaign.status === "draft"
                    ? "Start the campaign to begin placing calls to eligible leads."
                    : "Outreach calls will appear here as the campaign executes."
                }
              />
            ) : (
              <TableWrap>
                <Table>
                  <THead>
                    <TR>
                      <TH>Call ID</TH>
                      <TH>Lead ID</TH>
                      <TH>Status</TH>
                      <TH>Duration</TH>
                      <TH>Initiated</TH>
                      <TH className="text-right">Action</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {campaignCalls.map((c) => (
                      <TR key={c.id}>
                        <TD>
                          <Link
                            to={`/calls/${c.id}`}
                            className="font-mono text-xs font-semibold text-accent-700 hover:underline"
                          >
                            {c.id}
                          </Link>
                        </TD>
                        <TD>
                          <span className="font-mono text-xs text-slate-600">{c.lead_id}</span>
                        </TD>
                        <TD>
                          <StatusBadge status={c.status} />
                        </TD>
                        <TD>
                          <span className="text-xs text-slate-700">
                            {c.duration_seconds ? `${c.duration_seconds}s` : "—"}
                          </span>
                        </TD>
                        <TD>
                          <span className="text-xs text-slate-500">
                            {c.created_at ? formatDateTime(c.created_at) : "—"}
                          </span>
                        </TD>
                        <TD className="text-right">
                          <ButtonLink to={`/calls/${c.id}`} size="sm" variant="ghost">
                            View Transcript
                          </ButtonLink>
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </TableWrap>
            )}
          </Card>
        )}

        {/* TAB 4: COMPLIANCE & BLOCKS */}
        {tab === "compliance" && (
          <Card>
            <CardHeader
              title="Compliance Gate & Block Reasons"
              description="Full auditability: explain why leads were accepted or rejected before calling."
            />
            <CardBody className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-lg border border-slate-200 p-4">
                  <h4 className="text-sm font-semibold text-slate-900 mb-2">Preflight Gate Decisions</h4>
                  <div className="space-y-2 text-xs">
                    <div className="flex justify-between border-b border-slate-100 pb-1.5">
                      <span className="text-slate-600">Total Leads Checked:</span>
                      <span className="font-bold text-slate-900">{campaign.total_leads}</span>
                    </div>
                    <div className="flex justify-between border-b border-slate-100 pb-1.5 text-emerald-700">
                      <span>Server-Approved Eligible:</span>
                      <span className="font-bold">{campaign.eligible_leads}</span>
                    </div>
                    <div className="flex justify-between text-amber-700">
                      <span>Refused by Compliance Gate:</span>
                      <span className="font-bold">{campaign.blocked_leads}</span>
                    </div>
                  </div>
                </div>

                <div className="rounded-lg border border-slate-200 p-4">
                  <h4 className="text-sm font-semibold text-slate-900 mb-2">Block Reason Distribution</h4>
                  {campaign.eligibility_summary?.reasons &&
                  Object.keys(campaign.eligibility_summary.reasons).length > 0 ? (
                    <div className="space-y-2 text-xs">
                      {Object.entries(campaign.eligibility_summary.reasons).map(([reason, count]) => (
                        <div key={reason} className="flex justify-between items-center">
                          <span className="text-slate-600 flex items-center gap-1">
                            <ShieldAlert className="h-3.5 w-3.5 text-amber-500" /> {reason}
                          </span>
                          <span className="font-semibold text-slate-800">{count}</span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-slate-500">No block reasons recorded.</p>
                  )}
                </div>
              </div>

              <InlineAlert tone="info" title="Auditable Calling Guarantee">
                In adherence to PRD Section 6.10, the frontend never independently overrides eligibility. All calling decisions originate from server-side compliance checks.
              </InlineAlert>
            </CardBody>
          </Card>
        )}

        {/* TAB 5: SCHEDULING & RETRY SETTINGS */}
        {tab === "settings" && (
          <Card>
            <CardHeader title="Scheduling & Retry Configuration" description="Runtime operational rules" />
            <CardBody className="space-y-3 text-xs">
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-lg border border-slate-200 p-3">
                  <span className="text-slate-500">Daily Calling Hours:</span>
                  <p className="mt-1 font-semibold text-slate-900">
                    {String(campaign.scheduling.start_hour).padStart(2, "0")}:00 –{" "}
                    {String(campaign.scheduling.end_hour).padStart(2, "0")}:00
                  </p>
                </div>
                <div className="rounded-lg border border-slate-200 p-3">
                  <span className="text-slate-500">Active Days:</span>
                  <p className="mt-1 font-semibold text-slate-900">
                    {campaign.scheduling.days.map((d) => CALLING_DAYS.find((cd) => cd.value === d)?.label).join(", ")}
                  </p>
                </div>
                <div className="rounded-lg border border-slate-200 p-3">
                  <span className="text-slate-500">Max Retry Attempts:</span>
                  <p className="mt-1 font-semibold text-slate-900">{campaign.scheduling.max_retries} attempts</p>
                </div>
                <div className="rounded-lg border border-slate-200 p-3">
                  <span className="text-slate-500">Retry Interval:</span>
                  <p className="mt-1 font-semibold text-slate-900">{campaign.scheduling.retry_interval_minutes} minutes</p>
                </div>
              </div>
            </CardBody>
          </Card>
        )}
      </div>
    </div>
  );
}
