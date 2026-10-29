import { apiRequest } from "@/lib/api";
import type {
  AuditLog,
  Business,
  BusinessUpdate,
  Call,
  CallEvent,
  CallInitiationResponse,
  CallOutcome,
  ComplianceDecision,
  ConsentRecord,
  CRMConnection,
  DndEntry,
  EligibilityResult,
  FunnelSummary,
  Handoff,
  ImportError,
  ImportStatus,
  Integration,
  Job,
  KnowledgeItem,
  Lead,
  LeadCreate,
  LeadList,
  LeakageReport,
  Paginated,
  RecommendationAnalytics,
  RoiSummary,
  VoiceLeadProfile,
  VoiceStats,
  WorkflowFollowup,
  WorkflowRule,
  WorkflowRun,
} from "@/types/api";
import { toQueryString } from "@/lib/utils";

type Query = Record<string, string | number | boolean | null | undefined>;

/* ------------------------------------------------------------------ */
/* Workspace                                                           */
/* ------------------------------------------------------------------ */

export const getBusiness = () => apiRequest<Business>("/voice/business");

export const updateBusiness = (payload: BusinessUpdate) =>
  apiRequest<Business>("/voice/business", { method: "PATCH", body: payload });

/* ------------------------------------------------------------------ */
/* Leads                                                               */
/* ------------------------------------------------------------------ */

export const listLeads = (params: Query) =>
  apiRequest<LeadList>(`/leads${toQueryString(params)}`);

export const getLead = (leadId: string) =>
  apiRequest<Lead>(`/leads/${encodeURIComponent(leadId)}`);

export const createLead = (payload: LeadCreate) =>
  apiRequest<Lead>("/leads", { method: "POST", body: payload });

export const listVoiceLeads = (params: Query = {}) =>
  apiRequest<Paginated<VoiceLeadProfile>>(
    `/voice/leads${toQueryString(params)}`,
  );

export const getVoiceLead = (leadId: string) =>
  apiRequest<VoiceLeadProfile>(`/voice/leads/${encodeURIComponent(leadId)}`);

export const updateVoiceLeadProfile = (
  leadId: string,
  payload: {
    preferred_language?: string | null;
    timezone?: string | null;
    do_not_call?: boolean;
    notes?: string | null;
  },
) =>
  apiRequest<VoiceLeadProfile>(
    `/voice/leads/${encodeURIComponent(leadId)}/profile`,
    { method: "PUT", body: payload },
  );

export const getConsent = (leadId: string) =>
  apiRequest<ConsentRecord[]>(
    `/voice/leads/${encodeURIComponent(leadId)}/consent`,
  );

export const recordConsent = (
  leadId: string,
  payload: {
    status: string;
    channel: string;
    phone?: string | null;
    source?: string | null;
    granted_at?: string | null;
    expires_at?: string | null;
    revoked_at?: string | null;
    evidence?: Record<string, unknown>;
  },
) =>
  apiRequest<ConsentRecord>(
    `/voice/leads/${encodeURIComponent(leadId)}/consent`,
    { method: "PUT", body: payload },
  );

export const listDnd = (params: Query = {}) =>
  apiRequest<Paginated<DndEntry>>(`/voice/dnd${toQueryString(params)}`);

export const addDnd = (payload: {
  phone: string;
  scope?: string;
  reason?: string | null;
  source?: string | null;
}) => apiRequest<DndEntry>("/voice/dnd", { method: "POST", body: payload });

export const deactivateDnd = (entryId: string) =>
  apiRequest<DndEntry>(`/voice/dnd/${encodeURIComponent(entryId)}`, {
    method: "DELETE",
  });

/* ------------------------------------------------------------------ */
/* Imports                                                             */
/* ------------------------------------------------------------------ */

export const uploadImport = (formData: FormData) =>
  apiRequest<ImportStatus>("/leads/import", { method: "POST", formData });

export const getImport = (importId: string) =>
  apiRequest<ImportStatus>(`/imports/${encodeURIComponent(importId)}`);

export const getImportErrors = (importId: string, params: Query = {}) =>
  apiRequest<Paginated<ImportError> & { total: number; page: number; page_size: number }>(
    `/imports/${encodeURIComponent(importId)}/errors${toQueryString(params)}`,
  );

