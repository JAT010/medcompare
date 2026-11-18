import { useEffect, useState } from "react";
import {
  captureIntegration,
  createIntegration,
  createMonitoringSchedule,
  enqueueManualVerificationJob,
  cancelVerificationJob,
  listChangeSets,
  listIntegrations,
  listMonitoringExecutions,
  listMonitoringSchedules,
  listReconciliationCases,
  listSnapshots,
  listVerificationJobs,
  searchFacilities,
  transitionReconciliationCase,
  updateIntegration,
  updateMonitoringSchedule,
  getReconciliationCase,
  getChangeSet,
  toMessage,
} from "../api/client";
import type {
  CaptureResponse,
  ChangeSet,
  ChangeSetDetail,
  DetectedChange,
  Facility,
  FacilityIntegration,
  IntegrationAuthMode,
  MonitoringExecution,
  MonitoringSchedule,
  ReconciliationCase,
  ReconciliationCaseDetail,
  ReconciliationResolution,
  VerificationJob,
  HospitalSnapshot,
} from "../api/types";
import { useDemoMode } from "../api/useDemoMode";

const MOCK_CONNECTOR = "mock_hospital_api_simulated";
const LIVE_CONNECTOR = "hospital_rest_json";

const JOB_STATUS_CLASS: Record<string, string> = {
  pending: "badge badge-outdated",
  running: "badge badge-outdated",
  verified: "badge badge-ok",
  mismatch: "badge badge-conflict",
  insufficient_evidence: "badge badge-missing",
  failed: "badge badge-conflict",
  cancelled: "badge badge-missing",
};

const INTEGRATION_STATUS_CLASS: Record<string, string> = {
  configured: "badge badge-ok",
  not_configured: "badge badge-missing",
  disabled: "badge badge-missing",
  error: "badge badge-conflict",
};

const EXECUTION_STATUS_CLASS: Record<string, string> = {
  enqueued: "badge badge-ok",
  skipped: "badge badge-missing",
  error: "badge badge-conflict",
};

