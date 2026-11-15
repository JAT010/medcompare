import type { ComparisonCell, ComparisonResponse, EvidenceStatus } from "../api/types";

const STATUS_LABELS: Record<EvidenceStatus, string> = {
  available: "Available",
  unavailable: "Not available",
  conflicting: "Conflicting",
  outdated: "Outdated",
};

const STATUS_CLASSES: Record<EvidenceStatus, string> = {
  available: "badge badge-ok",
  unavailable: "badge badge-missing",
  conflicting: "badge badge-conflict",
  outdated: "badge badge-outdated",
};

function sourceLabel(cell: ComparisonCell): string | null {
  if (cell.source_name) return cell.source_name;
  switch (cell.source) {
    case "medcompare_server":
      return "MedCompare server";
    case "website_crawl":
      return "Hospital website (crawled)";
    case "public_research":
      return "Public internet research";
    case "user_provided":
      return "Provided by you";
    default:
      return null;
  }
}

function provenanceDate(cell: ComparisonCell): string | null {
  const raw = cell.retrieved_at ?? cell.published_at;
  return raw ? raw.slice(0, 10) : null;
}

function Cell({ cell }: { cell: ComparisonCell }) {
  const label = sourceLabel(cell);
  const date = provenanceDate(cell);
  return (
    <td>
      <span className={STATUS_CLASSES[cell.status]}>{STATUS_LABELS[cell.status]}</span>
      {cell.value ? <p className="cell-value">{cell.value}</p> : null}
      {!cell.value && cell.status === "unavailable" ? (
        <p className="cell-value muted">Nothing available to show</p>
      ) : null}
      {cell.explanation ? <p className="cell-explanation">{cell.explanation}</p> : null}
      {label || date ? (
        <p className="cell-provenance">
          {cell.source_url && label ? (
            <a href={cell.source_url} target="_blank" rel="noreferrer">
              {label}
            </a>
          ) : (
            label
          )}
          {date ? <span className="muted"> · {date}</span> : null}
        </p>
      ) : null}
    </td>
  );
}

export default function ComparisonMatrix({ result }: { result: ComparisonResponse }) {
  return (
    <section aria-labelledby="comparison-result-heading" className="panel result-panel">
      <h2 id="comparison-result-heading">Comparison result</h2>

      <p className="callout" role="note" data-testid="disclaimer">
        {result.disclaimer}
      </p>

      {result.warnings.length > 0 ? (
        <div className="callout callout-warning" role="status">
          <ul className="plain-list">
            {result.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <h3>Evidence sources</h3>
      <ul className="plain-list source-status-list">
        {result.source_statuses.map((status) => (
          <li key={status.source}>
            <span className={status.status === "active" ? "badge badge-ok" : "badge badge-missing"}>
              {status.status === "active"
                ? "Active"
                : status.status === "not_configured"
                  ? "Not configured"
                  : "Unsupported"}
            </span>{" "}
            <strong>{status.source.replaceAll("_", " ")}</strong> – {status.detail}
            {status.required_integration ? (
              <span className="required-integration"> Missing integration: {status.required_integration}</span>
            ) : null}
          </li>
        ))}
      </ul>

      <h3>Coverage</h3>
      <ul className="plain-list coverage-list">
        {result.coverage.map((entry) => {
          const column = result.columns.find((item) => item.column_id === entry.column_id);
          const percent = Math.round(entry.coverage_ratio * 100);
          return (
            <li key={entry.column_id}>
              <strong>{column?.name ?? entry.column_id}</strong>: {entry.criteria_with_evidence} of {entry.criteria_total} criteria have evidence ({percent}%)
            </li>
          );
        })}
      </ul>

      <div className="table-scroll">
        <table className="matrix-table">
          <caption className="muted">
            Side-by-side evidence. Missing information is shown as not available, never as a negative finding.
          </caption>
          <thead>
            <tr>
              <th scope="col">Criterion</th>
              {result.columns.map((column) => (
                <th scope="col" key={column.column_id}>
                  {column.name}
                  {column.location ? <span className="column-subtitle">{column.location}</span> : null}
                  {column.identity_note ? <span className="column-note">{column.identity_note}</span> : null}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row) => (
              <tr key={row.criterion.key}>
                <th scope="row">
                  {row.criterion.label}
                  <span className="criterion-origin">{row.criterion.origin}</span>
                </th>
                {result.columns.map((column, index) => {
                  const cell = row.cells[index];
                  if (!cell) {
                    return (
                      <td key={column.column_id}>
                        <span className="badge badge-missing">Not available</span>
                      </td>
                    );
                  }
                  return <Cell key={column.column_id} cell={cell} />;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