/* ------------------------------------------------------------------ */
/* Calls                                                               */
/* ------------------------------------------------------------------ */

export const listCalls = (params: Query = {}) =>
  apiRequest<Paginated<Call>>(`/voice/calls${toQueryString(params)}`);

export const getCall = (callId: string) =>
  apiRequest<Call>(`/voice/calls/${encodeURIComponent(callId)}`);

export const getCallEvents = (callId: string, params: Query = {}) =>
  apiRequest<Paginated<CallEvent>>(
    `/voice/calls/${encodeURIComponent(callId)}/events${toQueryString(params)}`,
  );

export const getCallOutcome = (callId: string) =>
  apiRequest<CallOutcome>(`/voice/calls/${encodeURIComponent(callId)}/outcome`);

export const initiateCall = (payload: {
  lead_id: string;
  language?: string | null;
  scheduled_at?: string | null;
  from_number?: string | null;
  agent_id?: string | null;
  extra_context?: Record<string, unknown>;
  dry_run?: boolean;
}) =>
  apiRequest<CallInitiationResponse>("/voice/calls", {
    method: "POST",
    body: { ...payload, extra_context: payload.extra_context ?? {} },
  });

export const refreshCall = (callId: string) =>
  apiRequest<{ call: Call; refreshed: boolean; message: string }>(
    `/voice/calls/${encodeURIComponent(callId)}/refresh`,
    { method: "POST" },
  );

export const cancelCall = (callId: string) =>
  apiRequest<Call>(`/voice/calls/${encodeURIComponent(callId)}/cancel`, {
    method: "POST",
  });

export const queuePostcall = (callId: string) =>
  apiRequest<Record<string, unknown>>(
    `/voice/calls/${encodeURIComponent(callId)}/postcall`,
    { method: "POST" },
  );

export const syncCallStatuses = () =>
  apiRequest<Record<string, unknown>>("/voice/ops/sync-calls", {
    method: "POST",
  });

/* ------------------------------------------------------------------ */
/* Handoffs / follow-up tasks                                          */
/* ------------------------------------------------------------------ */

export const listHandoffs = (params: Query = {}) =>
  apiRequest<Paginated<Handoff>>(`/voice/handoffs${toQueryString(params)}`);

export const getHandoff = (handoffId: string) =>
  apiRequest<Handoff>(`/voice/handoffs/${encodeURIComponent(handoffId)}`);

export const requestCallback = (payload: {
  lead_id?: string | null;
  call_id?: string | null;
  reason?: string | null;
  briefing?: string | null;
  target_number?: string | null;
  requested_by?: string;
}) =>
  apiRequest<Handoff>("/voice/handoffs", {
    method: "POST",
    body: { requested_by: "dashboard", ...payload },
  });

export const updateHandoff = (
  handoffId: string,
  payload: {
    status?: string | null;
    outcome?: string | null;
    note?: string | null;
  },
) =>
  apiRequest<Handoff>(`/voice/handoffs/${encodeURIComponent(handoffId)}`, {
    method: "PATCH",
    body: payload,
  });

export const listFollowups = (params: Query = {}) =>
  apiRequest<Paginated<WorkflowFollowup>>(
    `/workflow/followups${toQueryString(params)}`,
  );

export const completeFollowup = (
  taskId: string,
  payload: { outcome?: string; notes?: string },
) =>
  apiRequest<{ id: string; status: string; outcome: string | null }>(
    `/workflow/followups/${encodeURIComponent(taskId)}/complete`,
    { method: "POST", body: payload },
  );

/* ------------------------------------------------------------------ */
/* Knowledge base                                                      */
/* ------------------------------------------------------------------ */

export const listKnowledge = (params: Query = {}) =>
  apiRequest<Paginated<KnowledgeItem> & { total: number }>(
    `/voice/knowledge${toQueryString(params)}`,
  );

export const createKnowledge = (payload: {
  category: string;
  title: string;
  content: string;
  tags?: string[];
  source_reference?: string;
  is_active?: boolean;
  priority?: number;
}) =>
  apiRequest<KnowledgeItem>("/voice/knowledge", {
    method: "POST",
    body: payload,
  });

