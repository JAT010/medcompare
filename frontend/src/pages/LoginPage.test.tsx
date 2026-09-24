import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import App from "../App";
import { LoginPage } from "../pages/LoginPage";
import { setAuthToken, ApiError } from "../api/client";

const okUser = {
  user_id: "u1",
  username: "user@medcompare.demo",
  role: "viewer",
  permissions: ["view_hospital", "view_quality"],
  auth_status: "authenticated",
  development: true,
  hospital_ids: ["h_demo_northstar"],
};

const okAdmin = {
  user_id: "u2",
  username: "admin@medcompare.demo",
  role: "admin",
  permissions: ["view_hospital", "manage_users"],
  auth_status: "authenticated",
  development: true,
  hospital_ids: [],
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(handler: (url: string, init?: RequestInit) => Response | Promise<Response>) {
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

describe("LoginPage", () => {
  it("renders demo note and submits credentials", async () => {
    const user = userEvent.setup();
    stubFetch((url, init) => {
      if (url.includes("/auth/login") && init?.method === "POST") {
        return jsonResponse({
          access_token: "tok",
          expires_in: 3600,
          user: { user_id: "u1", username: "user@medcompare.demo", role: "viewer" },
          mode: "demo",
          development: true,
        });
      }
      if (url.includes("/auth/me")) return jsonResponse(okUser);
      return jsonResponse({ detail: "not found" }, 404);
    });

    render(
      <MemoryRouter initialEntries={["/login"]}>
        <AuthProvider>
          <LoginPage />
        </AuthProvider>
      </MemoryRouter>,
    );

    expect(screen.getByTestId("login-page")).toBeInTheDocument();
    expect(screen.getByText(/passwords come from/i)).toBeInTheDocument();

    await user.type(screen.getByTestId("username"), "user@medcompare.demo");
    await user.type(screen.getByTestId("password"), "secret");
    await user.click(screen.getByTestId("login-submit"));

    await waitFor(() => expect(sessionStorage.getItem("medcompare_token")).toBe("tok"));
  });

  it("shows error state on failed login without leaking password", async () => {
    const user = userEvent.setup();
    stubFetch(() => jsonResponse({ detail: "Invalid credentials" }, 401));

    render(
      <MemoryRouter initialEntries={["/login"]}>
        <AuthProvider>
          <LoginPage />
        </AuthProvider>
      </MemoryRouter>,
    );

    await user.type(screen.getByTestId("username"), "bad");
    await user.type(screen.getByTestId("password"), "wrongpass123");
    await user.click(screen.getByTestId("login-submit"));

    const alert = await screen.findByTestId("error");
    expect(alert).toHaveTextContent("Invalid credentials");
    expect(alert.textContent).not.toContain("wrongpass123");
  });
});

describe("routing & RBAC (full App)", () => {
  it("redirects anonymous users from protected routes to /login", async () => {
    setPath("/");
    stubFetch(() => jsonResponse({ detail: "Unauthorized" }, 401));
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("login-page")).toBeInTheDocument());
  });

  it("blocks non-admin from /admin and redirects home", async () => {
    setPath("/admin");
    sessionStorage.setItem("medcompare_token", "tok");
    stubFetch((url) => {
      if (url.includes("/auth/me")) return jsonResponse(okUser);
      return jsonResponse({ detail: "forbidden" }, 403);
    });
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("user-layout")).toBeInTheDocument());
    expect(screen.queryByTestId("admin-layout")).not.toBeInTheDocument();
  });

  it("renders admin layout for admin role", async () => {
    setPath("/admin");
    sessionStorage.setItem("medcompare_token", "tok");
    stubFetch((url) => {
      if (url.includes("/auth/me")) return jsonResponse(okAdmin);
      if (url.includes("/api/v1/health")) return jsonResponse({ status: "ok" });
      if (url.includes("/api/v1/users")) return jsonResponse([]);
      if (url.includes("/hospitals/registry")) return jsonResponse({ hospitals: [] });
      if (url.includes("/api/v1/hospitals")) return jsonResponse({ datasets: [] });
      if (url.includes("/quality/pipeline"))
        return jsonResponse({ report: { overall_score: null, note: "demo" } });
      return jsonResponse({ detail: "nf" }, 404);
    });
    render(<App />);
    expect(await screen.findByTestId("admin-layout")).toBeInTheDocument();
    expect(screen.getByTestId("admin-title")).toHaveTextContent("MedCompare Admin");
    expect(screen.getByTestId("current-user")).toHaveTextContent("admin@medcompare.demo");
  });

  it("on registry 401 clears session and returns to login", async () => {
    setPath("/");
    sessionStorage.setItem("medcompare_token", "tok");
    stubFetch((url) => {
      if (url.includes("/auth/me")) return jsonResponse(okUser);
      if (url.includes("/hospitals/registry")) return jsonResponse({ detail: "Unauthorized" }, 401);
      return jsonResponse({ detail: "nf" }, 404);
    });
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("login-page")).toBeInTheDocument());
    expect(sessionStorage.getItem("medcompare_token")).toBeNull();
  });

  it("shows error state when protected data fetch fails with 500", async () => {
    setPath("/");
    sessionStorage.setItem("medcompare_token", "tok");
    stubFetch((url) => {
      if (url.includes("/auth/me")) return jsonResponse(okUser);
      if (url.includes("/hospitals/registry")) return jsonResponse({ detail: "Boom" }, 500);
      return jsonResponse({ detail: "nf" }, 404);
    });
    render(<App />);
    expect(await screen.findByTestId("user-home")).toBeInTheDocument();
    expect(await screen.findByTestId("error")).toBeInTheDocument();
  });
});

describe("EvidenceValue UNKNOWN vs NO", () => {
  it("renders UNKNOWN distinctly from NO", async () => {
    const { EvidenceValue } = await import("../components/ui");
    const { render: r } = await import("@testing-library/react");
    r(
      <>
        <EvidenceValue value="UNKNOWN" />
        <EvidenceValue value="NO" />
        <EvidenceValue value={null} />
      </>,
    );
    expect(screen.getAllByTestId("unknown")).toHaveLength(2);
    expect(screen.getByTestId("no")).toHaveTextContent("NO");
  });
});

describe("ApiError", () => {
  it("carries status and detail", () => {
    const err = new ApiError(403, "Forbidden", "Forbidden");
    expect(err.status).toBe(403);
    expect(err.detail).toBe("Forbidden");
  });
});
