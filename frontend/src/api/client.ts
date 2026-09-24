import type {
  AuditEvent,
  ComparisonRequest,
  ComparisonResult,
  ConfiguredSource,
  DatasetSummary,
  HospitalSummary,
  LoginResponse,
  MeResponse,
  MonitoringPolicy,
  QualityReport,
  Query,
  QueryResult,
  ReconciliationCaseListItem,
  ReportJson,
  SourceRow,
  UserPublic,
  VerificationJobRow,
} from "../types/api";

const BASE = "";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, message: string, detail = "") {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

let authToken: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setAuthToken(token: string | null): void {
  authToken = token;
}

export function getAuthToken(): string | null {
  return authToken;
}

export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

async function request<T>(
  path: string,
  init: Omit<RequestInit, "body"> & {
    body?: unknown;
    /** False when a 401 from this endpoint means "resource not authorized", not "session expired". */
    sessionFailure?: boolean;
  } = {},
): Promise<T> {
  const sessionFailure = init.sessionFailure !== false;
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (authToken) headers.set("Authorization", `Bearer ${authToken}`);
  const rawBody = init.body;
  let body: BodyInit | undefined;
  if (rawBody !== undefined && !(rawBody instanceof FormData)) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(rawBody);
  } else if (rawBody instanceof FormData) {
    body = rawBody;
  }
  const res = await fetch(`${BASE}${path}`, {
    method: init.method,
    headers,
    body,
  });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }
  if (!res.ok) {
    const obj = data as { detail?: string; error?: string } | null;
    const message = obj?.detail || obj?.error || `Request failed (${res.status})`;
    if (res.status === 401 && sessionFailure) onUnauthorized?.();
    throw new ApiError(res.status, message, message);
  }
  return data as T;
}