export const updateKnowledge = (
  itemId: string,
  payload: Partial<{
    category: string;
    title: string;
    content: string;
    tags: string[];
    source_reference: string;
    is_active: boolean;
    priority: number;
  }>,
) =>
  apiRequest<KnowledgeItem>(`/voice/knowledge/${encodeURIComponent(itemId)}`, {
    method: "PUT",
    body: payload,
  });

export const deleteKnowledge = (itemId: string) =>
  apiRequest<void>(`/voice/knowledge/${encodeURIComponent(itemId)}`, {
    method: "DELETE",
  });

export const getCustomFunctions = () =>
  apiRequest<Record<string, unknown>>("/voice/bolna/custom-functions");

/* ------------------------------------------------------------------ */
/* Operations                                                          */
/* ------------------------------------------------------------------ */

export const getVoiceStats = () => apiRequest<VoiceStats>("/voice/ops/stats");

export const listAudit = (params: Query = {}) =>
  apiRequest<Paginated<AuditLog>>(`/voice/ops/audit${toQueryString(params)}`);

export const listComplianceDecisions = (params: Query = {}) =>
  apiRequest<Paginated<ComplianceDecision>>(
    `/voice/ops/compliance-decisions${toQueryString(params)}`,
  );

export const listJobs = (params: Query = {}) =>
  apiRequest<Paginated<Job>>(`/voice/ops/jobs${toQueryString(params)}`);

export const retryJob = (jobId: string) =>
  apiRequest<Job>(`/voice/ops/jobs/${encodeURIComponent(jobId)}/retry`, {
    method: "POST",
  });

/* ------------------------------------------------------------------ */
/* CRM + integrations                                                  */
/* ------------------------------------------------------------------ */

export const listCrmConnections = () =>
  apiRequest<CRMConnection[]>("/crm/connections");

export const saveCrmConnection = (payload: {
  connection_id?: string;
  name: string;
  crm_base_url: string;
  crm_x_api_key: string;
  crm_timeout_seconds?: number;
}) =>
  apiRequest<{
    connection: CRMConnection;
    verified: boolean;
    message: string;
  }>("/crm/connections", { method: "POST", body: payload });

export const listIntegrations = () =>
  apiRequest<Paginated<Integration>>("/workflow/integrations");

export const createIntegration = (payload: {
  provider: string;
  name: string;
  config?: Record<string, unknown>;
  capabilities?: string[];
}) =>
  apiRequest<Record<string, unknown>>("/workflow/integrations", {
    method: "POST",
    body: payload,
  });

/* ------------------------------------------------------------------ */
/* Workflow analytics + rules                                          */
/* ------------------------------------------------------------------ */

export const getFunnel = (params: Query = {}) =>
  apiRequest<FunnelSummary>(`/workflow/analytics/funnel${toQueryString(params)}`);

export const getLeakage = (params: Query = {}) =>
  apiRequest<LeakageReport>(
    `/workflow/analytics/leakage${toQueryString(params)}`,
  );

export const getRoi = (params: Query = {}) =>
  apiRequest<RoiSummary>(`/workflow/analytics/roi${toQueryString(params)}`);

export const getRecommendationAnalytics = () =>
  apiRequest<RecommendationAnalytics>("/recommendations/analytics");

export const listWorkflowRules = () =>
  apiRequest<WorkflowRule[]>("/workflow/rules");

export const upsertWorkflowRule = (payload: {
  key: string;
  name: string;
  value: unknown;
  description?: string | null;
  category?: string;
}) =>
  apiRequest<WorkflowRule>("/workflow/rules", { method: "PUT", body: payload });

export const listWorkflowRuns = (params: Query = {}) =>
  apiRequest<Paginated<WorkflowRun>>(`/workflow/runs${toQueryString(params)}`);

export const checkEligibility = (payload: {
  lead_id: string;
  channel?: string;
  case_id?: string | null;
  persist?: boolean;
}) =>
  apiRequest<EligibilityResult>("/workflow/eligibility", {
    method: "POST",
    body: payload,
  });
