export type ApiErrorBody = {
  error?: {
    code?: string;
    message?: string;
    details?: unknown[];
  };
  detail?: string | unknown;
};

export type Paginated<T> = {
  items: T[];
  business_id?: string;
  total?: number;
  limit?: number;
  offset?: number;
  page?: number;
  page_size?: number;
};

export type Business = {
  business_id: string;
  name: string;
  timezone: string;
  default_language: string;
  supported_languages: string[];
  calling_window_start: number;
  calling_window_end: number;
  calling_days: number[];
  max_calls_per_lead_per_day: number;
  max_calls_per_business_per_day: number;
  max_concurrent_calls: number;
  require_consent: boolean;
  dnd_enabled: boolean;
  bolna_agent_id: string | null;
  agent_instructions: string | null;
  qualification_questions: Array<Record<string, unknown>>;
  warm_transfer_enabled: boolean;
  warm_transfer_number: string | null;
  handoff_target_number: string | null;
  is_active: boolean;
  outbound_calls_enabled: boolean;
  provider_configured: boolean;
  call_quotas_disabled: boolean;
  created_at: string | null;
  updated_at: string | null;
};

export type BusinessUpdate = Partial<
  Pick<
    Business,
    | "name"
    | "timezone"
    | "default_language"
    | "supported_languages"
    | "calling_window_start"
    | "calling_window_end"
    | "calling_days"
    | "max_calls_per_lead_per_day"
    | "max_calls_per_business_per_day"
    | "max_concurrent_calls"
    | "require_consent"
    | "dnd_enabled"
    | "bolna_agent_id"
    | "agent_instructions"
    | "qualification_questions"
    | "warm_transfer_enabled"
    | "warm_transfer_number"
    | "handoff_target_number"
    | "is_active"
  >
>;

export type VoiceStats = {
  business_id: string;
  calls_total: number;
  calls_by_status: Record<string, number>;
  calls_today: number;
  blocked_today: number;
  handoffs_open: number;
  consent_granted: number;
  consent_missing: number;
  dnd_entries: number;
  jobs_pending: number;
  jobs_failed: number;
  total_cost: number;
  currency: string;
};

export type Call = {
  id: string;
  business_id: string;
  lead_id: string;
  provider: string;
  provider_call_id: string | null;
  status: string;
  blocked_reason: string | null;
  error_message: string | null;
  to_number: string | null;
  from_number: string | null;
  language: string | null;
  direction: string;
  scheduled_at: string | null;
  initiated_at: string | null;
  connected_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  cost: number | null;
  currency: string;
  recording_url: string | null;
  transcript: string | null;
  summary: string | null;
  compliance_decision_id: string | null;
  simulated: boolean;
  metadata: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
};

export type CallEvent = {
  id: string;
  call_id: string | null;
  provider: string;
  event_key: string;
  event_type: string;
  provider_status: string | null;
  provider_event_id: string | null;
  payload: Record<string, unknown>;
  signature_valid: boolean | null;
  processed: boolean;
  processing_error: string | null;
  received_at: string | null;
};

