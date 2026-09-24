export type Role = "admin" | "operator" | "reviewer" | "viewer";

export type Permission =
  | "view_hospital"
  | "capture_source"
  | "manage_monitoring"
  | "manage_sources"
  | "verify_change"
  | "review_reconciliation"
  | "generate_report"
  | "submit_report"
  | "manage_users"
  | "manage_configuration"
  | "view_quality";

export interface UserPublic {
  user_id: string;
  username: string;
  role: Role;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  hospital_ids: string[];
}

export interface LoginResponse {
  access_token: string;
  expires_in: number;
  user: UserPublic;
  mode: string;
  development: boolean;
}

export interface MeResponse {
  user_id: string;
  username: string;
  role: Role;
  permissions: Permission[];
  auth_status: string;
  development: boolean;
  hospital_ids: string[];
  correlation_id?: string;
}

export interface DatasetSummary {
  dataset_id: string;
  hospital_ref: string;
  hospital_name?: string | null;
  hospital_key?: string;
  hospital_id?: string;
  source_type: string;
  provider?: string | null;
  created_at: string;
  record_count?: number;
  quality_records?: number;
}

export interface HospitalSummary {
  hospital_id: string;
  name?: string | null;
  created_at: string;
  dataset_count?: number;
  snapshot_count?: number;
  source_count?: number;
}

export interface AuditEvent {
  audit_event_id: string;
  timestamp?: string;
  actor_id: string;
  actor_role: string;
  action: string;
  resource_type: string;
  resource_id: string;
  hospital_id?: string;
  outcome: string;
  correlation_id?: string;
  metadata?: Record<string, unknown>;
}

export interface SourceRow {
  source_type: string;
  label?: string;
  simulated?: boolean;
  requires_authorization?: boolean;
  authorization_status?: string;
  connector?: string;
}

export interface ConfiguredSource {
  name: string;
  source_type: string;
  hospital_ref?: string;
  base_url?: string;
  provider?: string;
  authorization_status: string;
  credential_ref?: string | null;
  allowed_hosts?: string[];
}

export interface MonitoringPolicy {
  policy_id: string;
  hospital_id: string;
  frequency: string;
  source_type: string;
  target_ref?: string;
  enabled: boolean;
  status?: string;
  next_run_at?: string | null;
  last_run_at?: string | null;
  last_success_at?: string | null;
  last_failure_at?: string | null;
  consecutive_failures?: number;
  last_error?: string | null;
  last_orchestration_run_id?: string | null;
  last_change_set_status?: string | null;
}

export interface VerificationJobRow {
  job: {
    verification_job_id: string;
    hospital_id: string;
    status: string;
    target_source_type: string;
    change_set_id: string;
    scheduled_for?: string;
  };
  result?: {
    verification_result_id: string;
    status: string;
    verified_count?: number;
    mismatch_count?: number;
    unverified_count?: number;
    explanation?: string | null;
  } | null;
}

export interface ReconciliationCaseJson {
  reconciliation_case_id: string;
  hospital_id: string;
  status: string;
  trigger?: string;
  created_at?: string;
  updated_at?: string;
  resolution?: string | null;
}

export interface ReconciliationCaseListItem {
  case: ReconciliationCaseJson;
  events: unknown[];
}

export interface ReportJson {
  report_id: string;
  hospital_id: string;
  report_type: string;
  status: string;
  format: string;
  generated_at?: string;
  content?: unknown;
}

export interface QualityReport {
  overall_score?: number | null;
  note?: string;
  [key: string]: unknown;
}

export interface QueryFilter {
  field: string;
  operator: string;
  value?: unknown;
}

export interface Query {
  filters?: QueryFilter[];
  hospital_ids?: string[];
  text?: string | null;
  limit?: number;
  offset?: number;
}

export interface QueryResult {
  total: number;
  limit: number;
  offset: number;
  matches: Array<Record<string, unknown>>;
  filters?: QueryFilter[];
}

export interface ComparisonRequest {
  hospital_ids: string[];
  fields?: string[];
}

export interface ComparisonResult {
  hospital_ids?: string[];
  hospitals?: Array<Record<string, unknown>>;
  fields?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface ApiErrorBody {
  error?: string;
  detail?: string;
}
