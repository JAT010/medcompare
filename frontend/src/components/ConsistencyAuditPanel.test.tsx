import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DEMO_MODE_KEY } from "../api/demo";
import ConsistencyAuditPanel from "./ConsistencyAuditPanel";

const FACILITY = "11111111-1111-4111-8111-111111111111";
const RUN = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

const FACILITIES = {
  identity_note: "Test facilities.",
  limit: 50,
  offset: 0,
  items: [
    {
      id: FACILITY,
      name: "Northgate General Hospital",
      organization_id: null,
      organization_name: "Northgate Health Group",
      canonical_url: "https://northgate.example/branch-main",
      website_domain: "northgate.example",
      address: "12 Sector 12",
      city: "Northbridge",
      state: "Northbridge State",
      country: "Testland",
      postal_code: "411001",
      status: "active",
    },
  ],
};

const AUDIT = {
  facility_id: FACILITY,
  facility_name: "Northgate General Hospital",
  organization_name: "Northgate Health Group",
  branch_identity_note: "Audited at the physical-facility level.",
  internal_record: {
    facility_id: FACILITY,
    name: "Northgate General Hospital",
    organization_name: "Northgate Health Group",
    canonical_url: "https://northgate.example/branch-main",
    address: "12 Sector 12",
    city: "Northbridge",
    state: "Northbridge State",
    postal_code: "411001",
    country: "Testland",
    updated_at: "2026-11-15T00:00:00Z",
  },
  website_snapshot: {
    crawl_run_id: RUN,
    status: "completed",
    target_url: "https://northgate.example/branch-main",
    requested_url: "https://northgate.example/branch-main",
    hospital_name: "Northgate General Hospital",
    pages_fetched: 3,
    evidence_count: 1,
    usable_for_comparison: true,
    requested_at: "2026-11-14T00:00:00Z",
    completed_at: "2026-11-14T00:00:01Z",
    retrieved_at: "2026-11-14T00:00:01Z",
  },
  findings: [
    {
      field_key: "facility_name",
      label: "Facility name",
      status: "consistent",
      internal: {
        source: "internal_record",
        source_name: "MedCompare registry (stored facility record)",
        value: "Northgate General Hospital",
        source_url: "https://northgate.example/branch-main",
        retrieved_at: "2026-11-15T00:00:00Z",
        confidence: 1,
        note: null,
      },
      website: {
        source: "website_crawl",
        source_name: "Hospital website (persisted crawl)",
        value: "Northgate General Hospital",
        source_url: "https://northgate.example/branch-main",
        retrieved_at: "2026-11-14T00:00:01Z",
        confidence: 0.8,
        note: null,
      },
      explanation: "Both sources agree.",
      requires_authorized_integration: false,
    },
    {
      field_key: "infrastructure",
      label: "Infrastructure",
      status: "missing_from_internal_record",
      internal: null,
      website: {
        source: "website_crawl",
        source_name: "Hospital website (persisted crawl)",
        value: "ICU available",
        source_url: "https://northgate.example/branch-main",
        retrieved_at: "2026-11-14T00:00:01Z",
        confidence: 0.7,
        note: null,
      },
      explanation: "The registry does not store this field.",
      requires_authorized_integration: true,
    },
  ],
  summary: {
    findings_total: 2,
    consistent: 1,
    differs: 0,
    missing_from_website: 0,
    missing_from_internal_record: 1,
    potentially_outdated: 0,
    unverifiable: 0,
  },
  warnings: ["Verifying service fields requires an authorized hospital-server integration."],
  disclaimer: "Missing information is not proof of absence.",
};

function jsonResponse(body: unknown) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  } as Response);
}

describe("ConsistencyAuditPanel", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    window.localStorage.setItem(DEMO_MODE_KEY, "0");
    fetchMock.mockReset();
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/facilities")) return jsonResponse(FACILITIES);
      if (url.includes("/consistency/audit")) {
        expect(init?.method).toBe("POST");
        return jsonResponse(AUDIT);
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("runs an audit and renders findings with provenance", async () => {
    render(<ConsistencyAuditPanel />);

    const runButton = await screen.findByRole("button", { name: "Run consistency audit" });
    await waitFor(() => expect(runButton).toBeEnabled());
    await userEvent.click(runButton);

    expect(await screen.findByText("Consistent")).toBeInTheDocument();
    expect(screen.getByText("Not stored internally")).toBeInTheDocument();
    expect(screen.getByText("Requires authorized hospital integration")).toBeInTheDocument();
    expect(screen.getAllByText(/confidence 80%/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Verifying service fields requires/)).toBeInTheDocument();
    expect(screen.getByText(/Missing information is not proof of absence/)).toBeInTheDocument();
  });

  it("renders explicitly labelled demo findings without touching the network", async () => {
    window.localStorage.setItem(DEMO_MODE_KEY, "1");
    render(<ConsistencyAuditPanel />);

    const runButton = await screen.findByRole("button", { name: "Run consistency audit" });
    await waitFor(() => expect(runButton).toBeEnabled());
    await userEvent.click(runButton);

    expect(
      await screen.findByText(/DEMONSTRATION DATA: not a live consistency audit/),
    ).toBeInTheDocument();
    expect(screen.getByText("Consistent")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows an honest error when the audit request fails", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/facilities")) return jsonResponse(FACILITIES);
      return Promise.resolve({
        ok: false,
        status: 409,
        json: () =>
          Promise.resolve({
            detail: {
              error: "crawl_run_not_usable",
              message: "Crawl run is not a usable snapshot.",
            },
          }),
      } as Response);
    });

    render(<ConsistencyAuditPanel />);

    const runButton = await screen.findByRole("button", { name: "Run consistency audit" });
    await waitFor(() => expect(runButton).toBeEnabled());
    await userEvent.click(runButton);

    expect(await screen.findByRole("alert")).toHaveTextContent("Crawl run is not a usable snapshot.");
  });
});
