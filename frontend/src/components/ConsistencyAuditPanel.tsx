import { useEffect, useRef, useState } from "react";
import { auditConsistency, searchFacilities, toMessage } from "../api/client";
import type {
  AuditFinding,
  AuditSourceValue,
  AuditStatus,
  ConsistencyAuditResponse,
  Facility,
} from "../api/types";
import { useDemoMode } from "../api/useDemoMode";

const STATUS_LABELS: Record<AuditStatus, string> = {
  consistent: "Consistent",
  differs: "Differs",
  missing_from_website: "Not found on website",
  missing_from_internal_record: "Not stored internally",
  potentially_outdated: "Potentially outdated",
  unverifiable: "Unverifiable",
};

const STATUS_CLASSES: Record<AuditStatus, string> = {
  consistent: "badge badge-ok",
  differs: "badge badge-conflict",
  missing_from_website: "badge badge-missing",
  missing_from_internal_record: "badge badge-missing",
  potentially_outdated: "badge badge-outdated",
  unverifiable: "badge badge-missing",
};

function formatTimestamp(value: string | null): string {
  if (!value) return "timestamp not recorded";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function formatConfidence(value: number | null): string {
  if (value === null) return "confidence not recorded";
  return `confidence ${Math.round(value * 100)}%`;
}

function SourceCell({ value }: { value: AuditSourceValue | null }) {
  if (!value) {
    return <span className="muted">Not present in this source.</span>;
  }
  return (
    <div>
      <p className="cell-value">{value.value}</p>
      <p className="cell-provenance muted">
        {value.source_name} · {formatTimestamp(value.retrieved_at)} · {formatConfidence(value.confidence)}
      </p>
      {value.source_url ? (
        <a href={value.source_url} target="_blank" rel="noreferrer noopener">
          Source
        </a>
      ) : null}
      {value.note ? <p className="cell-explanation">{value.note}</p> : null}
    </div>
  );
}

function FindingRow({ finding }: { finding: AuditFinding }) {
  return (
    <tr>
      <th scope="row">
        {finding.label}
        {finding.requires_authorized_integration ? (
          <span className="criterion-origin">Requires authorized hospital integration</span>
        ) : null}
      </th>
      <td>
        <span className={STATUS_CLASSES[finding.status]}>{STATUS_LABELS[finding.status]}</span>
      </td>
      <td>
        <SourceCell value={finding.internal} />
      </td>
      <td>
        <SourceCell value={finding.website} />
      </td>
      <td>
        <p className="cell-explanation">{finding.explanation}</p>
      </td>
    </tr>
  );
}

export default function ConsistencyAuditPanel() {
  const demo = useDemoMode();
  const [facilities, setFacilities] = useState<Facility[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [audit, setAudit] = useState<ConsistencyAuditResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingFacilities, setLoadingFacilities] = useState(true);
  const [running, setRunning] = useState(false);
  const demoRef = useRef(demo);

  useEffect(() => {
    let cancelled = false;
    if (demoRef.current !== demo) {
      demoRef.current = demo;
      setLoadingFacilities(true);
      setAudit(null);
      setError(null);
      setFacilities([]);
      setSelectedId("");
    }
    searchFacilities("")
      .then((data) => {
        if (cancelled) return;
        setFacilities(data.items);
        setSelectedId(data.items[0]?.id ?? "");
        setLoadingFacilities(false);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(toMessage(cause));
        setFacilities([]);
        setSelectedId("");
        setLoadingFacilities(false);
      });
    return () => {
      cancelled = true;
    };
  }, [demo]);

  function runAudit(): void {
    if (!selectedId) return;
    setRunning(true);
    setError(null);
    auditConsistency({ facility_id: selectedId })
      .then((data) => {
        setAudit(data);
        setRunning(false);
      })
      .catch((cause: unknown) => {
        setError(toMessage(cause));
        setAudit(null);
        setRunning(false);
      });
  }

  return (
    <section aria-labelledby="consistency-heading" className="panel">
      <h2 id="consistency-heading">Database-to-website consistency audit</h2>
      <p className="muted">
        Compares the stored record for one physical facility with its latest usable website extraction. Findings
        preserve provenance and never treat missing information as proof of absence.
      </p>

      <div className="audit-controls">
        <label>
          Physical facility
          <select
            value={selectedId}
            onChange={(event) => setSelectedId(event.target.value)}
            disabled={loadingFacilities || facilities.length === 0}
          >
            {facilities.length === 0 ? <option value="">No registered facilities</option> : null}
            {facilities.map((facility) => (
              <option key={facility.id} value={facility.id}>
                {facility.name}
                {facility.city ? ` — ${facility.city}` : ""}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="primary"
          onClick={runAudit}
          disabled={!selectedId || running || loadingFacilities}
        >
          {running ? "Auditing..." : "Run consistency audit"}
        </button>
      </div>

      {error ? (
        <p className="callout callout-error" role="alert">
          {error}
        </p>
      ) : null}

      {audit ? (
        <div className="audit-result">
          <p className="muted">{audit.branch_identity_note}</p>
          <ul className="chip-list">
            <li>Total: {audit.summary.findings_total}</li>
            <li>Consistent: {audit.summary.consistent}</li>
            <li>Differs: {audit.summary.differs}</li>
            <li>Not on website: {audit.summary.missing_from_website}</li>
            <li>Not stored internally: {audit.summary.missing_from_internal_record}</li>
            <li>Potentially outdated: {audit.summary.potentially_outdated}</li>
            <li>Unverifiable: {audit.summary.unverifiable}</li>
          </ul>

          {audit.website_snapshot ? (
            <p className="muted">
              Website snapshot: run <code>{audit.website_snapshot.crawl_run_id}</code>, retrieved{" "}
              {formatTimestamp(audit.website_snapshot.retrieved_at)} ·{" "}
              {audit.website_snapshot.evidence_count} evidence item
              {audit.website_snapshot.evidence_count === 1 ? "" : "s"}
            </p>
          ) : (
            <p className="callout callout-warning">
              No usable persisted website extraction exists for this facility. Run a website crawl first.
            </p>
          )}

          <div className="table-scroll">
            <table className="matrix-table audit-table">
              <caption className="muted">{audit.disclaimer}</caption>
              <thead>
                <tr>
                  <th scope="col">Field</th>
                  <th scope="col">Status</th>
                  <th scope="col">Internal record</th>
                  <th scope="col">Website extraction</th>
                  <th scope="col">Explanation</th>
                </tr>
              </thead>
              <tbody>
                {audit.findings.map((finding) => (
                  <FindingRow key={finding.field_key} finding={finding} />
                ))}
              </tbody>
            </table>
          </div>

          {audit.warnings.length > 0 ? (
            <div className="callout callout-warning">
              <ul className="plain-list">
                {audit.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
