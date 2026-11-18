/**
 * Typed fetch client for the MedCompare API.
 *
 * Errors are surfaced honestly: a thrown ApiError keeps the HTTP status and the
 * server's structured detail, so the UI can show what is actually missing
 * (for example the search integration required for public internet research)
 * instead of inventing a friendlier but false message.
 */

import type {
  CapabilityReport,
  CaseListResponse,
  ChangeSetDetail,
  ChangeSetListResponse,
  ComparisonRequest,
  ComparisonResponse,
  ConsistencyAuditRequest,
  ConsistencyAuditResponse,
  CrawlRequest,
  CrawlResponse,
  CrawlRunDetail,
  ExecutionListResponse,
  FacilityIntegration,
  FacilityListResponse,
  IntegrationCreateRequest,
  IntegrationListResponse,
  IntegrationUpdateRequest,
  InterpretResponse,
  JobListResponse,
  ManualJobResponse,
  MonitoringSchedule,
  ReconciliationCaseDetail,
  CaptureResponse,
  ScheduleListResponse,
  ScheduleCreateRequest,
  ScheduleUpdateRequest,
  SnapshotListResponse,
  TransitionCaseRequest,
} from "./types";
import {
  demoCapabilitiesData,
  demoComparisonData,
  demoConsistencyAuditData,
  demoCrawlData,
  demoCrawlRunData,
  demoFacilitiesData,
  demoInterpretData,
  isDemoMode,
} from "./demo";

const configuredBase =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export const API_BASE_URL = `${String(configuredBase).replace(/\/+$/, "")}/api/v1`;

interface ServerIssue {
  msg?: string;
}

function messageFromDetail(status: number, detail: unknown): string {
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  if (Array.isArray(detail)) {
    const messages = detail
      .map((issue: ServerIssue | null) =>
        issue && typeof issue.msg === "string" ? issue.msg : "",
      )
      .filter(Boolean);
    if (messages.length > 0) {
      return messages.join(" ");
    }
  }
  if (detail !== null && typeof detail === "object" && "message" in detail) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) {
      return message;
    }
  }
  if (status === 501) {
    return "This capability is not implemented in this deployment.";
  }
  return `Request failed with status ${status}.`;
}

function integrationFromDetail(detail: unknown): string | null {
  if (detail !== null && typeof detail === "object" && !Array.isArray(detail)) {
    const integration = (detail as { required_integration?: unknown })
      .required_integration;
    if (typeof integration === "string" && integration.trim()) {
      return integration;
    }
  }
  return null;
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  readonly errorCode: string | null;
  readonly requiredIntegration: string | null;

  constructor(status: number, detail: unknown) {
    super(messageFromDetail(status, detail));
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.errorCode =
      detail !== null &&
      typeof detail === "object" &&
      !Array.isArray(detail) &&
      typeof (detail as { error?: unknown }).error === "string"
        ? (detail as { error: string }).error
        : null;
    this.requiredIntegration = integrationFromDetail(detail);
  }
}

