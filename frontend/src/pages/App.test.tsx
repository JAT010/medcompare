import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import App from "../App";
import { setAuthToken } from "../api/client";

const viewer = {
  user_id: "u1",
  username: "user@medcompare.demo",
  role: "viewer",
  permissions: ["view_hospital"],
  auth_status: "authenticated",
  development: true,
  hospital_ids: ["h_demo_northstar"],
};

const admin = {
  user_id: "u2",
  username: "admin@medcompare.demo",
  role: "admin",
  permissions: ["view_hospital", "manage_users"],
  auth_status: "authenticated",
  development: true,
  hospital_ids: [],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stub(handler: (url: string, init?: RequestInit) => Response) {
  const spy = vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
    handler(String(input), init),
  );
  vi.stubGlobal("fetch", spy);
  return spy;
}

function setPath(path: string) {
  window.history.pushState({}, "", path);
}

beforeEach(() => {
  setAuthToken(null);
  setPath("/");
});
afterEach(() => {
  setAuthToken(null);
  vi.unstubAllGlobals();
});

describe("admin dashboard", () => {
  it("loads counts and shows null overall_score", async () => {
    setPath("/admin");
    sessionStorage.setItem("medcompare_token", "tok");
    stub((url) => {
      if (url.includes("/auth/me")) return json(admin);
      if (url.includes("/hospitals/registry"))
        return json({
          hospitals: [
            { hospital_id: "h_demo_northstar", name: "Northstar", created_at: "2026-01-01" },
          ],
        });
      if (url.includes("/api/v1/hospitals") && !url.includes("registry"))
        return json({
          datasets: [
            { dataset_id: "d1", hospital_ref: "r", source_type: "mock_api", created_at: "2026-01-01" },
          ],
        });
      if (url.includes("/api/v1/users")) return json([{ user_id: "u1" }, { user_id: "u2" }]);
      if (url.includes("/api/v1/health")) return json({ status: "ok" });
      if (url.includes("/quality/pipeline"))
        return json({ report: { overall_score: null, note: "no score" } });
      return json({ detail: "nf" }, 404);
    });

    render(<App />);

    expect(await screen.findByTestId("admin-dashboard")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("hospital-count")).toHaveTextContent("1"));
    await waitFor(() => expect(screen.getByTestId("user-count")).toHaveTextContent("2"));
    await waitFor(() => expect(screen.getByTestId("health-status")).toHaveTextContent("ok"));
    expect(screen.getByTestId("quality-snippet")).toHaveTextContent("null");
    expect(screen.getByTestId("disclaimer")).toBeInTheDocument();
  });

  it("logout clears session and returns to login", async () => {
    setPath("/admin");
    sessionStorage.setItem("medcompare_token", "tok");
    const user = userEvent.setup();
    stub((url, init) => {
      if (url.includes("/auth/me")) return json(admin);
      if (url.includes("/auth/logout") && init?.method === "POST") return json({ ok: true });
      return json({ detail: "nf" }, 404);
    });

    render(<App />);

    expect(await screen.findByTestId("admin-layout")).toBeInTheDocument();
    await user.click(screen.getByTestId("logout"));
    await waitFor(() => expect(sessionStorage.getItem("medcompare_token")).toBeNull());
    expect(await screen.findByTestId("login-page")).toBeInTheDocument();
  });
});

describe("user pages", () => {
  it("home shows role and disclaimer; no ranking language", async () => {
    setPath("/");
    sessionStorage.setItem("medcompare_token", "tok");
    stub((url) => {
      if (url.includes("/auth/me")) return json(viewer);
      if (url.includes("/hospitals/registry"))
        return json({
          hospitals: [
            { hospital_id: "h_demo_northstar", name: "Northstar Demo", created_at: "2026-01-01" },
          ],
        });
      return json({ detail: "nf" }, 404);
    });

    render(<App />);

    expect(await screen.findByTestId("user-home")).toBeInTheDocument();
    expect(screen.getByTestId("user-role")).toHaveTextContent("Role: viewer");
    expect(screen.getByTestId("disclaimer")).toHaveTextContent(/no ranking/i);
    const bodyText = document.body.textContent ?? "";
    expect(bodyText.toLowerCase()).not.toContain("best hospital");
    expect(bodyText.toLowerCase()).not.toContain("ranked #");
    expect(bodyText.toLowerCase()).not.toContain("top hospital");
  });

  it("compare requires 2+ selections and renders side-by-side without score", async () => {
    setPath("/compare");
    sessionStorage.setItem("medcompare_token", "tok");
    const user = userEvent.setup();
    stub((url) => {
      if (url.includes("/auth/me")) return json(viewer);
      if (url.includes("/hospitals/registry"))
        return json({
          hospitals: [
            { hospital_id: "h1", name: "A", created_at: "2026-01-01" },
            { hospital_id: "h2", name: "B", created_at: "2026-01-01" },
          ],
        });
      if (url.includes("/search/compare"))
        return json({
          result: {
            hospitals: [
              {
                hospital_id: "h1",
                name: "A",
                dimensions: [{ dimension_key: "city", decision: "UNKNOWN" }],
              },
              {
                hospital_id: "h2",
                name: "B",
                dimensions: [{ dimension_key: "city", decision: "Pune" }],
              },
            ],
            fields: [{ dimension_key: "city" }],
            overall_score: null,
          },
        });
      return json({ detail: "nf" }, 404);
    });

    render(<App />);

    expect(await screen.findByTestId("user-compare")).toBeInTheDocument();
    expect(await screen.findByTestId("compare-check-h1")).toBeInTheDocument();
    const submit = screen.getByTestId("compare-submit") as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    await user.click(screen.getByTestId("compare-check-h1"));
    expect((screen.getByTestId("compare-submit") as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByTestId("compare-check-h2"));
    expect((screen.getByTestId("compare-submit") as HTMLButtonElement).disabled).toBe(false);

    await user.click(screen.getByTestId("compare-submit"));
    expect(await screen.findByTestId("compare-result")).toBeInTheDocument();
    expect(screen.getByTestId("unknown")).toBeInTheDocument();
    expect(screen.getByTestId("value")).toHaveTextContent("Pune");
    expect(document.body.textContent).not.toMatch(/overall_score\s*[:=]\s*[1-9]/);
  });

  it("search shows empty state when no matches", async () => {
    setPath("/search");
    sessionStorage.setItem("medcompare_token", "tok");
    const user = userEvent.setup();
    stub((url) => {
      if (url.includes("/auth/me")) return json(viewer);
      if (url.includes("/api/v1/search") && url.endsWith("/search"))
        return json({ result: { total: 0, limit: 20, offset: 0, matches: [] } });
      return json({ detail: "nf" }, 404);
    });

    render(<App />);

    expect(await screen.findByTestId("user-search")).toBeInTheDocument();
    await user.type(screen.getByTestId("search-input"), "knee");
    await user.click(screen.getByTestId("search-submit"));
    expect(await screen.findByTestId("empty")).toHaveTextContent(/no hospitals matched/i);
  });
});
