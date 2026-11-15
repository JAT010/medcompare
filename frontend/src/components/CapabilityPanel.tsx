import { useEffect, useState } from "react";
import { getCapabilities, toMessage } from "../api/client";
import { setDemoMode } from "../api/demo";
import { useDemoMode } from "../api/useDemoMode";
import type { CapabilityReport } from "../api/types";

export default function CapabilityPanel() {
  const [report, setReport] = useState<CapabilityReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const demo = useDemoMode();

  useEffect(() => {
    let cancelled = false;
    getCapabilities()
      .then((data) => {
        if (!cancelled) {
          setReport(data);
          setError(null);
          setLoading(false);
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(toMessage(cause));
          setReport(null);
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [demo]);

  function toggleDemo(): void {
    setDemoMode(!demo);
  }

  if (loading) return <p className="muted">Checking server capabilities...</p>;

  if (error && !demo) {
    return (
      <section aria-labelledby="capabilities-heading" className="panel">
        <h2 id="capabilities-heading">Server connection</h2>
        <p className="callout callout-error" role="alert">
          Cannot reach the live MedCompare server to load capabilities: {error}
        </p>
        <p className="muted">
          If PostgreSQL/backend services are unavailable, enable <strong>Demonstration mode</strong> to walk through the
          full UI flow with explicitly labeled sample data (not live API responses, not real crawls, not real registry
          evidence).
        </p>
        <button type="button" className="primary" onClick={toggleDemo}>
          Enable demonstration mode
        </button>
      </section>
    );
  }

  if (!report) return null;

  return (
    <section aria-labelledby="capabilities-heading" className="panel">
      <div className="capability-header">
        <h2 id="capabilities-heading">What this server can do</h2>
        <button type="button" onClick={toggleDemo} aria-pressed={demo}>
          {demo ? "Disable demonstration mode" : "Enable demonstration mode"}
        </button>
      </div>
      {demo ? (
        <p className="callout callout-warning" role="status">
          <strong>Demonstration mode is ON.</strong> Data shown in the workflow is locally simulated sample data for UI
          walkthrough only — not from the live backend, not a real hospital website crawl, and not from the registry
          database.
        </p>
      ) : (
        <p className="muted">
          Loaded from <code>GET /api/v1/comparison/capabilities</code>. Anything marked unavailable is reported as
          missing, never simulated.
        </p>
      )}
      <ul className="capability-list">
        {report.capabilities.map((capability) => (
          <li key={capability.key} className="capability-item">
            <div className="capability-title">
              <span>{capability.label}</span>
              <span className={capability.available ? "badge badge-ok" : "badge badge-missing"}>
                {capability.available ? "Available" : "Not available"}
              </span>
            </div>
            <p className="capability-detail">{capability.detail}</p>
            {capability.implementation ? <p className="muted">How: {capability.implementation}</p> : null}
            {!capability.available && capability.required_integration ? (
              <p className="required-integration">Missing integration: {capability.required_integration}</p>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
