/** Types mirroring the MedCompare FastAPI contract (see comparison/schemas.py). */

export type ComparisonScope =
  | "standard_12"
  | "standard_12_plus_custom"
  | "custom_only";

export type EvidenceSource =
  | "medcompare_server"
  | "public_research"
  | "website_crawl";

export type EvidenceSourceValue = EvidenceSource | "user_provided";

export type EvidenceStatus =
  | "available"
  | "unavailable"
  | "conflicting"
  | "outdated";

export type CriterionOrigin = "standard" | "custom";

export interface CriterionDefinition {
  key: string;
  label: string;
  origin: CriterionOrigin;
  evidence_needed: string;
  matched_source: string | null;
  overlaps_standard: string | null;
  original_text: string | null;
}

export interface InterpretResponse {
  criteria: CriterionDefinition[];
  clarification_questions: string[];
  interpreter: string;
  interpretation_note: string;
}

export interface EvidenceItem {
  criterion_key: string;
  value: string;
  source: EvidenceSourceValue;
  source_name: string | null;
  source_url: string | null;
  retrieved_at: string | null;
  published_at: string | null;
  status: EvidenceStatus;
  confidence: number | null;
  note: string | null;
}

export interface Facility {
  id: string;
  name: string;
  organization_id: string | null;
  organization_name: string | null;
  canonical_url: string | null;
  website_domain: string | null;
  address: string | null;
  city: string | null;
  state: string | null;
  country: string | null;
  postal_code: string | null;
  status: string;
}

export interface FacilityListResponse {
  items: Facility[];
  identity_note: string;
  limit: number;
  offset: number;
}

export interface RegisteredHospitalInput {
  type: "registered";
  facility_id: string;
  crawl_run_id?: string | null;
  // Legacy/untrusted compatibility only; server ignores for registered facilities.
  evidence?: EvidenceItem[];
}

export interface UnregisteredHospitalInput {
  type: "unregistered";
  comparison_subject_id?: string | null;
  crawl_run_id?: string | null;
  name: string;
  location: string | null;
  website_url: string | null;
  // Legacy/untrusted compatibility only unless replaced by validated crawl_run_id.
  evidence?: EvidenceItem[];
}

export type HospitalInput = RegisteredHospitalInput | UnregisteredHospitalInput;

export interface ComparisonRequest {
  scope: ComparisonScope;
  source: EvidenceSource;
  criteria?: CriterionDefinition[];
  hospitals: HospitalInput[];
}

export interface ComparisonColumn {
  column_id: string;
  kind: "registered" | "unregistered";
  name: string;
  location: string | null;
  organization_name: string | null;
  website_url: string | null;
  identity_note: string | null;
  crawl_run_id?: string | null;
  comparison_subject_id?: string | null;
}

export interface ComparisonCell {
  status: EvidenceStatus;
  value: string | null;
  source: EvidenceSourceValue | null;
  source_name: string | null;
  source_url: string | null;
  retrieved_at: string | null;
  published_at: string | null;
  confidence: number | null;
  explanation: string;
}

export interface ComparisonRow {
  criterion: CriterionDefinition;
  cells: ComparisonCell[];
}

export interface SourceStatus {
  source: EvidenceSource;
  status: "active" | "not_configured" | "unsupported";
  detail: string;
  required_integration: string | null;
}

export interface ColumnCoverage {
  column_id: string;
  criteria_total: number;
  criteria_with_evidence: number;
  coverage_ratio: number;
}

export interface ComparisonResponse {
  scope: ComparisonScope;
  source: EvidenceSource;
  criteria: CriterionDefinition[];
  columns: ComparisonColumn[];
  rows: ComparisonRow[];
  source_statuses: SourceStatus[];
  coverage: ColumnCoverage[];
  warnings: string[];
  disclaimer: string;
}

export type AuditStatus =
  | "consistent"
  | "differs"
  | "missing_from_website"
  | "missing_from_internal_record"
  | "potentially_outdated"
  | "unverifiable";

export interface AuditSourceValue {
  source: "internal_record" | "website_crawl";
  source_name: string;
  value: string;
  source_url: string | null;
  retrieved_at: string | null;
  confidence: number | null;
  note: string | null;
}

export interface AuditFinding {
  field_key: string;
  label: string;
  status: AuditStatus;
  internal: AuditSourceValue | null;
  website: AuditSourceValue | null;
  explanation: string;
  requires_authorized_integration: boolean;
}

export interface AuditSummary {
  findings_total: number;
  consistent: number;
  differs: number;
  missing_from_website: number;
  missing_from_internal_record: number;
  potentially_outdated: number;
  unverifiable: number;
}

export interface InternalRecordSummary {
  facility_id: string;
  name: string;
  organization_name: string | null;
  canonical_url: string | null;
  address: string | null;
  city: string | null;
  state: string | null;
  postal_code: string | null;
  country: string | null;
  updated_at: string | null;
}

