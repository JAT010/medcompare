import { useEffect, useState } from "react";
import { getCapabilities, toMessage } from "../api/client";
import type { CapabilityReport } from "../api/types";

/**
 * Shows what this deployment can actually do, including the integrations that
 * are missing. Rendered from the server's capability report so the UI never
 * claims a feature the backend cannot serve.
 */
export default function CapabilityPanel() {
  const [report, setReport] = useState<CapabilityReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getCapabilities()
      .then((data) => {
        if (!cancelled) {
          setReport(data);
          setLoading(false);
        }
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(toMessage(cause));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return <p className="muted">Checking server capabilities…</p>;
  }
  if (error) {
    return (
      <p className="callout callout-error" role="alert">
        Could not load the capability report from the MedCompare server: {error}
      </p>
    );
  }
  if (!report) {
    return null;
  }

  return (
    <section aria-labelledby="capabilities-heading" className="panel">
      <h2 id="capabilities-heading">What this server can do</h2>
      <p className="muted">
        Loaded from <code>GET /api/v1/comparison/capabilities</code>. Anything
        marked unavailable is reported as missing, never simulated.
      </p>
      <ul className="capability-list">
        {report.capabilities.map((capability) => (
          <li key={capability.key} className="capability-item">
            <div className="capability-title">
              <span>{capability.label}</span>
              <span
                className={
                  capability.available ? "badge badge-ok" : "badge badge-missing"
                }
              >
                {capability.available ? "Available" : "Not available"}
              </span>
            </div>
            <p className="capability-detail">{capability.detail}</p>
            {capability.implementation ? (
              <p className="muted">How: {capability.implementation}</p>
            ) : null}
            {!capability.available && capability.required_integration ? (
              <p className="required-integration">
                Missing integration: {capability.required_integration}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