export type CallOutcome = {
  id: string;
  business_id: string;
  call_id: string;
  lead_id: string;
  summary: string;
  disposition: string;
  qualified: boolean | null;
  qualification_score: number | null;
  qualification_answers: Record<string, unknown>;
  refusal_category: string | null;
  refusal_reason: string | null;
  follow_up_actions: string[];
  handoff_required: boolean;
  handoff_briefing: string | null;
  language_detected: string | null;
  model: string | null;
  engine: string;
  processed_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type CallInitiationResponse = {
  call: Call;
  compliance_decision_id: string;
  compliance_decision: string;
  compliance_checks?: Array<Record<string, unknown>>;
  simulated: boolean;
  message: string;
};

export type Lead = {
  id: string;
  business_id: string;
  name: string;
  phone: string;
  email: string | null;
  source: string;
  language: string | null;
  deal_value: string | null;
  last_contact_at: string | null;
  custom_fields: Record<string, unknown>;
  external_id: string | null;
  created_at: string;
};

export type LeadList = Paginated<Lead> & {
  total: number;
  page: number;
  page_size: number;
};

export type LeadCreate = {
  name: string;
  phone: string;
  source: string;
  email?: string | null;
  language?: string | null;
  deal_value?: string | null;
  last_contact_at?: string | null;
  custom_fields?: Record<string, unknown>;
  external_id?: string | null;
};

export type ConsentRecord = {
  id: string;
  business_id: string;
  lead_id: string;
  phone: string | null;
  channel: string;
  status: string;
  source: string | null;
  granted_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  evidence: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
};

export type DndEntry = {
  id: string;
  scope: string;
  business_id: string;
  phone: string;
  reason: string | null;
  source: string | null;
  active: boolean;
  expires_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type VoiceLeadProfile = {
  lead_id: string;
  business_id: string;
  lead_name: string;
  phone: string;
  preferred_language: string | null;
  timezone: string | null;
  do_not_call: boolean;
  notes: string | null;
  call_attempts: number;
  last_call_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  consent: ConsentRecord | null;
  dnd_active: boolean;
};

export type Handoff = {
  id: string;
  business_id: string;
  call_id: string | null;
  lead_id: string | null;
  lead_name: string | null;
  kind: string;
  status: string;
  reason: string | null;
  reason_code: string | null;
  requested_by: string;
  target_number: string | null;
  briefing: string | null;
  context: Record<string, unknown>;
  outcome: string | null;
  outcome_note: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type KnowledgeItem = {
  id: string;
  business_id: string;
  category: string;
  title: string;
  content: string;
  tags: string[];
  source_reference: string;
  is_active: boolean;
  priority: number;
  extra_metadata: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
};

export type AuditLog = {
  id: string;
  business_id: string;
  actor: string;
  action: string;
  entity_type: string;
  entity_id: string;
  decision: string | null;
  reason_code: string | null;
  reason: string | null;
  metadata: Record<string, unknown>;
  created_at: string | null;
};

export type ComplianceDecision = {
  id: string;
  business_id: string;
  lead_id: string;
  call_id: string | null;
  decision: string;
  reason_code: string;
  reason: string | null;
  checks: Array<Record<string, unknown>>;
  metadata: Record<string, unknown>;
  created_at: string | null;
};

export type Job = {
  id: string;
  type: string;
  business_id: string | null;
  payload: Record<string, unknown>;
  status: string;
  attempts: number;
  max_attempts: number;
  last_error: string | null;
  result: Record<string, unknown> | null;
  dedupe_key: string | null;
  scheduled_at: string | null;
  locked_at: string | null;
  locked_by: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type ImportStatus = {
  import_id: string;
  status: string;
  total_rows: number;
  created: number;
  duplicates: number;
  rejected: number;
  errors_url: string;
  source_type: string;
  original_filename: string | null;
  mapping: Record<string, unknown>;
  error_message: string | null;
  created_at: string | null;
  started_at: string | null;
  completed_at: string | null;
};

export type ImportError = {
  row_number: number;
  field: string | null;
  error_code: string;
  message: string;
  raw_data: Record<string, unknown>;
};

export type CRMConnection = {
  connection_id: string;
  name: string;
  is_active: boolean;
  crm_base_url: string;
  has_api_key: boolean;
  crm_timeout_seconds: number;
  created_at: string | null;
  updated_at: string | null;
};

export type Integration = {
  id: string;
  provider: string;
  name: string;
  status: string;
  config: Record<string, unknown>;
  capabilities: string[];
  last_checked_at: string | null;
  last_error: string | null;
};

export type WorkflowFollowup = {
  id: string;
  lead_id: string;
  case_id: string | null;
  workflow_run_id: string | null;
  channel: string;
  reason: string | null;
  due_at: string | null;
  status: string;
  priority: string | null;
  assigned_to: string | null;
  outcome: string | null;
  completed_at: string | null;
};

export type WorkflowRule = {
  key: string;
  name: string;
  description: string | null;
  category: string;
  value: unknown;
  is_active: boolean;
  current_version: number;
};

export type WorkflowRun = {
  id: string;
  business_id: string;
  case_id: string | null;
  lead_id: string | null;
  workflow_type: string;
  status: string;
  dry_run: boolean;
  approval_required: boolean;
  approved_by: string | null;
  approved_at: string | null;
  idempotency_key: string | null;
  input: Record<string, unknown>;
  result: Record<string, unknown> | null;
  current_step: string | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  allowed_transitions: string[];
  actions?: Array<Record<string, unknown>>;
};

export type EligibilityResult = {
  business_id: string;
  lead_id: string;
  channel: string;
  eligible: boolean;
  reason_code?: string;
  reason?: string;
  checks?: Array<Record<string, unknown>>;
  [key: string]: unknown;
};

export type FunnelSummary = {
  business_id: string;
  window_days: number;
  calls: number;
  connected: number;
  completed: number;
  blocked: number;
  outcomes: number;
  qualified: number;
  refused: number;
  connect_rate_percent: number | null;
  qualification_rate_percent: number | null;
  min_sample_per_window: number;
  insufficient_data: boolean;
  note?: string;
};

export type LeakageReport = {
  business_id: string;
  window_days: number;
  analysed_calls: number;
  flagged_calls: number;
  leakage_percent: number | null;
  flags: Array<{ flag: string; count: number }>;
  min_calls_for_leakage: number;
  insufficient_data: boolean;
  note?: string;
};

export type RoiSummary = {
  business_id: string;
  window_days: number;
  total_costs: number;
  verified_revenue: number;
  unverified_pipeline: number;
  roi: number | null;
  cost_rows: number;
  revenue_rows: number;
  verified_revenue_rows: number;
  note?: string;
};

export type RecommendationAnalytics = {
  business_id: string;
  total_recommendations: number;
  new_recommendations: number;
  p1_count: number;
  p2_count: number;
  p3_count: number;
  implemented_count?: number;
  evaluated_count?: number;
  improved_count?: number;
  [key: string]: unknown;
};