export interface WebsiteSnapshotSummary {
  crawl_run_id: string;
  status: CrawlRunStatusValue;
  target_url: string;
  requested_url: string;
  hospital_name: string | null;
  pages_fetched: number;
  evidence_count: number;
  usable_for_comparison: boolean;
  requested_at: string | null;
  completed_at: string | null;
  retrieved_at: string | null;
}

export interface ConsistencyAuditRequest {
  facility_id: string;
  crawl_run_id?: string | null;
}

export interface ConsistencyAuditResponse {
  facility_id: string;
  facility_name: string;
  organization_name: string | null;
  branch_identity_note: string;
  internal_record: InternalRecordSummary;
  website_snapshot: WebsiteSnapshotSummary | null;
  findings: AuditFinding[];
  summary: AuditSummary;
  warnings: string[];
  disclaimer: string;
}

export interface CapabilityStatus {
  key: string;
  label: string;
  available: boolean;
  detail: string;
  implementation: string | null;
  required_integration: string | null;
}

export interface CapabilityReport {
  capabilities: CapabilityStatus[];
}

export type CrawlRunStatusValue =
  | "completed"
  | "completed_with_warnings"
  | "failed"
  | "timed_out"
  | "empty";

export interface CrawlRequest {
  url: string;
  facility_id?: string | null;
  comparison_subject_id?: string | null;
  name?: string | null;
  location?: string | null;
  max_pages?: number;
}

export interface CrawlResponse {
  status: "completed" | "failed";
  execution: "synchronous";
  job_store_available: boolean;
  target_url: string;
  pages_fetched: number;
  hospital_name: string | null;
  evidence: EvidenceItem[];
  criteria_without_evidence: string[];
  warnings: string[];
  crawl_run_id?: string | null;
  facility_id?: string | null;
  comparison_subject_id?: string | null;
  crawl_status?: CrawlRunStatusValue | null;
  evidence_count?: number | null;
  completed_at?: string | null;
  retrieved_at?: string | null;
}

export interface CrawlRunDetail {
  crawl_run_id: string;
  facility_id: string | null;
  comparison_subject_id: string | null;
  subject_name: string | null;
  subject_location: string | null;
  subject_website_url: string | null;
  status: CrawlRunStatusValue;
  execution_mode: "synchronous";
  target_url: string;
  requested_url: string;
  hospital_name: string | null;
  pages_fetched: number;
  evidence_count: number;
  evidence: EvidenceItem[];
  criteria_without_evidence: string[];
  warnings: string[];
  usable_for_comparison: boolean;
  requested_at: string | null;
  completed_at: string | null;
  retrieved_at: string | null;
  failure_code: string | null;
  failure_message: string | null;
}

export type IntegrationAuthMode = "none" | "api_key" | "bearer_token";
export type IntegrationStatus =
  | "not_configured"
  | "configured"
  | "disabled"
  | "error";

export interface FacilityIntegration {
  id: string;
  facility_id: string;
  name: string;
  connector_type: string;
  base_url: string | null;
  auth_mode: IntegrationAuthMode;
  credential_env_ref: string | null;
  config_json: Record<string, unknown>;
  status: IntegrationStatus;
  enabled: boolean;
  simulated: boolean;
  created_at: string;
  updated_at: string;
}

export interface IntegrationListResponse {
  facility_id: string;
  items: FacilityIntegration[];
}

export interface IntegrationCreateRequest {
  name: string;
  connector_type: string;
  base_url?: string | null;
  auth_mode?: IntegrationAuthMode;
  credential_env_ref?: string | null;
  config_json?: Record<string, unknown>;
  enabled?: boolean;
}

export interface IntegrationUpdateRequest {
  name?: string;
  base_url?: string | null;
  auth_mode?: IntegrationAuthMode;
  credential_env_ref?: string | null;
  config_json?: Record<string, unknown>;
  enabled?: boolean;
  status?: IntegrationStatus;
}

export interface HospitalSnapshot {
  id: string;
  facility_id: string;
  integration_id: string | null;
  source_kind: string;
  version_number: number;
  content_hash: string;
  captured_at: string;
  captured_by: string;
  is_simulation: boolean;
  created_at: string;
  provenance_json?: Record<string, unknown> | null;
  payload_json?: Record<string, unknown> | null;
}

export interface SnapshotListResponse {
  facility_id: string;
  items: HospitalSnapshot[];
  limit: number;
  offset: number;
}

export type DetectedChangeType = "added" | "removed" | "modified";
export type ChangeSetStatusValue =
  | "initial_snapshot"
  | "no_change"
  | "changes_detected";

export interface DetectedChange {
  id: string;
  path: string;
  change_type: DetectedChangeType;
  entity_name: string;
  field: string;
  old_value_json: unknown;
  new_value_json: unknown;
  source_url: string | null;
  detected_at: string;
}

