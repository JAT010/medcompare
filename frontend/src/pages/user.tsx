import { FormEvent, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { useAsync } from "../hooks/useAsync";
import {
  Badge,
  Card,
  Disclaimer,
  EmptyState,
  ErrorState,
  EvidenceValue,
  Loading,
} from "../components/ui";

export function UserHomePage() {
  const { user } = useAuth();
  const registry = useAsync(() => api.hospitalRegistry(), []);
  return (
    <div data-testid="user-home" style={{ display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>Welcome{user ? `, ${user.username}` : ""}</h1>
      <Disclaimer />
      <Card title="Hospitals in the store">
        {registry.loading ? <Loading /> : null}
        {registry.error ? <ErrorState message={registry.error.message} /> : null}
        {registry.data?.hospitals.length === 0 ? (
          <EmptyState message="No hospitals yet." />
        ) : (
          <ul data-testid="home-hospitals" style={{ margin: 0, paddingLeft: 18 }}>
            {registry.data?.hospitals.map((h) => (
              <li key={h.hospital_id}>
                <Link to={`/hospitals/${encodeURIComponent(h.hospital_id)}`}>
                  {h.name || h.hospital_id}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card title="Your access">
        <p data-testid="user-role">Role: {user?.role}</p>
        <p>Granted hospitals: {user?.hospital_ids.join(", ") || "all (admin)"}</p>
      </Card>
    </div>
  );
}

function FieldList({ fields }: { fields: unknown }) {
  if (!Array.isArray(fields) || fields.length === 0) return null;
  const list = fields as Array<Record<string, unknown>>;
  return (
    <div style={{ marginTop: 4 }}>
      {list.slice(0, 8).map((f, j) => (
        <div key={j} style={{ fontSize: 13 }}>
          {String(f.field ?? f.dimension_key ?? "")}:{" "}
          <EvidenceValue value={f.unknown === true ? "UNKNOWN" : f.value ?? f.decision} />
        </div>
      ))}
    </div>
  );
}

function EvidenceList({ evidence }: { evidence: unknown }) {
  if (!Array.isArray(evidence) || evidence.length === 0) return null;
  const list = evidence as Array<Record<string, unknown>>;
  return (
    <ul
      data-testid="search-evidence"
      style={{ margin: "4px 0 0", paddingLeft: 18, fontSize: 12, color: "#6b7280" }}
    >
      {list.slice(0, 3).map((ev, j) => (
        <li key={j}>
          {String(ev.source_url || ev.page_title || "stored dataset")} ·{" "}
          {String(ev.source_type || "—")}
          {ev.captured_at ? ` · captured ${String(ev.captured_at)}` : ""}
          {ev.extraction_method ? ` · ${String(ev.extraction_method)}` : ""}
          {ev.confidence !== null && ev.confidence !== undefined
            ? ` · confidence ${Number(ev.confidence).toFixed(2)}`
            : ""}
        </li>
      ))}
    </ul>
  );
}

export function UserSearchPage() {
  const [text, setText] = useState("");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await api.search({ text: text || undefined, limit: 20 });
      setResult(res.result as unknown as Record<string, unknown>);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  const matches = (result?.matches as Array<Record<string, unknown>> | undefined) ?? [];

  return (
    <div data-testid="user-search" style={{ display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>Search</h1>
      <Disclaimer />
      <Card title="Structured search">
        <form onSubmit={onSubmit} style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input
            data-testid="search-input"
            placeholder="e.g. Knee or city name"
            value={text}
            onChange={(e) => setText(e.target.value)}
            style={{ flex: 1, minWidth: 200 }}
          />
          <button type="submit" data-testid="search-submit">
            Search
          </button>
        </form>
        {loading ? <Loading label="Searching…" /> : null}
        {error ? <ErrorState message={error} /> : null}
        {result ? (
          <div data-testid="search-results" style={{ marginTop: 12 }}>
            <p>
              Total matches: <strong>{String(result.total)}</strong>
            </p>
            {matches.length === 0 ? (
              <EmptyState message="No hospitals matched this query." />
            ) : (
              <ul style={{ paddingLeft: 18 }}>
                {matches.map((m, i) => (
                  <li key={i} data-testid={`search-match-${i}`} style={{ marginBottom: 12 }}>
                    <strong>{String(m.hospital_name || m.name || m.hospital_id || "Hospital")}</strong>
                    <div style={{ fontSize: 12, color: "#6b7280" }}>
                      source: {String(m.source_type || "—")} · id: {String(m.hospital_id || "—")} ·
                      captured: {String(m.captured_at || "—")} · records:{" "}
                      {String(m.record_count ?? "—")}
                    </div>
                    <FieldList fields={m.fields} />
                    <FieldList fields={m.matches} />
                    <EvidenceList evidence={m.evidence} />
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : null}
      </Card>
    </div>
  );
}

interface CompareColumn {
  id: string;
  name: string;
}

interface CompareCell {
  value: unknown;
}

interface CompareRow {
  key: string;
  label: string;
  cells: CompareCell[];
}

/**
 * Normalizes either comparison response shape into columns + rows:
 * - stored-field shape: { hospital_ids, hospital_names, fields: [{ field, label, cells: [{ hospital_id, value, unknown }] }] }
 * - dimension shape:    { hospitals: [{ hospital_id, name, dimensions: [{ dimension_key, decision }] }], fields: [{ dimension_key }] }
 */
function normalizeCompare(
  result: Record<string, unknown> | null,
): { columns: CompareColumn[]; rows: CompareRow[] } {
  if (!result) return { columns: [], rows: [] };

  const hospitalList = (Array.isArray(result.hospitals) ? result.hospitals : []) as Array<
    Record<string, unknown>
  >;
  const idList = (Array.isArray(result.hospital_ids) ? result.hospital_ids : []) as unknown[];
  const nameMap = (result.hospital_names ?? {}) as Record<string, unknown>;
  const fields = (Array.isArray(result.fields) ? result.fields : []) as Array<
    Record<string, unknown>
  >;
  const storedFields = fields.filter((f) => Array.isArray(f.cells));
  const firstCells = (storedFields[0]?.cells ?? []) as Array<Record<string, unknown>>;

  let columns: CompareColumn[] = [];
  if (idList.length) {
    columns = idList.map((raw) => {
      const id = String(raw);
      return { id, name: String(nameMap[id] ?? id) };
    });
  } else if (hospitalList.length) {
    columns = hospitalList.map((h, i) => ({
      id: String(h.hospital_id ?? i),
      name: String(h.name ?? h.hospital_id ?? `Hospital ${i + 1}`),
    }));
  } else {
    columns = firstCells.map((c, i) => ({
      id: String(c.hospital_id ?? i),
      name: String(c.hospital_name ?? c.hospital_id ?? `Hospital ${i + 1}`),
    }));
  }

  if (storedFields.length) {
    const rows = storedFields.map((f) => {
      const byId = new Map(
        ((f.cells as Array<Record<string, unknown>>) ?? []).map((c) => [String(c.hospital_id), c]),
      );
      return {
        key: String(f.field ?? f.label ?? ""),
        label: String(f.label ?? f.field ?? ""),
        cells: columns.map((col) => {
          const cell = byId.get(col.id);
          if (!cell || cell.unknown === true || cell.value === null || cell.value === undefined) {
            return { value: "UNKNOWN" };
          }
          return { value: cell.value };
        }),
      };
    });
    return { columns, rows };
  }

  const dimensions = (hospitalList[0]?.dimensions ?? []) as Array<Record<string, unknown>>;
  const keys = fields.length
    ? fields.map((f) => String(f.dimension_key ?? f.field ?? ""))
    : dimensions.map((d) => String(d.dimension_key ?? ""));
  const rows = keys.map((key) => ({
    key,
    label: key,
    cells: hospitalList.map((h) => {
      const dim = ((h.dimensions ?? []) as Array<Record<string, unknown>>).find(
        (d) => String(d.dimension_key) === key,
      );
      const decision = dim?.decision ?? h[key];
      if (decision === undefined || decision === null || decision === "unknown") {
        return { value: "UNKNOWN" };
      }
      return { value: decision };
    }),
  }));
  return { columns, rows };
}

export function UserComparePage() {
  const registry = useAsync(() => api.hospitalRegistry(), []);
  const hospitals = registry.data?.hospitals ?? [];
  const [selected, setSelected] = useState<string[]>([]);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const canCompare = selected.length >= 2 && selected.length <= 4;

  function toggle(id: string) {
    setSelected((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= 4) return prev;
      return [...prev, id];
    });
  }

  async function runCompare(e: FormEvent) {
    e.preventDefault();
    if (!canCompare) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.compare({ hospital_ids: selected });
      setResult(res.result as unknown as Record<string, unknown>);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  const { columns, rows } = useMemo(() => normalizeCompare(result), [result]);
  const note = String(
    result?.note ??
      "Field comparison only. No ranking, winner, or medical-superiority claim — every cell is evidence from a stored dataset.",
  );

  return (
    <div data-testid="user-compare" style={{ display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>Compare hospitals</h1>
      <Disclaimer />
      <Card title="Select 2–4 hospitals">
        {registry.loading ? <Loading /> : null}
        <ul data-testid="compare-picker" style={{ listStyle: "none", paddingLeft: 0 }}>
          {hospitals.map((h) => (
            <li key={h.hospital_id} style={{ marginBottom: 6 }}>
              <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input
                  type="checkbox"
                  data-testid={`compare-check-${h.hospital_id}`}
                  checked={selected.includes(h.hospital_id)}
                  onChange={() => toggle(h.hospital_id)}
                />
                {h.name || h.hospital_id}
              </label>
            </li>
          ))}
        </ul>
        <form onSubmit={runCompare}>
          <button type="submit" data-testid="compare-submit" disabled={!canCompare}>
            Compare ({selected.length}/4)
          </button>
        </form>
        {loading ? <Loading /> : null}
        {error ? <ErrorState message={error} /> : null}
      </Card>
      {result ? (
        <Card title="Side-by-side (no ranking)">
          <div data-testid="compare-result" style={{ overflowX: "auto" }}>
            <table style={{ borderCollapse: "collapse", fontSize: 13, width: "100%" }}>
              <thead>
                <tr>
                  <th align="left">Field</th>
                  {columns.map((c, i) => (
                    <th key={i} align="left">
                      {c.name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.key} style={{ borderTop: "1px solid #e5e7eb" }}>
                    <td>{row.label}</td>
                    {row.cells.map((cell, i) => (
                      <td key={i}>
                        <EvidenceValue value={cell.value} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length === 0 ? (
              <EmptyState message="No comparable fields were returned for this selection." />
            ) : null}
            <p style={{ fontSize: 12, color: "#6b7280" }}>
              UNKNOWN means the field was not published for that hospital — never treat it as NO.
              No winner or score is computed.
            </p>
            <p style={{ fontSize: 12, color: "#6b7280" }}>{note}</p>
          </div>
        </Card>
      ) : null}
    </div>
  );
}

export function UserHospitalsPage() {
  const { user } = useAuth();
  const registry = useAsync(() => api.hospitalRegistry(), []);
  const granted = user?.role === "admin" ? null : new Set(user?.hospital_ids ?? []);
  const visible = (registry.data?.hospitals ?? []).filter(
    (h) => !granted || granted.has(h.hospital_id),
  );

  return (
    <div data-testid="user-hospitals" style={{ display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>My hospitals</h1>
      <Disclaimer />
      <Card title="Granted to your account">
        {registry.loading ? <Loading /> : null}
        {visible.length === 0 && !registry.loading ? (
          <EmptyState message="No hospital grants for your account." />
        ) : (
          <ul data-testid="granted-hospitals" style={{ margin: 0, paddingLeft: 18 }}>
            {visible.map((h) => (
              <li key={h.hospital_id}>
                <Link to={`/hospitals/${encodeURIComponent(h.hospital_id)}`}>
                  {h.name || h.hospital_id}
                </Link>{" "}
                <code>{h.hospital_id}</code>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

export function UserQualityPage() {
  const pipeline = useAsync(() => api.qualityPipeline(), []);
  return (
    <div data-testid="user-quality" style={{ display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>Quality</h1>
      <Disclaimer />
      <Card title="Pipeline report">
        {pipeline.loading ? <Loading /> : null}
        {pipeline.error ? <ErrorState message={pipeline.error.message} /> : null}
        {pipeline.data ? (
          <div data-testid="user-quality-report">
            <p>
              overall_score: <strong>{pipeline.data.report.overall_score === null ? "null" : String(pipeline.data.report.overall_score)}</strong>
            </p>
            <pre style={{ fontSize: 12, overflow: "auto" }}>
              {JSON.stringify(pipeline.data.report, null, 2)}
            </pre>
          </div>
        ) : null}
      </Card>
    </div>
  );
}

export function UserReportsPage() {
  const { user } = useAuth();
  const registry = useAsync(() => api.hospitalRegistry(), []);
  const hospitals = registry.data?.hospitals ?? [];
  const granted =
    user?.role === "admin"
      ? hospitals.map((h) => h.hospital_id)
      : hospitals.map((h) => h.hospital_id).filter((id) => (user?.hospital_ids ?? []).includes(id));
  const [hospitalId, setHospitalId] = useState("");
  const active = hospitalId || granted[0] || "";
  const reports = useAsync(
    () => (active ? api.hospitalReports(active) : Promise.resolve({ reports: [] })),
    [active],
  );

  return (
    <div data-testid="user-reports" style={{ display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>Reports</h1>
      <Disclaimer />
      <Card title="Evidence reports for your hospitals">
        <label style={{ display: "grid", gap: 4, fontSize: 13, marginBottom: 12 }}>
          Hospital
          <select data-testid="user-reports-hospital" value={active} onChange={(e) => setHospitalId(e.target.value)}>
            {granted.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </label>
        {reports.loading ? <Loading /> : null}
        {reports.error ? <ErrorState message={reports.error.message} /> : null}
        {reports.data?.reports.length === 0 ? (
          <EmptyState message="No reports available." />
        ) : (
          <ul data-testid="user-reports-list" style={{ margin: 0, paddingLeft: 18 }}>
            {reports.data?.reports.map((r) => (
              <li key={r.report_id}>
                <code>{r.report_id}</code> — {r.report_type} ({r.status})
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

export function UserHospitalDetailPage() {
  const { hospitalId = "" } = useParams<{ hospitalId: string }>();
  const { user } = useAuth();
  const granted = user?.role === "admin" || (user?.hospital_ids ?? []).includes(hospitalId);

  const registry = useAsync(() => api.hospitalRegistry(), []);
  const snapshots = useAsync(
    () =>
      hospitalId
        ? api.hospitalSnapshots(hospitalId)
        : Promise.resolve({ hospital_id: "", snapshots: [], change_sets: [] }),
    [hospitalId],
  );
  const changes = useAsync(
    () =>
      hospitalId
        ? api.hospitalChanges(hospitalId)
        : Promise.resolve({ hospital_id: "", change_sets: [] }),
    [hospitalId],
  );
  const quality = useAsync(
    () => (hospitalId ? api.qualityHospital(hospitalId) : Promise.resolve({ report: {} })),
    [hospitalId],
  );

  const entry = (registry.data?.hospitals ?? []).find((h) => h.hospital_id === hospitalId);
  const snapshotRows = (snapshots.data?.snapshots ?? []) as Array<Record<string, unknown>>;
  const changeRows = (changes.data?.change_sets ?? []) as Array<Record<string, unknown>>;
  const report = (quality.data?.report ?? null) as Record<string, unknown> | null;
  const metrics =
    report && Array.isArray(report.metrics)
      ? (report.metrics as Array<Record<string, unknown>>)
      : [];
  const captured = snapshotRows
    .map((s) => String(s.captured_at || ""))
    .filter(Boolean)
    .sort();
  const latest = captured.length ? captured[captured.length - 1] : null;
  const evidence: Array<Record<string, unknown>> = changeRows
    .flatMap((cs) => {
      const list = Array.isArray(cs.changes) ? (cs.changes as Array<Record<string, unknown>>) : [];
      return list.map((ch) => ({ ...ch, change_set_id: cs.change_set_id }));
    })
    .slice(0, 20);

  return (
    <div data-testid="user-hospital-detail" style={{ display: "grid", gap: 16 }}>
      <h1 style={{ margin: 0 }}>{entry?.name || hospitalId || "Hospital"}</h1>
      <Disclaimer />
      {!hospitalId ? <EmptyState message="No hospital selected." /> : null}
      {hospitalId && registry.loading ? <Loading /> : null}
      {hospitalId && !registry.loading && !granted ? (
        <EmptyState message="This hospital is not granted to your account." />
      ) : null}
      {hospitalId && granted ? (
        <>
          <Card title="Identity">
            <dl
              style={{
                margin: 0,
                display: "grid",
                gridTemplateColumns: "max-content 1fr",
                gap: "4px 16px",
                fontSize: 13,
              }}
            >
              <dt>Hospital ID</dt>
              <dd style={{ margin: 0 }}>
                <code>{hospitalId}</code>
              </dd>
              <dt>Name</dt>
              <dd style={{ margin: 0 }}>{entry?.name || "—"}</dd>
              <dt>Registered</dt>
              <dd style={{ margin: 0 }}>{entry?.created_at || "—"}</dd>
              <dt>Datasets / snapshots / sources</dt>
              <dd style={{ margin: 0 }}>
                {entry?.dataset_count ?? "—"} / {entry?.snapshot_count ?? "—"} /{" "}
                {entry?.source_count ?? "—"}
              </dd>
            </dl>
          </Card>

          <Card title="Sources & datasets">
            {snapshots.loading ? <Loading /> : null}
            {snapshots.error ? (
              <ErrorState
                message={snapshots.error.message}
                status={(snapshots.error as { status?: number }).status}
              />
            ) : null}
            {snapshotRows.length === 0 && !snapshots.loading ? (
              <EmptyState message="No snapshots captured yet." />
            ) : (
              <table
                data-testid="detail-snapshots"
                style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}
              >
                <thead>
                  <tr>
                    <th align="left">Source</th>
                    <th align="left">Reference</th>
                    <th align="left">Captured at</th>
                    <th align="left">Records</th>
                    <th align="left">Status</th>
                    <th align="left">Evidence hash</th>
                  </tr>
                </thead>
                <tbody>
                  {snapshotRows.map((s) => (
                    <tr key={String(s.snapshot_id)} style={{ borderTop: "1px solid #e5e7eb" }}>
                      <td>
                        <Badge tone={String(s.source_type) === "mock_api" ? "warn" : "neutral"}>
                          {String(s.source_type)}
                        </Badge>
                      </td>
                      <td style={{ fontSize: 12 }}>{String(s.hospital_ref || "—")}</td>
                      <td>{String(s.captured_at || "—")}</td>
                      <td>{String(s.record_count ?? "—")}</td>
                      <td>{String(s.status || "—")}</td>
                      <td>
                        <code style={{ fontSize: 11 }}>
                          {String(s.content_hash || "").slice(0, 12) || "—"}
                        </code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p data-testid="detail-freshness" style={{ fontSize: 12, color: "#6b7280" }}>
              Freshness — latest capture: {latest || "unknown"}
            </p>
          </Card>

          <Card title="Evidence & provenance">
            {changes.loading ? <Loading /> : null}
            {evidence.length === 0 && !changes.loading ? (
              <EmptyState message="No field-level changes recorded for this hospital." />
            ) : (
              <ul data-testid="detail-evidence" style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                {evidence.map((ch, i) => (
                  <li key={i} style={{ marginBottom: 8 }}>
                    <code>{String(ch.path || ch.field || "field")}</code> —{" "}
                    <Badge tone="warn">{String(ch.change_type || "changed")}</Badge>
                    <div style={{ fontSize: 12, color: "#6b7280" }}>
                      {String(ch.old_value ?? "—")} → {String(ch.new_value ?? "—")} · source:{" "}
                      {String(ch.source_type || "—")} · captured {String(ch.captured_at || "—")} ·
                      evidence: <code>{String(ch.source_url || "—")}</code> · change set{" "}
                      <code>{String(ch.change_set_id || "—")}</code>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Change history">
            {changeRows.length === 0 && !changes.loading ? (
              <EmptyState message="No change sets." />
            ) : (
              <ul data-testid="detail-changes" style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                {changeRows.map((cs) => (
                  <li key={String(cs.change_set_id)} style={{ marginBottom: 6 }}>
                    <code>{String(cs.change_set_id)}</code>{" "}
                    <Badge tone={String(cs.status) === "changes_detected" ? "warn" : "ok"}>
                      {String(cs.status || "unknown")}
                    </Badge>{" "}
                    <span style={{ fontSize: 12, color: "#6b7280" }}>
                      {String(cs.detected_at || "—")} · +{String(cs.added ?? 0)} / ~
                      {String(cs.modified ?? 0)} / -{String(cs.removed ?? 0)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Quality (no overall score)">
            {quality.loading ? <Loading /> : null}
            {quality.error ? (
              <ErrorState
                message={quality.error.message}
                status={(quality.error as { status?: number }).status}
              />
            ) : null}
            {report ? (
              <div data-testid="detail-quality">
                <p>
                  overall_score:{" "}
                  <strong>
                    {report.overall_score === null || report.overall_score === undefined
                      ? "null"
                      : String(report.overall_score)}
                  </strong>
                </p>
                <p style={{ fontSize: 12, color: "#6b7280" }}>{String(report.note || "")}</p>
                <dl
                  style={{
                    margin: 0,
                    display: "grid",
                    gridTemplateColumns: "max-content 1fr",
                    gap: "2px 16px",
                    fontSize: 12,
                  }}
                >
                  {metrics.map((m) => (
                    <div key={String(m.name)} style={{ display: "contents" }}>
                      <dt>{String(m.label || m.name)}</dt>
                      <dd style={{ margin: 0 }}>{String(m.value)}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            ) : null}
          </Card>
        </>
      ) : null}
    </div>
  );
}
