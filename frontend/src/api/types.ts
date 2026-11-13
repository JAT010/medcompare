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
