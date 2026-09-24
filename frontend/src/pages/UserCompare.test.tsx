import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { setAuthToken } from "../api/client";

const viewer = {
  user_id: "u1",
  username: "user@medcompare.demo",
  role: "viewer",
  permissions: ["view_hospital"],
  auth_status: "authenticated",
  development: true,
  hospital_ids: ["h_a", "h_b", "h_c"],
};

const registry = {
  hospitals: [
    { hospital_id: "h_a", name: "Hospital A", created_at: "2026-01-01" },
    { hospital_id: "h_b", name: "Hospital B", created_at: "2026-01-01" },
    { hospital_id: "h_c", name: "Hospital C", created_at: "2026-01-01" },
  ],
};

const compareStoredShape = {
  result: {
    hospital_ids: ["h_a", "h_b", "h_c"],
    hospital_names: { h_a: "Hospital A", h_b: "Hospital B", h_c: "Hospital C" },
    compared_at: "2026-09-24T12:00:00Z",
    note: "Field comparison only. No ranking, scoring, or medical superiority claim is made.",
    fields: [
      {
        field: "cardiology",
        label: "Cardiology",
        cells: [
          { hospital_id: "h_a", hospital_name: "Hospital A", value: true, unknown: false },
          { hospital_id: "h_b", hospital_name: "Hospital B", value: null, unknown: true },
          { hospital_id: "h_c", hospital_name: "Hospital C", value: false, unknown: false },
        ],
      },
      {
        field: "bed_count",
        label: "Bed count",
        cells: [
          { hospital_id: "h_a", hospital_name: "Hospital A", value: 750, unknown: false },
          { hospital_id: "h_b", hospital_name: "Hospital B", value: 420, unknown: false },
          { hospital_id: "h_c", hospital_name: "Hospital C", value: null, unknown: true },
        ],
      },
    ],
  },
};

const searchStoredShape = {
  result: {
    total: 1,
    limit: 20,
    offset: 0,
    filters: [],
    queried_at: "2026-09-24T12:00:00Z",
    matches: [
      {
        dataset_id: "ds_1",
        hospital_id: "h_a",
        hospital_key: "hospital-a",
        hospital_name: "Hospital A",
        hospital_ref: "hospital-a",
        source_type: "mock_api",
        provider: "Demo Data Services",
        captured_at: "2026-09-24T12:00:00Z",
        record_count: 43,
        fields: [
          { field: "city", value: null, unknown: true, evidence: [] },
          { field: "bed_count", value: 750, unknown: false, evidence: [] },
        ],
        evidence: [
          {
            source_url: "http://127.0.0.1:8001/demo/hospital/hospital-a",
            source_type: "mock_api",
            page_title: "Hospital A",
            captured_at: "2026-09-24T12:00:00Z",
            confidence: 1,
            extraction_method: "api-json",
            source_strength: "strong",
            context: null,
          },
        ],
      },
    ],
  },
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stub(handler: (url: string, init?: RequestInit) => Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => handler(String(input), init)),
  );
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

async function pick(path: string, handler: (url: string, init?: RequestInit) => Response) {
  setPath(path);
  sessionStorage.setItem("medcompare_token", "tok");
  stub((url, init) => {
    if (url.includes("/auth/me")) return json(viewer);
    if (url.includes("/hospitals/registry")) return json(registry);
    return handler(url, init);
  });
  render(<App />);
  await screen.findByTestId(path === "/compare" ? "user-compare" : "user-search");
}

describe("stored comparison contract", () => {
  it("renders YES, NO and UNKNOWN cells distinctly from fields[].cells", async () => {
    const user = userEvent.setup();
    await pick("/compare", (url) =>
      url.includes("/search/compare") ? json(compareStoredShape) : json({ detail: "nf" }, 404),
    );

    expect(await screen.findByTestId("compare-check-h_a")).toBeInTheDocument();
    await user.click(screen.getByTestId("compare-check-h_a"));
    await user.click(screen.getByTestId("compare-check-h_b"));
    await user.click(screen.getByTestId("compare-check-h_c"));
    await user.click(screen.getByTestId("compare-submit"));

    expect(screen.getByTestId("compare-result")).toBeInTheDocument();
    expect(await screen.findByTestId("compare-result")).toBeInTheDocument();
    const headers = Array.from(
      document.querySelectorAll('[data-testid="compare-result"] thead th'),
    ).map((th) => th.textContent?.trim());
    expect(headers).toEqual(["Field", "Hospital A", "Hospital B", "Hospital C"]);

    expect(screen.getByTestId("yes")).toHaveTextContent("YES");
    expect(screen.getByTestId("no")).toHaveTextContent("NO");
    expect(screen.getAllByTestId("unknown")).toHaveLength(2);
    expect(screen.getByText("750")).toBeInTheDocument();
    expect(screen.getByText("420")).toBeInTheDocument();

    expect(screen.getByText("Cardiology")).toBeInTheDocument();
    expect(screen.getByText("Bed count")).toBeInTheDocument();

    const text = document.body.textContent ?? "";
    expect(text).toMatch(/no ranking/i);
    expect(text.toLowerCase()).not.toContain("best hospital");
    expect(text).not.toMatch(/overall_score\s*[:=]\s*[1-9]/);
  });

  it("renders a stored-field search match with provenance and UNKNOWN", async () => {
    await pick("/search", (url) =>
      url.includes("/api/v1/search") && !url.includes("compare")
        ? json(searchStoredShape)
        : json({ detail: "nf" }, 404),
    );

    const user = userEvent.setup();
    await user.type(screen.getByTestId("search-input"), "cardiology");
    await user.click(screen.getByTestId("search-submit"));

    expect(await screen.findByTestId("search-results")).toBeInTheDocument();
    expect(screen.getByTestId("search-match-0")).toHaveTextContent("Hospital A");
    expect(screen.getByTestId("search-match-0")).toHaveTextContent("mock_api");
    expect(screen.getByTestId("search-match-0")).toHaveTextContent("records: 43");
    expect(screen.getByTestId("search-evidence")).toHaveTextContent(
      "http://127.0.0.1:8001/demo/hospital/hospital-a",
    );
    expect(screen.getByTestId("search-evidence")).toHaveTextContent("api-json");
    await waitFor(() => expect(screen.getByTestId("unknown")).toBeInTheDocument());
    expect(screen.getByText("750")).toBeInTheDocument();
  });
});
