import type { ReactNode } from "react";

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "ok" | "warn" | "bad" | "info";
}) {
  const colors: Record<string, string> = {
    neutral: "#6b7280",
    ok: "#047857",
    warn: "#b45309",
    bad: "#b91c1c",
    info: "#1d4ed8",
  };
  const bg: Record<string, string> = {
    neutral: "#f3f4f6",
    ok: "#ecfdf5",
    warn: "#fffbeb",
    bad: "#fef2f2",
    info: "#eff6ff",
  };
  return (
    <span
      data-testid="badge"
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: 12,
        fontWeight: 600,
        color: colors[tone],
        background: bg[tone],
        border: `1px solid ${colors[tone]}33`,
      }}
    >
      {children}
    </span>
  );
}

export function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section style={{ border: "1px solid #e5e7eb", borderRadius: 8, padding: 16, background: "#fff" }}>
      {title ? <h2 style={{ margin: "0 0 12px", fontSize: 16 }}>{title}</h2> : null}
      {children}
    </section>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" data-testid="loading" style={{ padding: 16, color: "#6b7280" }}>
      {label}
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div data-testid="empty" style={{ padding: 16, color: "#6b7280", fontStyle: "italic" }}>
      {message}
    </div>
  );
}

export function ErrorState({ message, status }: { message: string; status?: number }) {
  return (
    <div
      role="alert"
      data-testid="error"
      style={{
        padding: 12,
        borderRadius: 6,
        background: "#fef2f2",
        border: "1px solid #fecaca",
        color: "#b91c1c",
      }}
    >
      {status ? <strong>HTTP {status}</strong> : null} {status ? " — " : ""}
      {message}
    </div>
  );
}

/** Renders UNKNOWN distinctly from NO / empty. Never ranks or scores. */
export function EvidenceValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "" || value === "UNKNOWN") {
    return (
      <span data-testid="unknown" title="Not published for this hospital" style={{ color: "#92400e", fontWeight: 600 }}>
        UNKNOWN
      </span>
    );
  }
  if (value === false || value === "NO" || value === "no") {
    return (
      <span data-testid="no" style={{ color: "#b91c1c" }}>
        NO
      </span>
    );
  }
  if (value === true || value === "YES" || value === "yes") {
    return (
      <span data-testid="yes" style={{ color: "#047857" }}>
        YES
      </span>
    );
  }
  return <span data-testid="value">{String(value)}</span>;
}

export function Disclaimer({ compact }: { compact?: boolean }) {
  return (
    <p
      data-testid="disclaimer"
      style={{
        margin: compact ? "4px 0" : "12px 0",
        fontSize: 12,
        color: "#6b7280",
        lineHeight: 1.4,
      }}
    >
      Evidence from stored hospital datasets. No ranking, winner, or medical-superiority
      claim. Quality overall_score is always null by design. Real hospital API/feed
      integrations require an authorized source configuration and credentials and are not
      included by default. Demo data is fictional and labeled DEMO.
    </p>
  );
}
