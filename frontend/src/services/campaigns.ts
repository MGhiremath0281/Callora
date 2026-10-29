import { checkEligibility, initiateCall, listCalls, listLeads } from "@/services/callora";
import type { EligibilityResult, Lead } from "@/types/api";

export type CampaignStatus =
  | "draft"
  | "scheduled"
  | "running"
  | "paused"
  | "completed"
  | "stopped";

export type CampaignLeadItem = {
  lead_id: string;
  name: string;
  phone: string;
  source: string;
  eligible?: boolean;
  block_reason?: string | null;
  call_status?: string | null;
  call_id?: string | null;
  qualified?: boolean | null;
};

export type Campaign = {
  id: string;
  name: string;
  objective: string;
  business_id: string;
  agent_id: string;
  agent_name?: string;
  status: CampaignStatus;
  lead_ids: string[];
  leads_cache?: CampaignLeadItem[];
  total_leads: number;
  eligible_leads: number;
  blocked_leads: number;
  calls_completed: number;
  qualified_leads: number;
  scheduling: {
    start_hour: number;
    end_hour: number;
    days: number[];
    max_retries: number;
    retry_interval_minutes: number;
    is_dry_run?: boolean;
  };
  eligibility_summary?: {
    checked_count: number;
    eligible_count: number;
    blocked_count: number;
    reasons: Record<string, number>;
  };
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  completed_at?: string | null;
};

const STORAGE_KEY_PREFIX = "callora_campaigns_";

function getStorageKey(businessId: string): string {
  return `${STORAGE_KEY_PREFIX}${businessId}`;
}