export function toMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "Something went wrong while contacting the MedCompare server.";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    let detail: unknown = null;
    try {
      const body = (await response.json()) as { detail?: unknown } | null;
      detail = body?.detail ?? body ?? null;
    } catch {
      detail = null;
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export function getCapabilities(): Promise<CapabilityReport> {
  if (isDemoMode()) return Promise.resolve(demoCapabilitiesData);
  return request<CapabilityReport>("/comparison/capabilities");
}

export function searchFacilities(query: string): Promise<FacilityListResponse> {
  if (isDemoMode()) {
    const data = demoFacilitiesData;
    const q = query.trim().toLowerCase();
    if (!q) return Promise.resolve(data);
    return Promise.resolve({ ...data, items: data.items.filter((f) => f.name.toLowerCase().includes(q)) });
  }
  const params = new URLSearchParams();
  const trimmed = query.trim();
  if (trimmed) {
    params.set("q", trimmed);
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return request<FacilityListResponse>(`/facilities${suffix}`);
}

export function interpretPrompt(prompt: string): Promise<InterpretResponse> {
  if (isDemoMode()) return Promise.resolve(demoInterpretData(prompt));
  return request<InterpretResponse>("/comparison/interpret", {
    method: "POST",
    body: JSON.stringify({ prompt }),
  });
}

export function compareHospitals(
  body: ComparisonRequest,
): Promise<ComparisonResponse> {
  if (isDemoMode()) {
    if (body.source === "public_research") {
      return Promise.reject(new ApiError(501, { error: "public_research_not_configured", message: "DEMONSTRATION DATA: Public internet research is not implemented and cannot be performed in demo mode.", required_integration: "A server-side search provider with an API key." }));
    }
    return Promise.resolve(demoComparisonData(body));
  }
  return request<ComparisonResponse>("/comparison", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function auditConsistency(
  body: ConsistencyAuditRequest,
): Promise<ConsistencyAuditResponse> {
  if (isDemoMode()) return Promise.resolve(demoConsistencyAuditData(body.facility_id));
  return request<ConsistencyAuditResponse>("/consistency/audit", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function extractFromWebsite(
  body: CrawlRequest,
): Promise<CrawlResponse> {
  if (isDemoMode()) return Promise.resolve(demoCrawlData(body));
  return request<CrawlResponse>("/crawl/extract", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getCrawlRun(
  crawlRunId: string,
  options?: { facility_id?: string; comparison_subject_id?: string },
): Promise<CrawlRunDetail> {
  if (isDemoMode()) return Promise.resolve(demoCrawlRunData("https://demo.example/"));
  const params = new URLSearchParams();
  if (options?.facility_id) params.set("facility_id", options.facility_id);
  if (options?.comparison_subject_id)
    params.set("comparison_subject_id", options.comparison_subject_id);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return request<CrawlRunDetail>(`/crawl/runs/${crawlRunId}${suffix}`);
}

function operatorUnavailable(): never {
  throw new ApiError(
    501,
    {
      error: "demo_mode",
      message:
        "The hospital integration workflow is not available in demonstration mode. " +
        "Run the API server with a configured MEDCOMPARE_OPERATOR_API_KEY to configure and capture hospital sources.",
    },
  );
}

function authHeaders(
  operatorToken?: string,
): { headers?: Record<string, string> } {
  return operatorToken
    ? { headers: { Authorization: `Bearer ${operatorToken}` } }
    : {};
}

export function listIntegrations(
  facilityId: string,
): Promise<IntegrationListResponse> {
  if (isDemoMode()) return operatorUnavailable();
  return request<IntegrationListResponse>(
    `/facilities/${facilityId}/integrations`,
  );
}

export function createIntegration(
  facilityId: string,
  body: IntegrationCreateRequest,
  operatorToken: string,
): Promise<FacilityIntegration> {
  if (isDemoMode()) return operatorUnavailable();
  return request<FacilityIntegration>(`/facilities/${facilityId}/integrations`, {
    method: "POST",
    body: JSON.stringify(body),
    ...authHeaders(operatorToken),
  });
}

export function updateIntegration(
  facilityId: string,
  integrationId: string,
  body: IntegrationUpdateRequest,
  operatorToken: string,
): Promise<FacilityIntegration> {
  if (isDemoMode()) return operatorUnavailable();
  return request<FacilityIntegration>(
    `/facilities/${facilityId}/integrations/${integrationId}`,
    {
      method: "PATCH",
      body: JSON.stringify(body),
      ...authHeaders(operatorToken),
    },
  );
}

export function captureIntegration(
  facilityId: string,
  integrationId: string,
  operatorToken: string,
): Promise<CaptureResponse> {
  if (isDemoMode()) return operatorUnavailable();
  return request<CaptureResponse>(
    `/facilities/${facilityId}/integrations/${integrationId}/capture`,
    { method: "POST", ...authHeaders(operatorToken) },
  );
}

export function listSnapshots(
  facilityId: string,
): Promise<SnapshotListResponse> {
  if (isDemoMode()) return operatorUnavailable();
  return request<SnapshotListResponse>(`/facilities/${facilityId}/snapshots`);
}

export function listChangeSets(
  facilityId: string,
): Promise<ChangeSetListResponse> {
  if (isDemoMode()) return operatorUnavailable();
  return request<ChangeSetListResponse>(`/facilities/${facilityId}/change-sets`);
}

export function getChangeSet(
  facilityId: string,
  changeSetId: string,
): Promise<ChangeSetDetail> {
  if (isDemoMode()) return operatorUnavailable();
  return request<ChangeSetDetail>(
    `/facilities/${facilityId}/change-sets/${changeSetId}`,
  );
}

export function listVerificationJobs(
  facilityId: string,
): Promise<JobListResponse> {
  if (isDemoMode()) return operatorUnavailable();
  return request<JobListResponse>(
    `/facilities/${facilityId}/verification-jobs`,
  );
}

export function getVerificationJob(
  facilityId: string,
  jobId: string,
): Promise<import("./types").VerificationJob> {
  if (isDemoMode()) return operatorUnavailable();
  return request<import("./types").VerificationJob>(
    `/facilities/${facilityId}/verification-jobs/${jobId}`,
  );
}

export function enqueueManualVerificationJob(
  facilityId: string,
  operatorToken: string,
): Promise<ManualJobResponse> {
  if (isDemoMode()) return operatorUnavailable();
  return request<ManualJobResponse>(
    `/facilities/${facilityId}/verification-jobs`,
    { method: "POST", ...authHeaders(operatorToken) },
  );
}

export function cancelVerificationJob(
  facilityId: string,
  jobId: string,
  operatorToken: string,
): Promise<import("./types").VerificationJob> {
  if (isDemoMode()) return operatorUnavailable();
  return request<import("./types").VerificationJob>(
    `/facilities/${facilityId}/verification-jobs/${jobId}/cancel`,
    { method: "POST", ...authHeaders(operatorToken) },
  );
}

export function listMonitoringSchedules(
  facilityId: string,
): Promise<ScheduleListResponse> {
  if (isDemoMode()) return operatorUnavailable();
  return request<ScheduleListResponse>(
    `/facilities/${facilityId}/monitoring/schedules`,
  );
}

export function createMonitoringSchedule(
  facilityId: string,
  body: ScheduleCreateRequest,
  operatorToken: string,
): Promise<MonitoringSchedule> {
  if (isDemoMode()) return operatorUnavailable();
  return request<MonitoringSchedule>(
    `/facilities/${facilityId}/monitoring/schedules`,
    {
      method: "POST",
      body: JSON.stringify(body),
      ...authHeaders(operatorToken),
    },
  );
}

export function updateMonitoringSchedule(
  facilityId: string,
  scheduleId: string,
  body: ScheduleUpdateRequest,
  operatorToken: string,
): Promise<MonitoringSchedule> {
  if (isDemoMode()) return operatorUnavailable();
  return request<MonitoringSchedule>(
    `/facilities/${facilityId}/monitoring/schedules/${scheduleId}`,
    {
      method: "PATCH",
      body: JSON.stringify(body),
      ...authHeaders(operatorToken),
    },
  );
}

export function listMonitoringExecutions(
  facilityId: string,
): Promise<ExecutionListResponse> {
  if (isDemoMode()) return operatorUnavailable();
  return request<ExecutionListResponse>(
    `/facilities/${facilityId}/monitoring/executions`,
  );
}

export function listReconciliationCases(
  facilityId: string,
): Promise<CaseListResponse> {
  if (isDemoMode()) return operatorUnavailable();
  return request<CaseListResponse>(
    `/facilities/${facilityId}/reconciliation-cases`,
  );
}

export function getReconciliationCase(
  facilityId: string,
  caseId: string,
): Promise<ReconciliationCaseDetail> {
  if (isDemoMode()) return operatorUnavailable();
  return request<ReconciliationCaseDetail>(
    `/facilities/${facilityId}/reconciliation-cases/${caseId}`,
  );
}

export function transitionReconciliationCase(
  facilityId: string,
  caseId: string,
  body: TransitionCaseRequest,
  operatorToken: string,
): Promise<ReconciliationCaseDetail> {
  if (isDemoMode()) return operatorUnavailable();
  return request<ReconciliationCaseDetail>(
    `/facilities/${facilityId}/reconciliation-cases/${caseId}/transitions`,
    {
      method: "POST",
      body: JSON.stringify(body),
      ...authHeaders(operatorToken),
    },
  );
}
