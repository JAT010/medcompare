import { API_BASE_URL } from "./api/client";
import CapabilityPanel from "./components/CapabilityPanel";
import ComparisonWorkspace from "./components/ComparisonWorkspace";
import ConsistencyAuditPanel from "./components/ConsistencyAuditPanel";

export default function App() {
  return (
    <main className="app-shell">
      <header className="app-header">
        <h1>MedCompare</h1>
        <p className="subtitle">
          Evidence-based hospital comparison: interpret criteria, select hospitals, gather evidence, and compare with
          provenance. Unsupported capabilities are shown honestly (never fabricated).
        </p>
      </header>
      <CapabilityPanel />
      <ComparisonWorkspace />
      <ConsistencyAuditPanel />
      <footer className="app-footer">
        <p className="muted">
          Live API base: <code>{API_BASE_URL}</code> · In demonstration mode, workflow uses explicitly labeled local sample
          data (not live backend/crawl/registry).
        </p>
      </footer>
    </main>
  );
}