export const api = {
  login(username: string, password: string): Promise<LoginResponse> {
    return request("/api/v1/auth/login", {
      method: "POST",
      body: { username, password },
    });
  },
  logout(): Promise<void> {
    return request("/api/v1/auth/logout", { method: "POST" });
  },
  me(): Promise<MeResponse> {
    return request("/api/v1/auth/me");
  },
  health(): Promise<{ status: string }> {
    return request("/api/v1/health");
  },
  ready(): Promise<Record<string, unknown>> {
    return request("/ready");
  },
  listUsers(): Promise<UserPublic[]> {
    return request("/api/v1/users");
  },
  createUser(body: {
    username: string;
    password: string;
    role: string;
    enabled?: boolean;
    hospital_ids?: string[];
  }): Promise<UserPublic> {
    return request("/api/v1/users", { method: "POST", body });
  },
  updateUser(
    userId: string,
    body: Partial<{ role: string; enabled: boolean; password: string; hospital_ids: string[] }>,
  ): Promise<UserPublic> {
    return request(`/api/v1/users/${userId}`, { method: "PATCH", body });
  },
  listAudit(params?: { action?: string; hospital_id?: string; limit?: number }): Promise<
    { events: AuditEvent[] } | AuditEvent[]
  > {
    const qs = new URLSearchParams();
    if (params?.action) qs.set("action", params.action);
    if (params?.hospital_id) qs.set("hospital_id", params.hospital_id);
    if (params?.limit) qs.set("limit", String(params.limit));
    const suffix = qs.toString() ? `?${qs}` : "";
    return request(`/api/v1/audit${suffix}`);
  },
  listHospitals(): Promise<{ datasets: DatasetSummary[] }> {
    return request("/api/v1/hospitals");
  },
  hospitalRegistry(): Promise<{ hospitals: HospitalSummary[] }> {
    return request("/api/v1/hospitals/registry");
  },
  hospitalSnapshots(hospitalId: string): Promise<{
    hospital_id: string;
    snapshots: unknown[];
    change_sets: unknown[];
  }> {
    return request(`/api/v1/hospitals/${encodeURIComponent(hospitalId)}/snapshots`);
  },
  hospitalChanges(hospitalId: string): Promise<{
    hospital_id: string;
    change_sets: unknown[];
  }> {
    return request(`/api/v1/hospitals/${encodeURIComponent(hospitalId)}/changes`);
  },
  listSources(): Promise<{ sources: SourceRow[] }> {
    return request("/api/v1/sources");
  },
  listConnectors(): Promise<{ connectors: SourceRow[] }> {
    return request("/api/v1/connectors");
  },
  configuredSources(): Promise<{ configured_sources: ConfiguredSource[] }> {
    return request("/api/v1/sources/configured");
  },
  testSource(sourceType: string): Promise<Record<string, unknown>> {
    // The offline probe answers 401 when the *source* is unauthorized (P21 layer-2
    // denial). That is a domain result, not a dead session, so it must not clear auth.
    return request(`/api/v1/sources/${encodeURIComponent(sourceType)}/test`, {
      sessionFailure: false,
    });
  },
  monitoringPolicies(hospitalId: string): Promise<{ monitoring_policies: MonitoringPolicy[] }> {
    return request(`/api/v1/hospitals/${encodeURIComponent(hospitalId)}/monitoring-policies`);
  },
  createMonitoringPolicy(
    hospitalId: string,
    body: { frequency: string; source: string; target_ref?: string; enabled?: boolean },
  ): Promise<{ monitoring_policy: MonitoringPolicy }> {
    return request(`/api/v1/hospitals/${encodeURIComponent(hospitalId)}/monitoring-policies`, {
      method: "POST",
      body,
    });
  },
  enableMonitoringPolicy(policyId: string): Promise<unknown> {
    return request(`/api/v1/monitoring-policies/${encodeURIComponent(policyId)}/enable`, {
      method: "POST",
      body: {},
    });
  },
  disableMonitoringPolicy(policyId: string): Promise<unknown> {
    return request(`/api/v1/monitoring-policies/${encodeURIComponent(policyId)}/disable`, {
      method: "POST",
      body: {},
    });
  },
  runMonitoringPolicy(policyId: string): Promise<unknown> {
    return request(`/api/v1/monitoring-policies/${encodeURIComponent(policyId)}/run`, {
      method: "POST",
      body: {},
    });
  },
  verificationJobs(hospitalId: string): Promise<{
    hospital_id: string;
    verification_jobs: VerificationJobRow[];
  }> {
    return request(`/api/v1/hospitals/${encodeURIComponent(hospitalId)}/verification-jobs`);
  },
  reconciliationCases(hospitalId: string): Promise<{
    hospital_id: string;
    cases: ReconciliationCaseListItem[];
  }> {
    return request(`/api/v1/hospitals/${encodeURIComponent(hospitalId)}/reconciliation-cases`);
  },
  getReconciliationCase(caseId: string): Promise<Record<string, unknown>> {
    return request(`/api/v1/reconciliation-cases/${encodeURIComponent(caseId)}`);
  },
  reviewCase(caseId: string, body: Record<string, unknown> = {}): Promise<unknown> {
    return request(`/api/v1/reconciliation-cases/${encodeURIComponent(caseId)}/review`, {
      method: "POST",
      body,
    });
  },
  resolveCase(caseId: string, body: Record<string, unknown>): Promise<unknown> {
    return request(`/api/v1/reconciliation-cases/${encodeURIComponent(caseId)}/resolve`, {
      method: "POST",
      body,
    });
  },
  getCaseReport(caseId: string): Promise<ReportJson> {
    return request(`/api/v1/reconciliation-cases/${encodeURIComponent(caseId)}/report`);
  },
  generateCaseReport(caseId: string, body: Record<string, unknown> = {}): Promise<unknown> {
    return request(`/api/v1/reconciliation-cases/${encodeURIComponent(caseId)}/report`, {
      method: "POST",
      body,
    });
  },
  hospitalReports(hospitalId: string): Promise<{ reports: ReportJson[] }> {
    return request(`/api/v1/hospitals/${encodeURIComponent(hospitalId)}/reports`);
  },
  search(query: Query): Promise<{ result: QueryResult }> {
    return request("/api/v1/search", { method: "POST", body: query });
  },
  compare(body: ComparisonRequest): Promise<{ result: ComparisonResult }> {
    return request("/api/v1/search/compare", { method: "POST", body });
  },
  qualityPipeline(): Promise<{ report: QualityReport }> {
    return request("/api/v1/quality/pipeline");
  },
  qualityHospital(hospitalId: string): Promise<{ report: QualityReport }> {
    return request(`/api/v1/quality/hospitals/${encodeURIComponent(hospitalId)}`);
  },
  qualityDataset(datasetId: string): Promise<{ report: QualityReport }> {
    return request(`/api/v1/quality/datasets/${encodeURIComponent(datasetId)}`);
  },
};
