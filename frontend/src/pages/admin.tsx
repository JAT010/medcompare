import { useMemo, useState } from "react";
import { api } from "../api/client";
import { useAsync } from "../hooks/useAsync";
import { Badge, Card, Disclaimer, EmptyState, ErrorState, Loading } from "../components/ui";

function asArray<T>(value: unknown, key: string): T[] {
  if (Array.isArray(value)) return value as T[];
  if (value && typeof value === "object" && Array.isArray((value as Record<string, unknown>)[key])) {
    return (value as Record<string, unknown>)[key] as T[];
  }
  return [];
}

function metricValue(metrics: Array<Record<string, unknown>>, name: string): string {
  const hit = metrics.find((m) => m.name === name);
  return hit ? String(hit.value) : "—";
}

function metricLabel(metrics: Array<Record<string, unknown>>, name: string): string {
  const hit = metrics.find((m) => m.name === name);
  return hit ? String(hit.value) : "none";
}

export function AdminDashboardPage() {
  const hospitals = useAsync(() => api.hospitalRegistry(), []);
  const datasets = useAsync(() => api.listHospitals(), []);
  const users = useAsync(() => api.listUsers(), []);
  const quality = useAsync(() => api.qualityPipeline(), []);
  const health = useAsync(() => api.health(), []);
  const sources = useAsync(() => api.listSources(), []);
  const audit = useAsync(() => api.listAudit({ limit: 6 }), []);

  const report = (quality.data?.report ?? null) as Record<string, unknown> | null;
  const metrics =
    report && Array.isArray(report.metrics)
      ? (report.metrics as Array<Record<string, unknown>>)
      : [];
  const sourceRows = sources.data?.sources ?? [];
  const auditEvents = asArray<Record<string, unknown>>(audit.data, "events");

  return (
    <div data-testid="admin-dashboard" style={{ display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>Admin dashboard</h1>
      <Disclaimer />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))", gap: 12 }}>
        <Card title="Hospitals">
          {hospitals.loading ? <Loading /> : null}
          {hospitals.error ? <ErrorState message={hospitals.error.message} /> : null}
          <div data-testid="hospital-count" style={{ fontSize: 28, fontWeight: 700 }}>
            {hospitals.data?.hospitals.length ?? 0}
          </div>
        </Card>
        <Card title="Datasets">
          {datasets.loading ? <Loading /> : null}
          <div data-testid="dataset-count" style={{ fontSize: 28, fontWeight: 700 }}>
            {datasets.data?.datasets.length ?? 0}
          </div>
        </Card>
        <Card title="Users">
          {users.loading ? <Loading /> : null}
          <div data-testid="user-count" style={{ fontSize: 28, fontWeight: 700 }}>
            {users.data?.length ?? 0}
          </div>
        </Card>
        <Card title="Health">
          {health.loading ? <Loading /> : null}
          <div data-testid="health-status">{health.data?.status ?? "—"}</div>
        </Card>
      </div>

      <Card title="Operational status (stored rows only)">
        {quality.loading ? <Loading /> : null}
        {metrics.length ? (
          <table data-testid="ops-status" style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <tbody>
              <tr>
                <td>Monitoring policies</td>
                <td>{metricValue(metrics, "monitoring_policy_count")}</td>
                <td>{metricLabel(metrics, "monitoring_status_counts")}</td>
                <td>Orchestration runs</td>
                <td>{metricValue(metrics, "orchestration_run_count")}</td>
                <td>{metricLabel(metrics, "orchestration_status_counts")}</td>
              </tr>
              <tr style={{ borderTop: "1px solid #e5e7eb" }}>
                <td>Change sets</td>
                <td>{metricValue(metrics, "change_set_count")}</td>
                <td>Snapshots</td>
                <td>{metricValue(metrics, "snapshot_count")}</td>
                <td>Reports</td>
                <td>{metricValue(metrics, "report_count")}</td>
              </tr>
              <tr style={{ borderTop: "1px solid #e5e7eb" }}>
                <td>Verification jobs</td>
                <td>{metricValue(metrics, "verification_job_count")}</td>
                <td>{metricLabel(metrics, "verification_status_counts")}</td>
                <td>Reconciliation cases</td>
                <td>{metricValue(metrics, "reconciliation_case_count")}</td>
                <td>{metricLabel(metrics, "reconciliation_status_counts")}</td>
              </tr>
              <tr style={{ borderTop: "1px solid #e5e7eb" }}>
                <td>Authorization states</td>
                <td colSpan={5}>{metricLabel(metrics, "authorization_status_counts")}</td>
              </tr>
            </tbody>
          </table>
        ) : null}
      </Card>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(320px,1fr))", gap: 12 }}>
        <Card title="Sources & connectors">
          {sources.loading ? <Loading /> : null}
          {!sources.loading && !sourceRows.length ? (
            <EmptyState message="No source types registered." />
          ) : (
            <ul data-testid="dashboard-sources" style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
              {sourceRows.map((s) => (
                <li key={s.source_type} style={{ marginBottom: 4 }}>
                  {s.source_type}{" "}
                  <Badge
                    tone={
                      s.authorization_status === "UNAUTHORIZED"
                        ? "bad"
                        : s.authorization_status?.includes("SIMULATED")
                          ? "warn"
                          : "ok"
                    }
                  >
                    {s.authorization_status}
                  </Badge>{" "}
                  <code>{s.connector || "—"}</code>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Recent audit activity">
          {audit.loading ? <Loading /> : null}
          {!audit.loading && !auditEvents.length ? (
            <EmptyState message="No audit events." />
          ) : (
            <ul data-testid="dashboard-audit" style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
              {auditEvents.map((e, i) => (
                <li key={String(e.audit_event_id || i)} style={{ marginBottom: 4 }}>
                  {String(e.timestamp || "—")} · {String(e.action)} · {String(e.actor_id)} (
                  {String(e.actor_role)}) · <Badge tone={e.outcome === "success" ? "ok" : "warn"}>{String(e.outcome)}</Badge>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title="Quality (no overall score)">
        {quality.loading ? <Loading /> : null}
        {quality.error ? <ErrorState message={quality.error.message} /> : null}
        {quality.data ? (
          <pre data-testid="quality-snippet" style={{ fontSize: 12, overflow: "auto" }}>
            {JSON.stringify(
              { overall_score: quality.data.report.overall_score ?? null, note: quality.data.report.note },
              null,
              2,
            )}
          </pre>
        ) : null}
      </Card>
    </div>
  );
}

export function AdminHospitalsPage() {
  const datasets = useAsync(() => api.listHospitals(), []);
  const registry = useAsync(() => api.hospitalRegistry(), []);
  const [selected, setSelected] = useState<string | null>(null);
  const snapshots = useAsync(
    () => (selected ? api.hospitalSnapshots(selected) : Promise.resolve(null)),
    [selected],
  );

  return (
    <div data-testid="admin-hospitals" style={{ display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>Hospitals</h1>
      <Disclaimer />
      <Card title="Registry">
        {registry.loading ? <Loading /> : null}
        {registry.error ? <ErrorState message={registry.error.message} /> : null}
        {registry.data?.hospitals.length === 0 ? (
          <EmptyState message="No hospitals yet — run demo seed." />
        ) : (
          <ul data-testid="hospital-list" style={{ margin: 0, paddingLeft: 18 }}>
            {registry.data?.hospitals.map((h) => (
              <li key={h.hospital_id} style={{ marginBottom: 6 }}>
                <button type="button" onClick={() => setSelected(h.hospital_id)}>
                  {h.name || h.hospital_id}
                </button>{" "}
                <code>{h.hospital_id}</code>{" "}
                <Badge tone="info">{h.dataset_count ?? 0} datasets</Badge>{" "}
                <Badge>{h.snapshot_count ?? 0} snapshots</Badge>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title="Datasets">
        {datasets.loading ? <Loading /> : null}
        {datasets.error ? <ErrorState message={datasets.error.message} /> : null}
        {datasets.data?.datasets.length === 0 ? (
          <EmptyState message="No datasets." />
        ) : (
          <table data-testid="datasets-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr>
                <th align="left">Hospital</th>
                <th align="left">Source</th>
                <th align="left">Hospital ID</th>
                <th align="left">Created</th>
              </tr>
            </thead>
            <tbody>
              {datasets.data?.datasets.map((d) => (
                <tr key={d.dataset_id} style={{ borderTop: "1px solid #e5e7eb" }}>
                  <td>{d.hospital_name || d.hospital_ref}</td>
                  <td>
                    <Badge tone={d.source_type === "mock_api" ? "warn" : "neutral"}>
                      {d.source_type === "mock_api" ? "SIMULATED / MOCK" : d.source_type}
                    </Badge>
                  </td>
                  <td>
                    <code>{d.hospital_id || "—"}</code>
                  </td>
                  <td>{new Date(d.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      {selected ? (
        <Card title={`Snapshots & changes — ${selected}`}>
          {snapshots.loading ? <Loading /> : null}
          {snapshots.error ? <ErrorState message={snapshots.error.message} /> : null}
          {snapshots.data ? (
            <div data-testid="snapshots-panel">
              <p>
                Snapshots: {(snapshots.data.snapshots as unknown[]).length} · Change sets:{" "}
                {(snapshots.data.change_sets as unknown[]).length}
              </p>
              <ul>
                {(snapshots.data.change_sets as Array<Record<string, unknown>>).slice(0, 10).map((cs, i) => (
                  <li key={i}>
                    <code>{String(cs.change_set_id || cs.snapshot_id || i)}</code>{" "}
                    <Badge tone={String(cs.status) === "changes_detected" ? "warn" : "ok"}>
                      {String(cs.status || "unknown")}
                    </Badge>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}

export function AdminSourcesPage() {
  const sources = useAsync(() => api.listSources(), []);
  const configured = useAsync(() => api.configuredSources(), []);
  const connectors = useAsync(() => api.listConnectors(), []);
  const [probe, setProbe] = useState<string | null>(null);
  const [probeResult, setProbeResult] = useState<string>("");
  const [probeError, setProbeError] = useState<string | null>(null);

  async function runProbe(type: string) {
    setProbe(type);
    setProbeError(null);
    setProbeResult("");
    try {
      const body = await api.testSource(type);
      setProbeResult(JSON.stringify(body, null, 2));
    } catch (err) {
      setProbeError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div data-testid="admin-sources" style={{ display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>Sources & connectors</h1>
      <Disclaimer />
      <Card title="Registered source types">
        {sources.loading ? <Loading /> : null}
        {sources.error ? <ErrorState message={sources.error.message} /> : null}
        <table data-testid="sources-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr>
              <th align="left">Source</th>
              <th align="left">Status</th>
              <th align="left">Connector</th>
              <th align="left">Probe</th>
            </tr>
          </thead>
          <tbody>
            {sources.data?.sources.map((s) => (
              <tr key={s.source_type} style={{ borderTop: "1px solid #e5e7eb" }}>
                <td>{s.source_type}</td>
                <td>
                  <Badge
                    tone={
                      s.authorization_status === "UNAUTHORIZED"
                        ? "bad"
                        : s.authorization_status?.includes("SIMULATED")
                          ? "warn"
                          : "ok"
                    }
                  >
                    {s.authorization_status}
                  </Badge>
                </td>
                <td>{s.connector || "—"}</td>
                <td>
                  <button type="button" onClick={() => void runProbe(s.source_type)}>
                    Offline probe
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {probeError ? (
          <div style={{ marginTop: 8 }}>
            <ErrorState message={probeError} />
          </div>
        ) : null}
        {probeResult ? (
          <pre data-testid="probe-result" style={{ fontSize: 12, background: "#f9fafb", padding: 8 }}>
            {probeResult}
          </pre>
        ) : null}
        {probe === "hospital_api" && !probeResult && !probeError ? null : null}
      </Card>
      <Card title="Configured sources (config/sources.toml)">
        {configured.loading ? <Loading /> : null}
        {configured.error ? <ErrorState message={configured.error.message} /> : null}
        {configured.data?.configured_sources.length === 0 ? (
          <EmptyState message="No active configured sources (sample entries are disabled by default)." />
        ) : (
          <ul data-testid="configured-sources" style={{ margin: 0, paddingLeft: 18 }}>
            {configured.data?.configured_sources.map((c) => (
              <li key={c.name}>
                <strong>{c.name}</strong> ({c.source_type}) —{" "}
                <Badge tone={c.authorization_status === "UNAUTHORIZED" ? "bad" : "ok"}>
                  {c.authorization_status}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title="Connectors">
        {connectors.loading ? <Loading /> : null}
        <ul data-testid="connectors-list" style={{ margin: 0, paddingLeft: 18 }}>
          {connectors.data?.connectors.map((c) => (
            <li key={c.source_type}>
              {c.source_type}: <code>{c.connector || "unresolved"}</code>{" "}
              {c.requires_authorization ? <Badge tone="bad">requires authorization</Badge> : null}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

export function AdminMonitoringPage() {
  const registry = useAsync(() => api.hospitalRegistry(), []);
  const [hospitalId, setHospitalId] = useState("");
  const hospitals = useMemo(() => registry.data?.hospitals ?? [], [registry.data]);
  const active = hospitalId || hospitals[0]?.hospital_id || "";
  const policies = useAsync(
    () => (active ? api.monitoringPolicies(active) : Promise.resolve({ monitoring_policies: [] })),
    [active],
  );
  const [msg, setMsg] = useState<string | null>(null);

  const hospitalOptions = useMemo(() => hospitals.map((h) => h.hospital_id), [hospitals]);

  async function toggle(policyId: string, enable: boolean) {
    setMsg(null);
    try {
      if (enable) await api.enableMonitoringPolicy(policyId);
      else await api.disableMonitoringPolicy(policyId);
      policies.reload();
      setMsg(`${enable ? "Enabled" : "Disabled"} ${policyId}`);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div data-testid="admin-monitoring" style={{ display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>Monitoring</h1>
      <Disclaimer />
      <Card title="Policies">
        <label style={{ display: "grid", gap: 4, fontSize: 13, marginBottom: 12 }}>
          Hospital
          <select
            data-testid="monitoring-hospital"
            value={active}
            onChange={(e) => setHospitalId(e.target.value)}
          >
            {hospitalOptions.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </label>
        {msg ? <p data-testid="monitoring-msg">{msg}</p> : null}
        {policies.loading ? <Loading /> : null}
        {policies.error ? <ErrorState message={policies.error.message} /> : null}
        {policies.data?.monitoring_policies.length === 0 ? (
          <EmptyState message="No monitoring policies for this hospital." />
        ) : (
          <ul data-testid="policies-list" style={{ margin: 0, paddingLeft: 18 }}>
            {policies.data?.monitoring_policies.map((p) => (
              <li key={p.policy_id} style={{ marginBottom: 12 }}>
                <code>{p.policy_id}</code>{" "}
                <Badge tone={p.enabled ? "ok" : "neutral"}>{p.frequency}</Badge>{" "}
                <Badge>{p.source_type}</Badge>{" "}
                <Badge tone={p.status === "error" ? "bad" : "info"}>{p.status || "—"}</Badge>{" "}
                <button type="button" onClick={() => void toggle(p.policy_id, !p.enabled)}>
                  {p.enabled ? "Disable" : "Enable"}
                </button>
                <dl
                  data-testid={`policy-detail-${p.policy_id}`}
                  style={{
                    margin: "6px 0 0",
                    display: "grid",
                    gridTemplateColumns: "max-content 1fr",
                    gap: "2px 12px",
                    fontSize: 12,
                    color: "#374151",
                  }}
                >
                  <dt>Schedule</dt>
                  <dd style={{ margin: 0 }}>{p.frequency} (UTC)</dd>
                  <dt>Target</dt>
                  <dd style={{ margin: 0 }}>{p.target_ref || "—"}</dd>
                  <dt>Next run</dt>
                  <dd style={{ margin: 0 }}>{p.next_run_at || "—"}</dd>
                  <dt>Last run</dt>
                  <dd style={{ margin: 0 }}>{p.last_run_at || "never"}</dd>
                  <dt>Last success</dt>
                  <dd style={{ margin: 0 }}>{p.last_success_at || "never"}</dd>
                  <dt>Last failure</dt>
                  <dd style={{ margin: 0 }}>{p.last_failure_at || "none"}</dd>
                  <dt>Consecutive failures</dt>
                  <dd style={{ margin: 0 }}>{p.consecutive_failures ?? 0}</dd>
                  <dt>Last error</dt>
                  <dd style={{ margin: 0 }}>{p.last_error || "none"}</dd>
                  <dt>Orchestration run</dt>
                  <dd style={{ margin: 0 }}>
                    <code>{p.last_orchestration_run_id || "not run yet"}</code>
                  </dd>
                  <dt>Last change set</dt>
                  <dd style={{ margin: 0 }}>{p.last_change_set_status || "none"}</dd>
                </dl>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

export function AdminVerificationPage() {
  const registry = useAsync(() => api.hospitalRegistry(), []);
  const hospitals = registry.data?.hospitals ?? [];
  const [hospitalId, setHospitalId] = useState("");
  const active = hospitalId || hospitals[0]?.hospital_id || "";
  const jobs = useAsync(
    () =>
      active
        ? api.verificationJobs(active)
        : Promise.resolve({ hospital_id: "", verification_jobs: [] }),
    [active],
  );

  return (
    <div data-testid="admin-verification" style={{ display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>Verification</h1>
      <Disclaimer />
      <Card title="Jobs & results">
        <label style={{ display: "grid", gap: 4, fontSize: 13, marginBottom: 12 }}>
          Hospital
          <select data-testid="verification-hospital" value={active} onChange={(e) => setHospitalId(e.target.value)}>
            {hospitals.map((h) => (
              <option key={h.hospital_id} value={h.hospital_id}>
                {h.hospital_id}
              </option>
            ))}
          </select>
        </label>
        {jobs.loading ? <Loading /> : null}
        {jobs.error ? <ErrorState message={jobs.error.message} /> : null}
        {jobs.data?.verification_jobs.length === 0 ? (
          <EmptyState message="No verification jobs." />
        ) : (
          <ul data-testid="verification-jobs" style={{ margin: 0, paddingLeft: 18 }}>
            {jobs.data?.verification_jobs.map((row) => (
              <li key={row.job.verification_job_id} style={{ marginBottom: 8 }}>
                <code>{row.job.verification_job_id}</code>{" "}
                <Badge tone={row.job.status === "mismatch" ? "bad" : "ok"}>{row.job.status}</Badge>{" "}
                target={row.job.target_source_type}
                {row.result ? (
                  <>
                    {" "}
                    — result <Badge tone={row.result.status === "mismatch" ? "bad" : "ok"}>
                      {row.result.status}
                    </Badge>
                    {row.result.explanation ? <div style={{ fontSize: 12 }}>{row.result.explanation}</div> : null}
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

export function AdminReconciliationPage() {
  const registry = useAsync(() => api.hospitalRegistry(), []);
  const hospitals = registry.data?.hospitals ?? [];
  const [hospitalId, setHospitalId] = useState("");
  const active = hospitalId || hospitals[0]?.hospital_id || "";
  const cases = useAsync(
    () =>
      active
        ? api.reconciliationCases(active)
        : Promise.resolve({ hospital_id: "", cases: [] }),
    [active],
  );
  const [msg, setMsg] = useState<string | null>(null);

  async function review(caseId: string) {
    setMsg(null);
    try {
      await api.reviewCase(caseId, {});
      cases.reload();
      setMsg(`Started review for ${caseId}`);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div data-testid="admin-reconciliation" style={{ display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>Reconciliation</h1>
      <Disclaimer />
      <Card title="Cases">
        <label style={{ display: "grid", gap: 4, fontSize: 13, marginBottom: 12 }}>
          Hospital
          <select data-testid="recon-hospital" value={active} onChange={(e) => setHospitalId(e.target.value)}>
            {hospitals.map((h) => (
              <option key={h.hospital_id} value={h.hospital_id}>
                {h.hospital_id}
              </option>
            ))}
          </select>
        </label>
        {msg ? <p data-testid="recon-msg">{msg}</p> : null}
        {cases.loading ? <Loading /> : null}
        {cases.error ? <ErrorState message={cases.error.message} /> : null}
        {cases.data?.cases.length === 0 ? (
          <EmptyState message="No reconciliation cases for this hospital." />
        ) : (
          <ul data-testid="recon-cases" style={{ margin: 0, paddingLeft: 18 }}>
            {cases.data?.cases.map((item) => (
              <li key={item.case.reconciliation_case_id} style={{ marginBottom: 8 }}>
                <code>{item.case.reconciliation_case_id}</code>{" "}
                <Badge
                  tone={
                    item.case.status === "resolved"
                      ? "ok"
                      : item.case.status === "under_review"
                        ? "warn"
                        : "bad"
                  }
                >
                  {item.case.status}
                </Badge>{" "}
                <button type="button" onClick={() => void review(item.case.reconciliation_case_id)}>
                  Start review
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

export function AdminReportsPage() {
  const registry = useAsync(() => api.hospitalRegistry(), []);
  const hospitals = registry.data?.hospitals ?? [];
  const [hospitalId, setHospitalId] = useState("");
  const active = hospitalId || hospitals[0]?.hospital_id || "";
  const reports = useAsync(
    () => (active ? api.hospitalReports(active) : Promise.resolve({ reports: [] })),
    [active],
  );
  const [detail, setDetail] = useState<string>("");

  return (
    <div data-testid="admin-reports" style={{ display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>Reports</h1>
      <Disclaimer />
      <Card title="Generated reports">
        <label style={{ display: "grid", gap: 4, fontSize: 13, marginBottom: 12 }}>
          Hospital
          <select data-testid="reports-hospital" value={active} onChange={(e) => setHospitalId(e.target.value)}>
            {hospitals.map((h) => (
              <option key={h.hospital_id} value={h.hospital_id}>
                {h.hospital_id}
              </option>
            ))}
          </select>
        </label>
        {reports.loading ? <Loading /> : null}
        {reports.error ? <ErrorState message={reports.error.message} /> : null}
        {reports.data?.reports.length === 0 ? (
          <EmptyState message="No reports for this hospital." />
        ) : (
          <ul data-testid="reports-list" style={{ margin: 0, paddingLeft: 18 }}>
            {reports.data?.reports.map((r) => (
              <li key={r.report_id} style={{ marginBottom: 8 }}>
                <code>{r.report_id}</code> <Badge tone="info">{r.report_type}</Badge>{" "}
                <Badge>{r.status}</Badge>{" "}
                <button type="button" onClick={() => setDetail(JSON.stringify(r, null, 2))}>
                  View
                </button>
              </li>
            ))}
          </ul>
        )}
        {detail ? (
          <pre data-testid="report-detail" style={{ fontSize: 12, background: "#f9fafb", padding: 8 }}>
            {detail}
          </pre>
        ) : null}
      </Card>
    </div>
  );
}

export function AdminQualityPage() {
  const pipeline = useAsync(() => api.qualityPipeline(), []);
  return (
    <div data-testid="admin-quality" style={{ display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>Data quality</h1>
      <Disclaimer />
      <Card title="Pipeline metrics (stored rows only)">
        {pipeline.loading ? <Loading /> : null}
        {pipeline.error ? <ErrorState message={pipeline.error.message} /> : null}
        {pipeline.data ? (
          <div data-testid="quality-report">
            <p>
              overall_score:{" "}
              <strong data-testid="overall-score">
                {pipeline.data.report.overall_score === null ||
                pipeline.data.report.overall_score === undefined
                  ? "null"
                  : String(pipeline.data.report.overall_score)}
              </strong>
            </p>
            {pipeline.data.report.note ? (
              <p style={{ fontSize: 13, color: "#6b7280" }}>{pipeline.data.report.note}</p>
            ) : null}
            <pre style={{ fontSize: 12, overflow: "auto", maxHeight: 400 }}>
              {JSON.stringify(pipeline.data.report, null, 2)}
            </pre>
          </div>
        ) : null}
      </Card>
    </div>
  );
}

export function AdminAuditPage() {
  const audit = useAsync(() => api.listAudit({ limit: 100 }), []);
  const events = asArray<Record<string, unknown>>(
    audit.data,
    "events",
  );

  return (
    <div data-testid="admin-audit" style={{ display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>Audit</h1>
      <Disclaimer compact />
      <Card title="Recent events">
        {audit.loading ? <Loading /> : null}
        {audit.error ? <ErrorState message={audit.error.message} status={(audit.error as { status?: number }).status} /> : null}
        {events.length === 0 && !audit.loading && !audit.error ? (
          <EmptyState message="No audit events." />
        ) : (
          <table data-testid="audit-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr>
                <th align="left">Time</th>
                <th align="left">Action</th>
                <th align="left">Actor</th>
                <th align="left">Outcome</th>
                <th align="left">Resource</th>
                <th align="left">Correlation</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e, i) => (
                <tr key={String(e.audit_event_id || i)} style={{ borderTop: "1px solid #e5e7eb" }}>
                  <td>{String(e.timestamp || e.created_at || "—")}</td>
                  <td>{String(e.action)}</td>
                  <td>
                    {String(e.actor_id)} ({String(e.actor_role)})
                  </td>
                  <td>
                    <Badge
                      tone={
                        e.outcome === "success" ? "ok" : e.outcome === "denied" ? "bad" : "warn"
                      }
                    >
                      {String(e.outcome)}
                    </Badge>
                  </td>
                  <td>
                    {String(e.resource_type)} / {String(e.resource_id)}
                  </td>
                  <td>
                    <code>{String(e.correlation_id || "—")}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

export function AdminUsersPage() {
  const users = useAsync(() => api.listUsers(), []);
  const [form, setForm] = useState({
    username: "",
    password: "",
    role: "viewer",
  });
  const [msg, setMsg] = useState<string | null>(null);

  async function createUser(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    try {
      await api.createUser({
        username: form.username.trim(),
        password: form.password,
        role: form.role,
        enabled: true,
      });
      setMsg(`Created ${form.username}`);
      setForm({ username: "", password: "", role: "viewer" });
      users.reload();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div data-testid="admin-users" style={{ display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>Users</h1>
      <Disclaimer compact />
      <Card title="All users">
        {users.loading ? <Loading /> : null}
        {users.error ? <ErrorState message={users.error.message} /> : null}
        <table data-testid="users-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <thead>
            <tr>
              <th align="left">Username</th>
              <th align="left">Role</th>
              <th align="left">Enabled</th>
              <th align="left">Hospital grants</th>
            </tr>
          </thead>
          <tbody>
            {users.data?.map((u) => (
              <tr key={u.user_id} style={{ borderTop: "1px solid #e5e7eb" }}>
                <td>{u.username}</td>
                <td>
                  <Badge tone={u.role === "admin" ? "info" : "neutral"}>{u.role}</Badge>
                </td>
                <td>{u.enabled ? "yes" : "no"}</td>
                <td>{u.hospital_ids.join(", ") || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title="Create user">
        {msg ? <p data-testid="user-msg">{msg}</p> : null}
        <form onSubmit={createUser} style={{ display: "grid", gap: 8, maxWidth: 400 }}>
          <label>
            Username
            <input
              data-testid="new-username"
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
              required
            />
          </label>
          <label>
            Password
            <input
              data-testid="new-password"
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              minLength={8}
              required
            />
          </label>
          <label>
            Role
            <select
              data-testid="new-role"
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value })}
            >
              <option value="viewer">viewer</option>
              <option value="reviewer">reviewer</option>
              <option value="operator">operator</option>
              <option value="admin">admin</option>
            </select>
          </label>
          <button type="submit">Create</button>
        </form>
      </Card>
    </div>
  );
}

export function AdminHealthPage() {
  const health = useAsync(() => api.health(), []);
  const ready = useAsync(() => api.ready(), []);
  return (
    <div data-testid="admin-health" style={{ display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>Health & readiness</h1>
      <Card title="/api/v1/health">
        {health.loading ? <Loading /> : null}
        <pre data-testid="health-body">{JSON.stringify(health.data, null, 2)}</pre>
      </Card>
      <Card title="/ready">
        {ready.loading ? <Loading /> : null}
        {ready.error ? <ErrorState message={ready.error.message} /> : null}
        <pre data-testid="ready-body">{JSON.stringify(ready.data, null, 2)}</pre>
      </Card>
    </div>
  );
}