function formatTimestamp(value: string | null): string {
  if (!value) return "timestamp not recorded";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

export default function HospitalIntegrationPanel() {
  const demo = useDemoMode();
  const [facilities, setFacilities] = useState<Facility[]>([]);
  const [facilityId, setFacilityId] = useState<string>("");
  const [operatorToken, setOperatorToken] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [integrations, setIntegrations] = useState<FacilityIntegration[]>([]);
  const [snapshots, setSnapshots] = useState<HospitalSnapshot[]>([]);
  const [changeSets, setChangeSets] = useState<ChangeSet[]>([]);
  const [jobs, setJobs] = useState<VerificationJob[]>([]);
  const [cases, setCases] = useState<ReconciliationCase[]>([]);
  const [schedules, setSchedules] = useState<MonitoringSchedule[]>([]);
  const [executions, setExecutions] = useState<MonitoringExecution[]>([]);

  const [captureResult, setCaptureResult] = useState<CaptureResponse | null>(null);
  const [expandedChangeSet, setExpandedChangeSet] = useState<string | null>(null);
  const [changeSetDetail, setChangeSetDetail] = useState<ChangeSetDetail | null>(null);
  const [expandedJob, setExpandedJob] = useState<string | null>(null);
  const [expandedCase, setExpandedCase] = useState<string | null>(null);
  const [caseDetail, setCaseDetail] = useState<ReconciliationCaseDetail | null>(null);

  const [busy, setBusy] = useState(false);

  // New integration form.
  const [newName, setNewName] = useState("Simulated baseline");
  const [connectorType, setConnectorType] = useState<string>(MOCK_CONNECTOR);
  const [baseUrl, setBaseUrl] = useState("");
  const [allowedHosts, setAllowedHosts] = useState("");
  const [credentialEnvRef, setCredentialEnvRef] = useState("");
  const [authMode, setAuthMode] = useState<IntegrationAuthMode>("none");
  const [payloadVariant, setPayloadVariant] = useState<
    "default" | "changed" | "changed_beds"
  >("default");

  const [scheduleName, setScheduleName] = useState("drift monitor");
  const [scheduleInterval, setScheduleInterval] = useState(300);
  const [scheduleTargetRef, setScheduleTargetRef] = useState("");

  const facility = facilities.find((item) => item.id === facilityId) ?? null;

  useEffect(() => {
    let cancelled = false;
    searchFacilities("")
      .then((data) => {
        if (cancelled) return;
        setFacilities(data.items);
        setFacilityId(data.items[0]?.id ?? "");
        setLoading(false);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(toMessage(cause));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [demo]);

  useEffect(() => {
    if (!facilityId || demo) return;
    void refreshFacility();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [facilityId, demo]);

  async function refreshFacility(): Promise<void> {
    setError(null);
    try {
      const [
        integrationData,
        snapshotData,
        changeSetData,
        jobData,
        caseData,
        scheduleData,
        executionData,
      ] = await Promise.all([
        listIntegrations(facilityId),
        listSnapshots(facilityId),
        listChangeSets(facilityId),
        listVerificationJobs(facilityId),
        listReconciliationCases(facilityId),
        listMonitoringSchedules(facilityId),
        listMonitoringExecutions(facilityId),
      ]);
      setIntegrations(integrationData.items);
      setSnapshots(snapshotData.items);
      setChangeSets(changeSetData.items);
      setJobs(jobData.items);
      setCases(caseData.items);
      setSchedules(scheduleData.items);
      setExecutions(executionData.items);
    } catch (cause: unknown) {
      setError(toMessage(cause));
    }
  }

  async function withBusy(action: () => Promise<void>): Promise<void> {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch (cause: unknown) {
      setError(toMessage(cause));
    } finally {
      setBusy(false);
    }
  }

  function requireToken(): string | null {
    const token = operatorToken.trim();
    if (!token) {
      setError(
        "Enter the operator token (MEDCOMPARE_OPERATOR_API_KEY) to run operator operations.",
      );
      return null;
    }
    return token;
  }

  function buildConfigJson(): Record<string, unknown> {
    if (connectorType === MOCK_CONNECTOR) {
      return { payload_variant: payloadVariant };
    }
    const hosts = allowedHosts
      .split(",")
      .map((host) => host.trim())
      .filter(Boolean);
    return { allowed_hosts: hosts };
  }

  function create(): void {
    const token = requireToken();
    if (!token || !facilityId) return;
    void withBusy(async () => {
      const created = await createIntegration(
        facilityId,
        {
          name: newName.trim(),
          connector_type: connectorType,
          base_url: connectorType === LIVE_CONNECTOR ? baseUrl.trim() || null : null,
          auth_mode: connectorType === LIVE_CONNECTOR ? authMode : "none",
          credential_env_ref:
            connectorType === LIVE_CONNECTOR
              ? credentialEnvRef.trim() || null
              : null,
          config_json: buildConfigJson(),
          enabled: true,
        },
        token,
      );
      setNotice(
        `Created ${created.name} (${created.connector_type}). ` +
          (created.simulated ? "This is a SIMULATED source and never contacts a real hospital." : "Live captures use the allowlist and environment-backed credential."),
      );
      await refreshFacility();
    });
  }

  function capture(integration: FacilityIntegration): void {
    const token = requireToken();
    if (!token || !facilityId) return;
    void withBusy(async () => {
      const result = await captureIntegration(facilityId, integration.id, token);
      setCaptureResult(result);
      setNotice(
        `Captured snapshot v${result.snapshot.version_number}: ${result.change_set.status.replace(/_/g, " ")}` +
          (result.verification_job
            ? `; verification job created (${result.job_created ? "new" : "existing"}).`
            : `; ${result.job_skip_reason ?? "no verification job enqueued"}.`),
      );
      await refreshFacility();
    });
  }

  function triggerManualJob(): void {
    const token = requireToken();
    if (!token || !facilityId) return;
    void withBusy(async () => {
      const result = await enqueueManualVerificationJob(facilityId, token);
      setNotice(
        result.verification_job
          ? `Verification job ${result.created ? "created" : "already exists"} (${result.verification_job.status}).`
          : `No verification job created: ${result.reason ?? "nothing to verify"}.`,
      );
      await refreshFacility();
    });
  }

  function cancelJob(job: VerificationJob): void {
    const token = requireToken();
    if (!token || !facilityId) return;
    void withBusy(async () => {
      await cancelVerificationJob(facilityId, job.id, token);
      await refreshFacility();
    });
  }

  function toggleIntegration(integration: FacilityIntegration): void {
    const token = requireToken();
    if (!token || !facilityId) return;
    void withBusy(async () => {
      await updateIntegration(
        facilityId,
        integration.id,
        { enabled: !integration.enabled },
        token,
      );
      await refreshFacility();
    });
  }

  function showChangeSet(changeSet: ChangeSet): void {
    if (!facilityId) return;
    if (expandedChangeSet === changeSet.id) {
      setExpandedChangeSet(null);
      setChangeSetDetail(null);
      return;
    }
    setExpandedChangeSet(changeSet.id);
    void withBusy(async () => {
      const detail = await getChangeSet(facilityId, changeSet.id);
      setChangeSetDetail(detail);
    });
  }

  function toggleJob(job: VerificationJob): void {
    setExpandedJob(expandedJob === job.id ? null : job.id);
  }

  function toggleCase(item: ReconciliationCase): void {
    if (!facilityId) return;
    if (expandedCase === item.id) {
      setExpandedCase(null);
      setCaseDetail(null);
      return;
    }
    setExpandedCase(item.id);
    void withBusy(async () => {
      const detail = await getReconciliationCase(facilityId, item.id);
      setCaseDetail(detail);
    });
  }

  function transitionCase(
    item: ReconciliationCase,
    toStatus: "under_review" | "resolved" | "dismissed",
    resolution?: ReconciliationResolution,
  ): void {
    const token = requireToken();
    if (!token || !facilityId) return;
    void withBusy(async () => {
      const detail = await transitionReconciliationCase(
        facilityId,
        item.id,
        {
          to_status: toStatus,
          resolution: resolution ?? null,
          actor_label: "operator",
        },
        token,
      );
      setCaseDetail(detail);
      await refreshFacility();
    });
  }

  function createSchedule(): void {
    const token = requireToken();
    if (!token || !facilityId) return;
    const integration = integrations[0];
    if (!integration) {
      setError("Create an integration first.");
      return;
    }
    void withBusy(async () => {
      await createMonitoringSchedule(
        facilityId,
        {
          name: scheduleName.trim(),
          integration_id: integration.id,
          interval_seconds: scheduleInterval,
          enabled: true,
          target_ref: scheduleTargetRef.trim() || facility?.canonical_url || null,
        },
        token,
      );
      setNotice("Monitoring schedule created and enabled.");
      await refreshFacility();
    });
  }

  function toggleSchedule(schedule: MonitoringSchedule): void {
    const token = requireToken();
    if (!token || !facilityId) return;
    void withBusy(async () => {
      await updateMonitoringSchedule(
        facilityId,
        schedule.id,
        { enabled: !schedule.enabled },
        token,
      );
      await refreshFacility();
    });
  }

  const jobResult = (job: VerificationJob) => job.result;

  return (
    <section aria-labelledby="hospital-integration-heading" className="panel">
      <h2 id="hospital-integration-heading">Hospital source integration</h2>
      <p className="muted">
        Capture evidence from an authorized hospital-provided source, detect what changed, and verify it against
        the public website. Connectors are facility-scoped, fail closed, and never claim a live connection that
        does not exist.
      </p>

      <div className="callout callout-warning" role="status">
        Integrations connect only to explicitly authorized sources. The <code>mock_hospital_api_simulated</code>{" "}
        connector is <strong>SIMULATED</strong>: it never contacts a real hospital and exists for tests and demonstrations.
      </div>

      <div className="audit-controls">
        <label>
          Physical facility
          <select
            value={facilityId}
            onChange={(event) => setFacilityId(event.target.value)}
            disabled={loading || facilities.length === 0 || demo}
          >
            {facilities.length === 0 ? <option value="">No registered facilities</option> : null}
            {facilities.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
                {item.city ? ` — ${item.city}` : ""}
              </option>
            ))}
          </select>
        </label>
        <label>
          Operator token
          <input
            type="password"
            value={operatorToken}
            autoComplete="off"
            placeholder="MEDCOMPARE_OPERATOR_API_KEY"
            onChange={(event) => setOperatorToken(event.target.value)}
            disabled={demo}
          />
        </label>
        <button type="button" onClick={() => void refreshFacility()} disabled={!facilityId || demo || busy}>
          Refresh
        </button>
      </div>
      <p className="muted">
        The operator token is held in memory only, and is sent solely as{" "}
        <code>Authorization: Bearer …</code> to operator endpoints. Read-only lists stay open; captures, configuration,
        verification-job control, monitoring and reconciliation transitions require the token.
      </p>

      {error ? (
        <p className="callout callout-error" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? <p className="callout">{notice}</p> : null}

      {demo ? (
        <p className="callout callout-warning">
          Demonstration mode: hospital integration is unavailable with sample data. Run the server with a
          configured operator token to use the simulated or live integration path.
        </p>
      ) : null}

      {!demo && facilityId ? (
        <div className="step">
          <h3>Integrations</h3>
          <div className="form-grid">
            <label>
              Name
              <input
                type="text"
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
              />
            </label>
            <label>
              Connector
              <select
                value={connectorType}
                onChange={(event) => setConnectorType(event.target.value)}
              >
                <option value={MOCK_CONNECTOR}>mock_hospital_api_simulated (simulated)</option>
                <option value={LIVE_CONNECTOR}>hospital_rest_json (live, authorized)</option>
              </select>
            </label>
            {connectorType === MOCK_CONNECTOR ? (
              <label>
                Payload variant (simulated)
                <select
                  value={payloadVariant}
                  onChange={(event) =>
                    setPayloadVariant(
                      event.target.value as "default" | "changed" | "changed_beds"
                    )
                  }
                >
                  <option value="default">default (baseline)</option>
                  <option value="changed">changed (mixed: bed counts + new procedure)</option>
                  <option value="changed_beds">changed_beds (bed numbers only — fully verifiable)</option>
                </select>
              </label>
            ) : (
              <>
                <label>
                  Authorized base URL
                  <input
                    type="url"
                    value={baseUrl}
                    placeholder="https://api.hospital.example"
                    onChange={(event) => setBaseUrl(event.target.value)}
                  />
                </label>
                <label>
                  Allowed hosts (comma separated)
                  <input
                    type="text"
                    value={allowedHosts}
                    placeholder="api.hospital.example"
                    onChange={(event) => setAllowedHosts(event.target.value)}
                  />
                </label>
                <label>
                  Credential environment variable
                  <input
                    type="text"
                    value={credentialEnvRef}
                    placeholder="MEDCOMPARE_HOSPITAL_API_TOKEN"
                    onChange={(event) => setCredentialEnvRef(event.target.value)}
                  />
                </label>
                <label>
                  Auth mode
                  <select
                    value={authMode}
                    onChange={(event) =>
                      setAuthMode(event.target.value as IntegrationAuthMode)
                    }
                  >
                    <option value="none">none</option>
                    <option value="api_key">api_key</option>
                    <option value="bearer_token">bearer_token</option>
                  </select>
                </label>
              </>
            )}
          </div>
          <button type="button" className="primary" onClick={create} disabled={busy}>
            {busy ? "Working…" : "Create integration"}
          </button>

          {integrations.length === 0 ? (
            <p className="muted">No integrations configured for this facility yet.</p>
          ) : (
            <ul className="integration-list">
              {integrations.map((integration) => (
                <li key={integration.id} className="integration-item">
                  <div className="integration-item-header">
                    <strong>{integration.name}</strong>
                    <span className={INTEGRATION_STATUS_CLASS[integration.status]}>{integration.status}</span>
                    {integration.simulated ? <span className="chip">SIMULATED</span> : null}
                  </div>
                  <p className="muted">
                    <code>{integration.connector_type}</code>
                    {integration.base_url ? <> · {integration.base_url}</> : null}
                    {integration.credential_env_ref ? <> · credential {integration.credential_env_ref}</> : null}
                  </p>
                  <div className="integration-item-actions">
                    <button type="button" onClick={() => toggleIntegration(integration)} disabled={busy}>
                      {integration.enabled ? "Disable" : "Enable"}
                    </button>
                    <button type="button" onClick={() => capture(integration)} disabled={busy}>
                      Capture snapshot
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}

      {!demo && facilityId && snapshots.length > 0 ? (
        <div className="step">
          <h3>Snapshots (versioned, immutable)</h3>
          <ul className="plain-list">
            {snapshots.slice(0, 8).map((snapshot) => (
              <li key={snapshot.id}>
                v{snapshot.version_number} · {snapshot.source_kind} ·{" "}
                <code>{snapshot.content_hash.slice(0, 12)}…</code> ·{" "}
                {formatTimestamp(snapshot.captured_at)} ·{" "}
                {snapshot.is_simulation ? "simulated" : "live"}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {!demo && facilityId && changeSets.length > 0 ? (
        <div className="step">
          <h3>Change detection</h3>
          <ul className="plain-list">
            {changeSets.slice(0, 6).map((changeSet) => (
              <li key={changeSet.id}>
                <button
                  type="button"
                  className="link-button"
                  onClick={() => showChangeSet(changeSet)}
                >
                  {changeSet.status.replace(/_/g, " ")} — +{changeSet.added_count} / −
                  {changeSet.removed_count} / ~{changeSet.modified_count}{" "}
                  ({formatTimestamp(changeSet.detected_at)})
                </button>
                {expandedChangeSet === changeSet.id && changeSetDetail ? (
                  <div className="table-scroll">
                    <table className="matrix-table">
                      <caption className="muted">
                        {changeSetDetail.changes.length} field-level change(s)
                      </caption>
                      <thead>
                        <tr>
                          <th scope="col">Path</th>
                          <th scope="col">Type</th>
                          <th scope="col">Entity</th>
                          <th scope="col">Field</th>
                          <th scope="col">Change</th>
                        </tr>
                      </thead>
                      <tbody>
                        {changeSetDetail.changes.map((change: DetectedChange) => (
                          <tr key={change.id}>
                            <td><code>{change.path}</code></td>
                            <td>{change.change_type}</td>
                            <td>{change.entity_name || "—"}</td>
                            <td>{change.field || "—"}</td>
                            <td>
                              <code>
                                {JSON.stringify(change.old_value_json)} →{" "}
                                {JSON.stringify(change.new_value_json)}
                              </code>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {!demo && facilityId ? (
        <div className="step">
          <h3>Verification jobs</h3>
          <p className="muted">
            Operated by a separate worker process (<code>python -m hospital_extractor.worker</code>). The UI only
            enqueues and cancels jobs; the worker crawls the website (SSRF-validated) and compares with the triggering
            snapshot.
          </p>
          <button type="button" onClick={triggerManualJob} disabled={busy}>
            Enqueue verification for latest changes
          </button>
          {jobs.length === 0 ? (
            <p className="muted">No verification jobs yet.</p>
          ) : (
            <ul className="integration-list">
              {jobs.slice(0, 8).map((job) => (
                <li key={job.id} className="integration-item">
                  <div className="integration-item-header">
                    <span className={JOB_STATUS_CLASS[job.status]}>{job.status}</span>
                    <span className="muted">{job.trigger_type} · attempts {job.attempt_count}/{job.max_attempts}</span>
                    {job.attempt_count > 0 && job.status === "pending" ? (
                      <span className="muted">retrying</span>
                    ) : null}
                  </div>
                  <p className="muted">Target: {job.target_ref || "—"}</p>
                  {job.last_error_message ? (
                    <p className="callout callout-warning">{job.last_error_message}</p>
                  ) : null}
                  <div className="integration-item-actions">
                    {job.result ? (
                      <button type="button" onClick={() => toggleJob(job)}>
                        {expandedJob === job.id ? "Hide result" : "Show result"}
                      </button>
                    ) : null}
                    {job.status === "pending" || job.status === "running" ? (
                      <button type="button" onClick={() => cancelJob(job)} disabled={busy}>
                        Cancel
                      </button>
                    ) : null}
                  </div>
                  {expandedJob === job.id && jobResult(job) ? (
                    <div className="table-scroll">
                      <p className="muted">{jobResult(job)?.summary}</p>
                      <table className="matrix-table">
                        <caption className="muted">
                          Provenance: {String(
                            (jobResult(job)?.provenance_json as { website?: { crawl_run_id?: string | null } })?.website?.crawl_run_id ?? "no crawl persisted",
                          )}
                        </caption>
                        <thead>
                          <tr>
                            <th scope="col">Change</th>
                            <th scope="col">Verdict</th>
                            <th scope="col">Detail</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(((jobResult(job)?.evidence_json as { checks?: Array<Record<string, unknown>> })?.checks) ?? []).map(
                            (check, index) => (
                              <tr key={`${job.id}-${index}`}>
                                <td><code>{String(check.path)}</code></td>
                                <td>{String(check.status)}</td>
                                <td>{String(check.detail)}</td>
                              </tr>
                            ),
                          )}
                        </tbody>
                      </table>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}

      {!demo && facilityId ? (
        <div className="step">
          <h3>Reconciliation review</h3>
          {cases.length === 0 ? (
            <p className="muted">No review cases open. Insufficient-evidence or mismatch verdicts create cases automatically.</p>
          ) : (
            <ul className="integration-list">
              {cases.map((item) => (
                <li key={item.id} className="integration-item">
                  <div className="integration-item-header">
                    <span className={item.status === "resolved" || item.status === "dismissed" ? "badge badge-ok" : "badge badge-conflict"}>
                      {item.status}
                    </span>
                    <span className="muted">{item.resolution ?? "unresolved"}</span>
                  </div>
                  <p className="cell-value">{item.summary}</p>
                  <div className="integration-item-actions">
                    <button type="button" onClick={() => toggleCase(item)}>
                      {expandedCase === item.id ? "Hide" : "Review"}
                    </button>
                  </div>
                  {expandedCase === item.id ? (
                    <div>
                      {caseDetail && caseDetail.id === item.id ? (
                        <>
                          <ul className="plain-list">
                            {caseDetail.events.map((event_) => (
                              <li key={event_.id}>
                                {event_.from_status ?? "—"} → {event_.to_status} ·{" "}
                                {event_.actor_label ?? "system"} · {formatTimestamp(event_.occurred_at)}
                                {event_.note ? ` · ${event_.note}` : ""}
                              </li>
                            ))}
                          </ul>
                          <div className="integration-item-actions">
                            {item.status === "open" ? (
                              <button type="button" onClick={() => transitionCase(item, "under_review")} disabled={busy}>
                                Start review
                              </button>
                            ) : null}
                            {item.status === "open" || item.status === "under_review" ? (
                              <>
                                <button
                                  type="button"
                                  onClick={() => transitionCase(item, "resolved", "website_confirmed")}
                                  disabled={busy}
                                >
                                  Resolve: website confirmed
                                </button>
                                <button
                                  type="button"
                                  onClick={() => transitionCase(item, "resolved", "hospital_source_confirmed")}
                                  disabled={busy}
                                >
                                  Resolve: hospital source confirmed
                                </button>
                                <button
                                  type="button"
                                  onClick={() => transitionCase(item, "resolved", "insufficient_evidence")}
                                  disabled={busy}
                                >
                                  Resolve: insufficient evidence
                                </button>
                                <button type="button" onClick={() => transitionCase(item, "dismissed")} disabled={busy}>
                                  Dismiss
                                </button>
                              </>
                            ) : null}
                          </div>
                        </>
                      ) : (
                        <p className="muted">Loading case…</p>
                      )}
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}

      {!demo && facilityId ? (
        <div className="step">
          <h3>Monitoring schedules</h3>
          <p className="muted">
            The scheduler creates verification jobs for detected changes; it never crawls websites itself. Every
            decision is recorded in the execution history.
          </p>
          <div className="form-grid">
            <label>
              Name
              <input type="text" value={scheduleName} onChange={(event) => setScheduleName(event.target.value)} />
            </label>
            <label>
              Interval (seconds, min 60)
              <input
                type="number"
                min={60}
                value={scheduleInterval}
                onChange={(event) => setScheduleInterval(Number(event.target.value))}
              />
            </label>
            <label>
              Target ref (defaults to facility URL)
              <input
                type="url"
                value={scheduleTargetRef}
                placeholder={facility?.canonical_url ?? ""}
                onChange={(event) => setScheduleTargetRef(event.target.value)}
              />
            </label>
          </div>
          <button type="button" onClick={createSchedule} disabled={busy}>
            Create enabled schedule
          </button>
          {schedules.length === 0 ? (
            <p className="muted">No monitoring schedules configured.</p>
          ) : (
            <ul className="integration-list">
              {schedules.map((schedule) => (
                <li key={schedule.id} className="integration-item">
                  <div className="integration-item-header">
                    <strong>{schedule.name}</strong>
                    <span className={schedule.enabled ? "badge badge-ok" : "badge badge-missing"}>
                      {schedule.enabled ? "enabled" : "disabled"}
                    </span>
                    {schedule.last_status ? <span className="muted">last: {schedule.last_status}</span> : null}
                  </div>
                  <p className="muted">
                    every {schedule.interval_seconds}s · next run {formatTimestamp(schedule.next_run_at)}
                  </p>
                  <div className="integration-item-actions">
                    <button type="button" onClick={() => toggleSchedule(schedule)} disabled={busy}>
                      {schedule.enabled ? "Disable" : "Enable"}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {executions.length > 0 ? (
            <table className="matrix-table">
              <caption className="muted">Recent monitoring executions</caption>
              <thead>
                <tr>
                  <th scope="col">Status</th>
                  <th scope="col">Reason</th>
                  <th scope="col">Job</th>
                  <th scope="col">Executed</th>
                </tr>
              </thead>
              <tbody>
                {executions.slice(0, 10).map((execution) => (
                  <tr key={execution.id}>
                    <td>
                      <span className={EXECUTION_STATUS_CLASS[execution.status]}>{execution.status}</span>
                    </td>
                    <td>{execution.reason ?? "—"}</td>
                    <td>{execution.job_id ? <code>{execution.job_id.slice(0, 8)}…</code> : "—"}</td>
                    <td>{formatTimestamp(execution.executed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </div>
      ) : null}

      {captureResult ? (
        <div className="step">
          <h3>Last capture</h3>
          <p className="muted">
            Snapshot v{captureResult.snapshot.version_number} ({captureResult.snapshot.source_kind}) ·{" "}
            {captureResult.change_set.status.replace(/_/g, " ")} ·{" "}
            <code>{captureResult.snapshot.content_hash.slice(0, 12)}…</code>
            {captureResult.snapshot.is_simulation ? " · simulated" : ""}
          </p>
          <p className="muted">
            +{captureResult.change_set.added_count} added · −{captureResult.change_set.removed_count} removed · ~
            {captureResult.change_set.modified_count} modified
          </p>
        </div>
      ) : null}
    </section>
  );
}