export interface ChangeSet {
  id: string;
  facility_id: string;
  integration_id: string | null;
  previous_snapshot_id: string | null;
  snapshot_id: string;
  status: ChangeSetStatusValue;
  added_count: number;
  removed_count: number;
  modified_count: number;
  detected_at: string;
}

export interface ChangeSetDetail extends ChangeSet {
  changes: DetectedChange[];
}

export interface ChangeSetListResponse {
  facility_id: string;
  items: ChangeSet[];
  limit: number;
  offset: number;
}

export type VerificationJobStatusValue =
  | "pending"
  | "running"
  | "verified"
  | "mismatch"
  | "insufficient_evidence"
  | "failed"
  | "cancelled";
export type VerificationTriggerValue =
  | "change_detected"
  | "manual"
  | "monitoring";

export interface VerificationResult {
  id: string;
  job_id: string;
  facility_id: string;
  hospital_snapshot_id: string | null;
  website_crawl_run_id: string | null;
  status: "match" | "mismatch" | "insufficient_evidence" | "error";
  summary: string | null;
  compared_at: string;
  evidence_json: Record<string, unknown>;
  provenance_json: Record<string, unknown>;
}

export interface VerificationEvidenceCheck {
  path: string;
  change_type: string;
  entity_name: string;
  field: string;
  status: "match" | "mismatch" | "insufficient_evidence";
  hospital_value: unknown;
  website_value: unknown;
  detail: string;
}

export interface VerificationJob {
  id: string;
  facility_id: string;
  integration_id: string | null;
  change_set_id: string | null;
  snapshot_id: string | null;
  target_ref: string;
  trigger_type: VerificationTriggerValue;
  status: VerificationJobStatusValue;
  attempt_count: number;
  max_attempts: number;
  scheduled_for: string;
  started_at: string | null;
  finished_at: string | null;
  last_error_code: string | null;
  last_error_message: string | null;
  created_at: string;
  result: VerificationResult | null;
}

export interface JobListResponse {
  facility_id: string;
  items: VerificationJob[];
  limit: number;
  offset: number;
}

export interface ManualJobResponse {
  verification_job: VerificationJob | null;
  created: boolean;
  reason: string | null;
}

export interface CaptureResponse {
  snapshot: HospitalSnapshot;
  change_set: ChangeSetDetail;
  verification_job: VerificationJob | null;
  job_created: boolean;
  job_skip_reason: string | null;
}

export interface MonitoringSchedule {
  id: string;
  facility_id: string;
  integration_id: string;
  name: string;
  interval_seconds: number;
  enabled: boolean;
  next_run_at: string | null;
  last_enqueued_at: string | null;
  last_status: string | null;
}

export interface ScheduleListResponse {
  facility_id: string;
  items: MonitoringSchedule[];
}

export interface ScheduleCreateRequest {
  name: string;
  integration_id: string;
  interval_seconds: number;
  enabled?: boolean;
  target_ref?: string | null;
}

export interface ScheduleUpdateRequest {
  name?: string;
  interval_seconds?: number;
  enabled?: boolean;
  target_ref?: string | null;
}

export interface MonitoringExecution {
  id: string;
  schedule_id: string;
  facility_id: string;
  job_id: string | null;
  status: "enqueued" | "skipped" | "error";
  reason: string | null;
  scheduled_for: string | null;
  executed_at: string;
}

export interface ExecutionListResponse {
  facility_id: string;
  items: MonitoringExecution[];
  limit: number;
  offset: number;
}

export type ReconciliationResolution =
  | "hospital_source_confirmed"
  | "website_confirmed"
  | "source_disagreement"
  | "insufficient_evidence"
  | "false_positive";
export type ReconciliationCaseStatusValue =
  | "open"
  | "under_review"
  | "resolved"
  | "dismissed";

export interface ReconciliationCase {
  id: string;
  facility_id: string;
  verification_result_id: string | null;
  detected_change_id: string | null;
  status: ReconciliationCaseStatusValue;
  resolution: ReconciliationResolution | null;
  summary: string | null;
  notes: string | null;
  opened_at: string;
  resolved_at: string | null;
  resolved_by_label: string | null;
  provenance_json: Record<string, unknown>;
}

export interface ReconciliationEvent {
  id: string;
  case_id: string;
  from_status: ReconciliationCaseStatusValue | null;
  to_status: ReconciliationCaseStatusValue;
  actor_label: string | null;
  note: string | null;
  occurred_at: string;
}

export interface ReconciliationCaseDetail extends ReconciliationCase {
  events: ReconciliationEvent[];
}

export interface CaseListResponse {
  facility_id: string;
  items: ReconciliationCase[];
  limit: number;
  offset: number;
}

export interface TransitionCaseRequest {
  to_status: ReconciliationCaseStatusValue;
  resolution?: ReconciliationResolution | null;
  note?: string | null;
  actor_label?: string | null;
}
