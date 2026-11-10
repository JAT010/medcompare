const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export default function App() {
  return (
    <main className="app-shell">
      <header className="app-header">
        <h1>MedCompare</h1>
        <p className="subtitle">Internal hospital intelligence and recommendation platform</p>
      </header>
      <section className="app-content" aria-label="Application status">
        <p>Application scaffold initialized.</p>
        <p>
          API base URL: <code>{apiBaseUrl}</code>
        </p>
      </section>
    </main>
  );
}