export function loadStoredCampaigns(businessId: string): Campaign[] {
  try {
    const raw = localStorage.getItem(getStorageKey(businessId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveStoredCampaigns(businessId: string, campaigns: Campaign[]): void {
  try {
    localStorage.setItem(getStorageKey(businessId), JSON.stringify(campaigns));
  } catch (error) {
    console.error("Failed to save campaigns to localStorage", error);
  }
}

/**
 * Returns the campaign list for the workspace, seeding a realistic baseline if none exist.
 */
export async function getCampaigns(businessId: string, businessName = "Workspace"): Promise<Campaign[]> {
  const existing = loadStoredCampaigns(businessId);
  if (existing.length > 0) {
    return existing;
  }

  // Seed default demonstration campaigns that match Indian B2B sales context
  const now = new Date();
  const seedCampaigns: Campaign[] = [
    {
      id: "cmp_demo_blr_001",
      name: "Bangalore SME Outreach Q4",
      objective: "Qualify retail and logistics founders for automated outbound voice agent demo",
      business_id: businessId,
      agent_id: businessId,
      agent_name: `${businessName} Sales Agent`,
      status: "running",
      lead_ids: ["lead_001", "lead_002", "lead_003", "lead_004", "lead_005"],
      total_leads: 18,
      eligible_leads: 14,
      blocked_leads: 4,
      calls_completed: 12,
      qualified_leads: 5,
      scheduling: {
        start_hour: 10,
        end_hour: 19,
        days: [0, 1, 2, 3, 4],
        max_retries: 2,
        retry_interval_minutes: 60,
        is_dry_run: false,
      },
      eligibility_summary: {
        checked_count: 18,
        eligible_count: 14,
        blocked_count: 4,
        reasons: {
          dnd_registered: 2,
          missing_consent: 1,
          outside_calling_window: 1,
        },
      },
      created_at: new Date(now.getTime() - 48 * 3600 * 1000).toISOString(),
      updated_at: new Date(now.getTime() - 2 * 3600 * 1000).toISOString(),
      started_at: new Date(now.getTime() - 24 * 3600 * 1000).toISOString(),
    },
    {
      id: "cmp_demo_hindi_002",
      name: "Hindi Inbound Follow-up Surge",
      objective: "Call inbound website queries in Hindi to confirm budget and decision-maker availability",
      business_id: businessId,
      agent_id: businessId,
      agent_name: `${businessName} Sales Agent`,
      status: "draft",
      lead_ids: ["lead_006", "lead_007", "lead_008"],
      total_leads: 10,
      eligible_leads: 8,
      blocked_leads: 2,
      calls_completed: 0,
      qualified_leads: 0,
      scheduling: {
        start_hour: 11,
        end_hour: 18,
        days: [0, 1, 2, 3, 4, 5],
        max_retries: 1,
        retry_interval_minutes: 120,
        is_dry_run: true,
      },
      eligibility_summary: {
        checked_count: 10,
        eligible_count: 8,
        blocked_count: 2,
        reasons: {
          missing_consent: 2,
        },
      },
      created_at: new Date(now.getTime() - 12 * 3600 * 1000).toISOString(),
      updated_at: new Date(now.getTime() - 12 * 3600 * 1000).toISOString(),
    },
  ];

  saveStoredCampaigns(businessId, seedCampaigns);
  return seedCampaigns;
}

export async function getCampaignById(businessId: string, campaignId: string): Promise<Campaign | null> {
  const campaigns = await getCampaigns(businessId);
  return campaigns.find((c) => c.id === campaignId) || null;
}

export async function createCampaign(
  businessId: string,
  payload: Omit<Campaign, "id" | "created_at" | "updated_at">,
): Promise<Campaign> {
  const campaigns = await getCampaigns(businessId);
  const now = new Date().toISOString();
  const id = `cmp_${Math.random().toString(36).substring(2, 10)}`;
  const newCampaign: Campaign = {
    ...payload,
    id,
    business_id: businessId,
    created_at: now,
    updated_at: now,
  };

  const updated = [newCampaign, ...campaigns];
  saveStoredCampaigns(businessId, updated);
  return newCampaign;
}

export async function updateCampaign(
  businessId: string,
  campaignId: string,
  updates: Partial<Campaign>,
): Promise<Campaign> {
  const campaigns = await getCampaigns(businessId);
  const index = campaigns.findIndex((c) => c.id === campaignId);
  if (index === -1) {
    throw new Error(`Campaign ${campaignId} not found`);
  }

  const updatedCampaign: Campaign = {
    ...campaigns[index],
    ...updates,
    updated_at: new Date().toISOString(),
  };

  campaigns[index] = updatedCampaign;
  saveStoredCampaigns(businessId, campaigns);
  return updatedCampaign;
}

export async function deleteCampaign(businessId: string, campaignId: string): Promise<void> {
  const campaigns = await getCampaigns(businessId);
  const filtered = campaigns.filter((c) => c.id !== campaignId);
  saveStoredCampaigns(businessId, filtered);
}

/**
 * Runs REAL server-side compliance and eligibility checks on a list of leads using /workflow/eligibility.
 * Does not simulate: each lead is sent to the backend evaluate_eligibility gate.
 */
export async function preflightLeadsEligibility(
  leads: Array<{ id: string; name: string; phone: string; source?: string }>,
): Promise<{
  eligible: Array<CampaignLeadItem>;
  blocked: Array<CampaignLeadItem>;
  summary: {
    total: number;
    eligible_count: number;
    blocked_count: number;
    reasons: Record<string, number>;
  };
}> {
  const eligible: CampaignLeadItem[] = [];
  const blocked: CampaignLeadItem[] = [];
  const reasons: Record<string, number> = {};

  // Check in batches or parallel
  await Promise.all(
    leads.map(async (lead) => {
      try {
        const res = await checkEligibility({
          lead_id: lead.id,
          channel: "voice",
          persist: false,
        });

        const isAllowed = Boolean(res.eligible ?? (res as Record<string, unknown>).allowed);
        const reason = res.reason || res.reason_code || (isAllowed ? "Eligible" : "Blocked by policy");

        const item: CampaignLeadItem = {
          lead_id: lead.id,
          name: lead.name,
          phone: lead.phone,
          source: lead.source || "lead",
          eligible: isAllowed,
          block_reason: isAllowed ? null : reason,
        };

        if (isAllowed) {
          eligible.push(item);
        } else {
          blocked.push(item);
          reasons[reason] = (reasons[reason] || 0) + 1;
        }
      } catch (error) {
        // If server returned an error (e.g. LeadNotFound or ConsentMissing), mark as blocked
        const errorMsg = error instanceof Error ? error.message : "Eligibility check failed";
        blocked.push({
          lead_id: lead.id,
          name: lead.name,
          phone: lead.phone,
          source: lead.source || "lead",
          eligible: false,
          block_reason: errorMsg,
        });
        reasons[errorMsg] = (reasons[errorMsg] || 0) + 1;
      }
    }),
  );

  return {
    eligible,
    blocked,
    summary: {
      total: leads.length,
      eligible_count: eligible.length,
      blocked_count: blocked.length,
      reasons,
    },
  };
}

/**
 * Executes a live or dry-run outbound call batch for eligible leads in a campaign,
 * passing campaign attribution to the backend.
 */
export async function runCampaignBatch(
  campaign: Campaign,
  options: { dryRun?: boolean; limit?: number } = {},
): Promise<{
  attempted: number;
  successful: number;
  failed: number;
}> {
  const dryRun = options.dryRun ?? campaign.scheduling.is_dry_run ?? true;
  const eligibleItems = (campaign.leads_cache || []).filter((item) => item.eligible && !item.call_id);
  const toProcess = options.limit ? eligibleItems.slice(0, options.limit) : eligibleItems.slice(0, 5);

  let successful = 0;
  let failed = 0;

  for (const item of toProcess) {
    try {
      const result = await initiateCall({
        lead_id: item.lead_id,
        agent_id: campaign.agent_id,
        dry_run: dryRun,
        extra_context: {
          campaign_id: campaign.id,
          campaign_name: campaign.name,
        },
      });

      if (result.call) {
        item.call_id = result.call.id;
        item.call_status = result.call.status;
        successful += 1;
      }
    } catch {
      failed += 1;
    }
  }

  // Update stored campaign metrics
  campaign.calls_completed = (campaign.calls_completed || 0) + successful;
  if (campaign.calls_completed >= campaign.eligible_leads) {
    campaign.status = "completed";
    campaign.completed_at = new Date().toISOString();
  }
  await updateCampaign(campaign.business_id, campaign.id, campaign);

  return { attempted: toProcess.length, successful, failed };
}
