import { API_BASE_URL } from "./api/client";
import CapabilityPanel from "./components/CapabilityPanel";
import ComparisonWorkspace from "./components/ComparisonWorkspace";

export default function App() {
  return (
    <main className="app-shell">
      <header className="app-header">
        <h1>MedCompare</h1>
        <p className="subtitle">
          Hospital comparison backed by evidence from this server, on-request
          website crawling, and nothing that does not exist.
        </p>
      </header>
      <CapabilityPanel />
      <ComparisonWorkspace />
      <footer className="app-footer">
        <p className="muted">
          API base URL: <code>{API_BASE_URL}</code>
        </p>
      </footer>
    </main>
  );
}
