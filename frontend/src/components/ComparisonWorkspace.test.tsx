import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ComparisonWorkspace from "./ComparisonWorkspace";

const fetchMock = vi.fn();

const FACILITY_A = "11111111-1111-1111-1111-111111111111";
const FACILITY_B = "22222222-2222-2222-2222-222222222222";
const RUN_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const RUN_A2 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaab";
const RUN_U = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const RUN_U2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2";
const RUN_FAIL = "cccccccc-cccc-cccc-cccc-cccccccccccc";
const SUBJECT_U = "dddddddd-dddd-dddd-dddd-dddddddddddd";
const SUBJECT_U2 = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee";

const FACILITIES = [
  {
    id: FACILITY_A,
    name: "Northgate General Hospital",
    organization_id: null,
    organization_name: "Northgate Health Group",
    canonical_url: "https://northgate.example/",
    website_domain: "northgate.example",
    address: null,
    city: "Northbridge",
    state: null,
    country: null,
    postal_code: null,
    status: "active",
  },
  {
    id: FACILITY_B,
    name: "Southwind Medical Centre",
    organization_id: null,
    organization_name: null,
    canonical_url: "https://southwind.example/",
    website_domain: "southwind.example",
    address: null,
    city: "Southmere",
    state: null,
    country: null,
    postal_code: null,
    status: "active",
  },
];

const CRITERION = {
  key: "location",
  label: "Location",
  origin: "standard" as const,
  evidence_needed: "A stored address for this facility.",
  matched_source: null,
  overlaps_standard: null,
  original_text: null,
};

const EVIDENCE_LOCATION = {
  criterion_key: "location",
  value: "Harbour Side, Testville",
  source: "website_crawl" as const,
  source_name: "Hospital website (crawled)",
  source_url: "https://harbourview.example/",
  retrieved_at: "2026-11-13T10:00:00Z",
  published_at: null,
  status: "available" as const,
  confidence: null,
  note: null,
};

const MATRIX = {
  scope: "standard_12",
  source: "medcompare_server",
  criteria: [CRITERION],
  columns: [
    {
      column_id: FACILITY_A,
      kind: "registered",
      name: "Northgate General Hospital",
      location: "Northbridge",
      organization_name: null,
      website_url: null,
      identity_note: "Registered MedCompare facility.",
    },
    {
      column_id: FACILITY_B,
      kind: "registered",
      name: "Southwind Medical Centre",
      location: "Southmere",
      organization_name: null,
      website_url: null,
      identity_note: "Registered MedCompare facility.",
    },
  ],
  rows: [
    {
      criterion: CRITERION,
      cells: [
        {
          status: "available",
          value: "12 Sector 12, Northbridge",
          source: "medcompare_server",
          source_name: "MedCompare registry",
          source_url: null,
          retrieved_at: "2026-11-13T10:00:00Z",
          published_at: null,
          confidence: null,
          explanation: "Stored registry address for this branch.",
        },
        {
          status: "unavailable",
          value: null,
          source: null,
          source_name: null,
          source_url: null,
          retrieved_at: null,
          published_at: null,
          confidence: null,
          explanation: "Nothing was found for this facility, which is not evidence that it lacks it.",
        },
      ],
    },
  ],
  source_statuses: [
    { source: "medcompare_server", status: "active", detail: "Evidence read from the MedCompare hospital registry.", required_integration: null },
    { source: "public_research", status: "not_configured", detail: "No search provider client exists in this deployment.", required_integration: "A server-side search provider with an API key." },
    { source: "website_crawl", status: "active", detail: "Extraction runs synchronously when requested.", required_integration: null },
  ],
  coverage: [
    { column_id: FACILITY_A, criteria_total: 1, criteria_with_evidence: 1, coverage_ratio: 1 },
    { column_id: FACILITY_B, criteria_total: 1, criteria_with_evidence: 0, coverage_ratio: 0 },
  ],
  warnings: [],
  disclaimer: "Comparison reports evidence only. No winner, clinical quality score or overall ranking is produced.",
};

const INTERPRETATION = {
  criteria: [
    {
      key: "parking",
      label: "Parking",
      origin: "custom",
      evidence_needed: 'A citable source stating "parking" for this exact facility.',
      matched_source: null,
      overlaps_standard: null,
      original_text: "parking",
    },
  ],
  clarification_questions: [],
  interpreter: "deterministic_clause_parser",
  interpretation_note: "No language model is configured, so your wording was parsed deterministically.",
};

const CRAWL_RESULT = {
  status: "completed",
  execution: "synchronous",
  job_store_available: false,
  target_url: "https://harbourview.example/",
  pages_fetched: 4,
  hospital_name: "Harbour View Hospital",
  evidence: [EVIDENCE_LOCATION],
  criteria_without_evidence: ["affordability"],
  warnings: [
    "Extraction ran synchronously in this request. This deployment has no persistent crawl job store, so no job id or status endpoint exists yet.",
  ],
};

const CRAWL_RESULT_REGISTERED = {
  ...CRAWL_RESULT,
  target_url: "https://northgate.example/",
  crawl_run_id: RUN_A,
  facility_id: FACILITY_A,
  comparison_subject_id: null,
  crawl_status: "completed",
  evidence_count: 1,
};

const CRAWL_RESULT_UNREGISTERED = {
  ...CRAWL_RESULT,
  crawl_run_id: RUN_U,
  facility_id: null,
  comparison_subject_id: SUBJECT_U,
  crawl_status: "completed_with_warnings",
  evidence_count: 1,
};

function crawlDetail(overrides: Record<string, unknown> = {}) {
  return {
    crawl_run_id: RUN_A,
    facility_id: FACILITY_A,
    comparison_subject_id: null,
    subject_name: null,
    subject_location: null,
    subject_website_url: null,
    status: "completed",
    execution_mode: "synchronous",
    target_url: "https://northgate.example/",
    requested_url: "https://northgate.example/",
    hospital_name: "Northgate General Hospital",
    pages_fetched: 3,
    evidence_count: 1,
    evidence: [EVIDENCE_LOCATION],
    criteria_without_evidence: [],
    warnings: [],
    usable_for_comparison: true,
    requested_at: "2026-11-13T10:00:00Z",
    completed_at: "2026-11-13T10:00:01Z",
    retrieved_at: "2026-11-13T10:00:01Z",
    failure_code: null,
    failure_message: null,
    ...overrides,
  };
}

interface PostedCall {
  url: string;
  body: unknown;
}

function ok(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function installRouter(options?: {
  compareStatus?: number;
  compareBody?: unknown;
  interpretBody?: unknown;
  crawlStatus?: number;
  crawlBody?: unknown;
  crawlRunGet?: (url: string) => { status: number; body: unknown } | null;
}) {
  const posted: PostedCall[] = [];
  fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    if (method === "POST" && url.endsWith("/comparison/interpret")) {
      return Promise.resolve(ok(200, options?.interpretBody ?? INTERPRETATION));
    }
    if (method === "POST" && url.endsWith("/comparison")) {
      posted.push({ url, body });
      return Promise.resolve(ok(options?.compareStatus ?? 200, options?.compareBody ?? MATRIX));
    }
    if (method === "POST" && url.endsWith("/crawl/extract")) {
      posted.push({ url, body });
      return Promise.resolve(ok(options?.crawlStatus ?? 200, options?.crawlBody ?? CRAWL_RESULT));
    }
    if (method === "GET" && url.includes("/crawl/runs/")) {
      const custom = options?.crawlRunGet?.(url);
      if (custom) return Promise.resolve(ok(custom.status, custom.body));
      return Promise.resolve(ok(404, { detail: { error: "crawl_run_not_found", message: "not found" } }));
    }
    if (url.includes("/facilities")) {
      return Promise.resolve(
        ok(200, { items: FACILITIES, identity_note: "Registered physical facilities.", limit: 50, offset: 0 }),
      );
    }
    return Promise.resolve(ok(404, { detail: "not found" }));
  });
  return posted;
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  sessionStorage.clear();
});

async function selectTwoHospitals(user: ReturnType<typeof userEvent.setup>) {
  const first = await screen.findByLabelText(/^Northgate General Hospital/);
  const second = await screen.findByLabelText(/^Southwind Medical Centre/);
  await user.click(first);
  await user.click(second);
}

it("compares two registered facilities and renders evidence with provenance", async () => {
  const posted = installRouter();
  const user = userEvent.setup();
  render(<ComparisonWorkspace />);

  await selectTwoHospitals(user);
  const compareButton = screen.getByRole("button", { name: "Compare hospitals" });
  await waitFor(() => expect(compareButton).toBeEnabled());
  await user.click(compareButton);

  expect(await screen.findByRole("heading", { name: "Comparison result" })).toBeInTheDocument();
  expect(screen.getByTestId("disclaimer")).toHaveTextContent("No winner");
  expect(screen.getByText("12 Sector 12, Northbridge")).toBeInTheDocument();
  expect(screen.getByText("Available")).toBeInTheDocument();
  expect(screen.getByText(/not evidence that it lacks it/)).toBeInTheDocument();
  expect(posted).toHaveLength(1);
  expect(JSON.stringify(posted[0]?.body)).not.toContain('"evidence"');
});

it("blocks a custom-only comparison until the prompt is interpreted", async () => {
  const posted = installRouter();
  const user = userEvent.setup();
  render(<ComparisonWorkspace />);

  await user.click(screen.getByLabelText(/Custom criteria only/));
  await selectTwoHospitals(user);
  const compareButton = screen.getByRole("button", { name: "Compare hospitals" });
  expect(compareButton).toBeDisabled();
  expect(screen.getByText(/Interpret your prompt first/)).toBeInTheDocument();

  await user.type(screen.getByLabelText(/What should be compared in addition/), "compare parking");
  await user.click(screen.getByRole("button", { name: "Interpret prompt" }));
  const criteriaList = await screen.findByLabelText("Interpreted criteria");
  expect(criteriaList).toHaveTextContent("Parking");
  await waitFor(() => expect(compareButton).toBeEnabled());
  await user.click(compareButton);
  await screen.findByRole("heading", { name: "Comparison result" });
  expect(posted[0]?.body).toMatchObject({ scope: "custom_only", criteria: [{ key: "parking" }] });
});

it("surfaces the missing search integration instead of pretending to research", async () => {
  installRouter({
    compareStatus: 501,
    compareBody: {
      detail: {
        error: "public_research_not_configured",
        message: "MedCompare cannot research hospitals for you until this integration exists.",
        required_integration: "A server-side search provider with an API key.",
      },
    },
  });
  const user = userEvent.setup();
  render(<ComparisonWorkspace />);
  await selectTwoHospitals(user);
  await user.click(screen.getByLabelText(/Public internet research/));
  const compareButton = screen.getByRole("button", { name: "Compare hospitals" });
  await waitFor(() => expect(compareButton).toBeEnabled());
  await user.click(compareButton);
  const failure = await screen.findByTestId("compare-error");
  expect(failure).toHaveTextContent("MedCompare cannot research hospitals");
  expect(failure).toHaveTextContent(/Missing integration: A server-side search provider/);
  expect(screen.queryByRole("heading", { name: "Comparison result" })).not.toBeInTheDocument();
});

it("extracts a hospital website on request and reports synchronous execution", async () => {
  const posted = installRouter();
  const user = userEvent.setup();
  render(<ComparisonWorkspace />);
  await user.type(screen.getByLabelText("Name"), "Harbour View Hospital");
  await user.type(screen.getByLabelText(/Website URL/), "https://harbourview.example/");
  await user.click(screen.getByRole("button", { name: "Add hospital" }));
  await user.click(screen.getByLabelText(/Website crawling/));
  const extractButton = await screen.findByRole("button", { name: "Extract from website" });
  await waitFor(() => expect(extractButton).toBeEnabled());
  await user.click(extractButton);
  expect(await screen.findByText(/4 page\(s\) fetched/)).toBeInTheDocument();
  expect(screen.getByText(/persistent job store: not available/)).toBeInTheDocument();
  expect(screen.getByText(/No evidence found for: affordability/)).toBeInTheDocument();
  const crawlCall = posted.find((call) => call.url.endsWith("/crawl/extract"));
  expect(crawlCall?.body).toMatchObject({ url: "https://harbourview.example/", max_pages: 15, name: "Harbour View Hospital" });
});

describe("P0 persisted crawl references", () => {
  it("registered extraction sends facility_id and captures crawl_run_id", async () => {
    const posted = installRouter({ crawlBody: CRAWL_RESULT_REGISTERED });
    const user = userEvent.setup();
    render(<ComparisonWorkspace />);
    await user.click(await screen.findByLabelText(/^Northgate General Hospital/));
    await user.click(screen.getByLabelText(/Website crawling/));
    await user.click(await screen.findByRole("button", { name: "Extract from website" }));
    expect((await screen.findAllByText(new RegExp(RUN_A))).length).toBeGreaterThan(0);
    const crawlCall = posted.find((call) => call.url.endsWith("/crawl/extract"));
    expect(crawlCall?.body).toMatchObject({ url: "https://northgate.example/", facility_id: FACILITY_A, max_pages: 15 });
  });

  it("registered comparison sends selected crawl_run_id without client evidence blobs", async () => {
    const posted = installRouter({ crawlBody: CRAWL_RESULT_REGISTERED, compareBody: MATRIX });
    const user = userEvent.setup();
    render(<ComparisonWorkspace />);
    await selectTwoHospitals(user);
    await user.click(screen.getByLabelText(/Website crawling/));
    const extractButtons = await screen.findAllByRole("button", { name: "Extract from website" });
    await user.click(extractButtons[0]!);
    await screen.findAllByText(new RegExp(RUN_A));
    const compareButton = screen.getByRole("button", { name: "Compare hospitals" });
    await waitFor(() => expect(compareButton).toBeEnabled());
    await user.click(compareButton);
    await screen.findByRole("heading", { name: "Comparison result" });
    const compareCall = posted.find((call) => call.url.endsWith("/comparison"));
    expect(compareCall?.body).toMatchObject({
      hospitals: [
        { type: "registered", facility_id: FACILITY_A, crawl_run_id: RUN_A },
        { type: "registered", facility_id: FACILITY_B },
      ],
    });
    expect(JSON.stringify(compareCall?.body)).not.toContain('"evidence"');
  });

  it("unregistered extraction captures server-issued comparison_subject_id and preserves it", async () => {
    const posted: PostedCall[] = [];
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      if (method === "POST" && url.endsWith("/crawl/extract")) {
        posted.push({ url, body });
        const subj = body?.comparison_subject_id ?? SUBJECT_U;
        return Promise.resolve(ok(200, { ...CRAWL_RESULT_UNREGISTERED, comparison_subject_id: subj, crawl_run_id: body?.comparison_subject_id ? RUN_U2 : RUN_U }));
      }
      if (url.includes("/facilities")) {
        return Promise.resolve(ok(200, { items: FACILITIES, identity_note: "Registered physical facilities.", limit: 50, offset: 0 }));
      }
      return Promise.resolve(ok(404, { detail: "not found" }));
    });
    const user = userEvent.setup();
    render(<ComparisonWorkspace />);
    await user.type(screen.getByLabelText("Name"), "Harbour View Hospital");
    await user.type(screen.getByLabelText("Location"), "Testville");
    await user.type(screen.getByLabelText(/Website URL/), "https://harbourview.example/");
    await user.click(screen.getByRole("button", { name: "Add hospital" }));
    await user.click(screen.getByLabelText(/Website crawling/));
    await user.click(await screen.findByRole("button", { name: "Extract from website" }));
    await screen.findByText(new RegExp(SUBJECT_U));
    expect((posted[0]?.body as { comparison_subject_id?: string }).comparison_subject_id).toBeFalsy();
    await user.click(screen.getByRole("button", { name: "Extract from website" }));
    await waitFor(() => expect(posted.filter((c) => c.url.endsWith("/crawl/extract")).length).toBe(2));
    expect(posted[1]?.body).toMatchObject({ comparison_subject_id: SUBJECT_U });
  });

  it("unregistered comparison sends comparison_subject_id and crawl_run_id without client evidence blobs", async () => {
    const posted = installRouter({ crawlBody: CRAWL_RESULT_UNREGISTERED, compareBody: MATRIX });
    const user = userEvent.setup();
    render(<ComparisonWorkspace />);
    await user.click(await screen.findByLabelText(/^Northgate General Hospital/));
    await user.type(screen.getByLabelText("Name"), "Harbour View Hospital");
    await user.type(screen.getByLabelText(/Website URL/), "https://harbourview.example/");
    await user.click(screen.getByRole("button", { name: "Add hospital" }));
    await user.click(screen.getByLabelText(/Website crawling/));
    const extractButtons = await screen.findAllByRole("button", { name: "Extract from website" });
    await user.click(extractButtons[extractButtons.length - 1]!);
    await screen.findAllByText(new RegExp(RUN_U));
    const compareButton = screen.getByRole("button", { name: "Compare hospitals" });
    await waitFor(() => expect(compareButton).toBeEnabled());
    await user.click(compareButton);
    await screen.findByRole("heading", { name: "Comparison result" });
    const compareCall = posted.find((call) => call.url.endsWith("/comparison"));
    const hospitals = (compareCall?.body as { hospitals: Record<string, unknown>[] }).hospitals;
    const unreg = hospitals.find((h) => h.type === "unregistered");
    expect(unreg).toMatchObject({ type: "unregistered", comparison_subject_id: SUBJECT_U, crawl_run_id: RUN_U, name: "Harbour View Hospital" });
    expect(JSON.stringify(unreg)).not.toContain('"evidence"');
  });

  it("restores references from sessionStorage and recovers runs with correct ownership params", async () => {
    sessionStorage.setItem(
      "medcompare.workspace.crawlRefs.v1",
      JSON.stringify([
        { kind: "registered", localKey: FACILITY_A, facilityId: FACILITY_A, crawlRunId: RUN_A, facilityName: "Northgate General Hospital" },
        { kind: "unregistered", localKey: "local-unreg-1", comparisonSubjectId: SUBJECT_U, crawlRunId: RUN_U, name: "Harbour View Hospital", websiteUrl: "https://harbourview.example/" },
      ]),
    );
    installRouter({
      crawlRunGet: (url) => {
        if (url.includes(RUN_A)) {
          expect(url).toContain(`facility_id=${FACILITY_A}`);
          return { status: 200, body: crawlDetail({ crawl_run_id: RUN_A, facility_id: FACILITY_A }) };
        }
        if (url.includes(RUN_U)) {
          expect(url).toContain(`comparison_subject_id=${SUBJECT_U}`);
          return { status: 200, body: crawlDetail({ crawl_run_id: RUN_U, facility_id: null, comparison_subject_id: SUBJECT_U }) };
        }
        return null;
      },
    });
    render(<ComparisonWorkspace />);
    expect((await screen.findAllByText(new RegExp(RUN_A))).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(new RegExp(RUN_U))).length).toBeGreaterThan(0);
  });

  it("prevents cross-subject/facility recovery leakage", async () => {
    sessionStorage.setItem(
      "medcompare.workspace.crawlRefs.v1",
      JSON.stringify([
        { kind: "registered", localKey: FACILITY_A, facilityId: FACILITY_A, crawlRunId: RUN_A, facilityName: "Northgate General Hospital" },
        { kind: "unregistered", localKey: "local-unreg-1", comparisonSubjectId: SUBJECT_U, crawlRunId: RUN_U, name: "Harbour View Hospital" },
      ]),
    );
    installRouter({
      crawlRunGet: (url) => {
        if (url.includes(RUN_A)) return { status: 200, body: crawlDetail({ crawl_run_id: RUN_A, facility_id: FACILITY_B }) };
        if (url.includes(RUN_U)) return { status: 200, body: crawlDetail({ crawl_run_id: RUN_U, facility_id: null, comparison_subject_id: SUBJECT_U2 }) };
        return null;
      },
    });
    render(<ComparisonWorkspace />);
    expect(await screen.findByText(/Recovered crawl run does not belong to this facility/)).toBeInTheDocument();
    expect(await screen.findByText(/Recovered crawl run does not belong to this subject/)).toBeInTheDocument();
  });

  it("captures persisted crawl_run_id from failed/timed-out error details and marks unusable", async () => {
    installRouter({
      crawlStatus: 504,
      crawlBody: { detail: { error: "crawl_exceeded_request_window", message: "Extraction did not finish within 120 seconds.", crawl_run_id: RUN_FAIL, comparison_subject_id: SUBJECT_U } },
    });
    const user = userEvent.setup();
    render(<ComparisonWorkspace />);
    await user.type(screen.getByLabelText("Name"), "Harbour View Hospital");
    await user.type(screen.getByLabelText(/Website URL/), "https://harbourview.example/");
    await user.click(screen.getByRole("button", { name: "Add hospital" }));
    await user.click(screen.getByLabelText(/Website crawling/));
    await user.click(await screen.findByRole("button", { name: "Extract from website" }));
    expect((await screen.findAllByText(/Extraction did not finish within 120 seconds/)).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(new RegExp(RUN_FAIL))).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/timed out|timed_out|not usable/i).length).toBeGreaterThan(0);
  });

  it("treats unusable or missing recovered runs as non-evidence with honest state", async () => {
    sessionStorage.setItem(
      "medcompare.workspace.crawlRefs.v1",
      JSON.stringify([{ kind: "registered", localKey: FACILITY_A, facilityId: FACILITY_A, crawlRunId: RUN_A, facilityName: "Northgate General Hospital" }]),
    );
    installRouter({
      crawlRunGet: (url) => {
        if (url.includes(RUN_A)) {
          return { status: 200, body: crawlDetail({ crawl_run_id: RUN_A, facility_id: FACILITY_A, status: "failed", usable_for_comparison: false, evidence: [], failure_message: "Extraction failed." }) };
        }
        return null;
      },
    });
    render(<ComparisonWorkspace />);
    expect(await screen.findByText(/Historical crawl run is not usable \(failed\)/)).toBeInTheDocument();
    expect(screen.getByText(/Extraction failed/)).toBeInTheDocument();
  });

  it("handles missing/inaccessible recovered run without crashing", async () => {
    sessionStorage.setItem(
      "medcompare.workspace.crawlRefs.v1",
      JSON.stringify([{ kind: "registered", localKey: FACILITY_A, facilityId: FACILITY_A, crawlRunId: RUN_A, facilityName: "Northgate General Hospital" }]),
    );
    installRouter({ crawlRunGet: () => ({ status: 404, body: { detail: { error: "crawl_run_not_found", message: "No crawl run with id ..." } } }) });
    render(<ComparisonWorkspace />);
    expect(await screen.findByText(/Selected crawl run reference is inaccessible/)).toBeInTheDocument();
  });

  it("handles malformed sessionStorage without crashing", async () => {
    sessionStorage.setItem("medcompare.workspace.crawlRefs.v1", "{not-json");
    installRouter();
    render(<ComparisonWorkspace />);
    expect(await screen.findByRole("heading", { name: "Compare hospitals" })).toBeInTheDocument();
  });
});

describe("P0.1 lifecycle hardening", () => {
  it("1. overlapping crawl requests for one registered facility: older response arriving last does not win", async () => {
    const user = userEvent.setup();
    const d1 = deferred<ReturnType<typeof ok>>();
    const d2 = deferred<ReturnType<typeof ok>>();
    let call = 0;
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/facilities")) return Promise.resolve(ok(200, { items: FACILITIES, identity_note: "note", limit: 50, offset: 0 }));
      if (method === "POST" && url.endsWith("/crawl/extract")) {
        call += 1;
        return call === 1 ? d1.promise : d2.promise;
      }
      return Promise.resolve(ok(404, { detail: "nf" }));
    });
    render(<ComparisonWorkspace />);
    await user.click(await screen.findByLabelText(/^Northgate General Hospital/));
    await user.click(screen.getByLabelText(/Website crawling/));
    const btn = await screen.findByRole("button", { name: /Extract|Re-extract/i });
    await user.click(btn);
    await user.click(screen.getByRole("button", { name: /Extract|Re-extract/i }));
    d2.resolve(ok(200, { ...CRAWL_RESULT_REGISTERED, crawl_run_id: RUN_A2, crawl_status: "completed" }));
    await screen.findAllByText(new RegExp(RUN_A2));
    d1.resolve(ok(200, { ...CRAWL_RESULT_REGISTERED, crawl_run_id: RUN_A, crawl_status: "completed_with_warnings" }));
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getAllByText(new RegExp(RUN_A2)).length).toBeGreaterThan(0);
    expect(screen.queryAllByText(new RegExp(RUN_A))).toHaveLength(0);
    const stored = JSON.parse(sessionStorage.getItem("medcompare.workspace.crawlRefs.v1") || "[]");
    expect(stored.find((r: { facilityId: string }) => r.facilityId === FACILITY_A).crawlRunId).toBe(RUN_A2);
  });

  it("2. overlapping crawl requests for one unregistered subject: older response arriving last does not win", async () => {
    const user = userEvent.setup();
    const d1 = deferred<ReturnType<typeof ok>>();
    const d2 = deferred<ReturnType<typeof ok>>();
    let call = 0;
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/facilities")) return Promise.resolve(ok(200, { items: FACILITIES, identity_note: "note", limit: 50, offset: 0 }));
      if (method === "POST" && url.endsWith("/crawl/extract")) {
        call += 1;
        return call === 1 ? d1.promise : d2.promise;
      }
      return Promise.resolve(ok(404, { detail: "nf" }));
    });
    render(<ComparisonWorkspace />);
    await user.type(screen.getByLabelText("Name"), "Harbour View Hospital");
    await user.type(screen.getByLabelText(/Website URL/), "https://harbourview.example/");
    await user.click(screen.getByRole("button", { name: "Add hospital" }));
    await user.click(screen.getByLabelText(/Website crawling/));
    const btn = await screen.findByRole("button", { name: /Extract|Re-extract/i });
    await user.click(btn);
    await user.click(screen.getByRole("button", { name: /Extract|Re-extract/i }));
    d2.resolve(ok(200, { ...CRAWL_RESULT_UNREGISTERED, crawl_run_id: RUN_U2, comparison_subject_id: SUBJECT_U }));
    await screen.findAllByText(new RegExp(RUN_U2));
    d1.resolve(ok(200, { ...CRAWL_RESULT_UNREGISTERED, crawl_run_id: RUN_U, comparison_subject_id: SUBJECT_U }));
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getAllByText(new RegExp(RUN_U2)).length).toBeGreaterThan(0);
    expect(screen.queryAllByText(new RegExp(RUN_U))).toHaveLength(0);
    const stored = JSON.parse(sessionStorage.getItem("medcompare.workspace.crawlRefs.v1") || "[]");
    expect(stored.find((r: { kind: string }) => r.kind === "unregistered").crawlRunId).toBe(RUN_U2);
  });

  it("3. recovery resolves after newer crawl completed and must not overwrite it", async () => {
    const user = userEvent.setup();
    const rec = deferred<ReturnType<typeof ok>>();
    let recoverCalled = false;
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/facilities")) return Promise.resolve(ok(200, { items: FACILITIES, identity_note: "note", limit: 50, offset: 0 }));
      if (method === "GET" && url.includes(`/crawl/runs/${RUN_A}`)) {
        recoverCalled = true;
        return rec.promise;
      }
      if (method === "POST" && url.endsWith("/crawl/extract")) {
        return Promise.resolve(ok(200, { ...CRAWL_RESULT_REGISTERED, crawl_run_id: RUN_A2, crawl_status: "completed" }));
      }
      return Promise.resolve(ok(404, { detail: "nf" }));
    });
    sessionStorage.setItem("medcompare.workspace.crawlRefs.v1", JSON.stringify([{ kind: "registered", localKey: FACILITY_A, facilityId: FACILITY_A, crawlRunId: RUN_A, facilityName: "Northgate General Hospital" }]));
    render(<ComparisonWorkspace />);
    await waitFor(() => expect(recoverCalled).toBe(true));
    await user.click(screen.getByLabelText(/Website crawling/));
    await user.click(await screen.findByRole("button", { name: "Extract from website" }));
    await screen.findAllByText(new RegExp(RUN_A2));
    rec.resolve(ok(200, crawlDetail({ crawl_run_id: RUN_A, facility_id: FACILITY_A, status: "completed", usable_for_comparison: true })));
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getAllByText(new RegExp(RUN_A2)).length).toBeGreaterThan(0);
    expect(screen.queryAllByText(new RegExp(RUN_A))).toHaveLength(0);
  });

  it("4. choice removed while crawl request in flight does not resurrect it", async () => {
    const user = userEvent.setup();
    const d = deferred<ReturnType<typeof ok>>();
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/facilities")) return Promise.resolve(ok(200, { items: FACILITIES, identity_note: "note", limit: 50, offset: 0 }));
      if (method === "POST" && url.endsWith("/crawl/extract")) return d.promise;
      return Promise.resolve(ok(404, { detail: "nf" }));
    });
    render(<ComparisonWorkspace />);
    await user.click(await screen.findByLabelText(/^Northgate General Hospital/));
    await user.click(screen.getByLabelText(/Website crawling/));
    await user.click(await screen.findByRole("button", { name: "Extract from website" }));
    await user.click(screen.getByLabelText(`Remove Northgate General Hospital`));
    d.resolve(ok(200, CRAWL_RESULT_REGISTERED));
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryAllByText(new RegExp(RUN_A))).toHaveLength(0);
    const stored = JSON.parse(sessionStorage.getItem("medcompare.workspace.crawlRefs.v1") || "[]");
    expect(stored.find((r: { localKey: string }) => r.localKey === FACILITY_A)).toBeUndefined();
  });

  it("5. choice removed and immediate reload: stale reference must not reappear", async () => {
    const user = userEvent.setup();
    installRouter({ crawlBody: CRAWL_RESULT_REGISTERED });
    const { unmount } = render(<ComparisonWorkspace />);
    await user.click(await screen.findByLabelText(/^Northgate General Hospital/));
    await user.click(screen.getByLabelText(/Website crawling/));
    await user.click(await screen.findByRole("button", { name: "Extract from website" }));
    await screen.findAllByText(new RegExp(RUN_A));
    await user.click(screen.getByLabelText(`Remove Northgate General Hospital`));
    expect(JSON.parse(sessionStorage.getItem("medcompare.workspace.crawlRefs.v1") || "[]")).toEqual([]);
    unmount();
    installRouter();
    render(<ComparisonWorkspace />);
    await screen.findByRole("heading", { name: "Compare hospitals" });
    expect(screen.queryAllByText(new RegExp(RUN_A))).toHaveLength(0);
  });

  it("6. replacement cannot inherit previous choice's crawl reference", async () => {
    const user = userEvent.setup();
    installRouter({ crawlBody: CRAWL_RESULT_UNREGISTERED });
    render(<ComparisonWorkspace />);
    await user.type(screen.getByLabelText("Name"), "Harbour View Hospital");
    await user.type(screen.getByLabelText(/Website URL/), "https://harbourview.example/");
    await user.click(screen.getByRole("button", { name: "Add hospital" }));
    await user.click(screen.getByLabelText(/Website crawling/));
    await user.click(await screen.findByRole("button", { name: "Extract from website" }));
    await screen.findAllByText(new RegExp(RUN_U));
    await user.click(screen.getByLabelText(`Remove Harbour View Hospital`));
    await user.type(screen.getByLabelText("Name"), "Harbour View Hospital");
    await user.type(screen.getByLabelText(/Website URL/), "https://other.example/");
    await user.click(screen.getByRole("button", { name: "Add hospital" }));
    expect(screen.queryAllByText(new RegExp(RUN_U))).toHaveLength(0);
    expect(screen.queryByText(new RegExp(SUBJECT_U))).not.toBeInTheDocument();
  });

  it("7. missing crawl_run_id does not trigger latest-run substitution", async () => {
    const getRunUrls: string[] = [];
    sessionStorage.setItem("medcompare.workspace.crawlRefs.v1", JSON.stringify([{ kind: "registered", localKey: FACILITY_A, facilityId: FACILITY_A, facilityName: "Northgate General Hospital" }]));
    installRouter({ crawlRunGet: (url) => { getRunUrls.push(url); return { status: 404, body: { detail: "nf" } }; } });
    render(<ComparisonWorkspace />);
    // no stored crawl_run_id: must not substitute latest or call recovery for an arbitrary run
    await screen.findByRole("heading", { name: "Compare hospitals" });
    expect(getRunUrls.length).toBe(0);
    expect(screen.queryByText(/Recovering the exact selected crawl run/)).not.toBeInTheDocument();
  });

  it("8. malformed crawl_run_id produces explicit recovery failure", async () => {
    const getRunUrls: string[] = [];
    sessionStorage.setItem("medcompare.workspace.crawlRefs.v1", JSON.stringify([{ kind: "registered", localKey: FACILITY_A, facilityId: FACILITY_A, crawlRunId: "!!!bad", facilityName: "Northgate General Hospital" }]));
    installRouter({ crawlRunGet: (url) => { getRunUrls.push(url); return { status: 404, body: { detail: "nf" } }; } });
    render(<ComparisonWorkspace />);
    expect(await screen.findByText(/stored crawl run reference is malformed or corrupted/)).toBeInTheDocument();
    expect(getRunUrls.length).toBe(0);
  });

  it("9. ownership mismatch remains rejected", async () => {
    sessionStorage.setItem("medcompare.workspace.crawlRefs.v1", JSON.stringify([{ kind: "registered", localKey: FACILITY_A, facilityId: FACILITY_A, crawlRunId: RUN_A, facilityName: "Northgate General Hospital" }]));
    installRouter({ crawlRunGet: () => ({ status: 403, body: { detail: { error: "crawl_run_ownership_mismatch", message: "mismatch" } } }) });
    render(<ComparisonWorkspace />);
    expect(await screen.findByText(/Ownership\/identity mismatch/)).toBeInTheDocument();
  });

  it("10. failed crawl does not misrepresent previous successful evidence and comparison uses previous snapshot", async () => {
    const posted = installRouter({ crawlBody: CRAWL_RESULT_REGISTERED, compareBody: MATRIX });
    const user = userEvent.setup();
    render(<ComparisonWorkspace />);
    await selectTwoHospitals(user);
    await user.click(screen.getByLabelText(/Website crawling/));
    const extractButtons = await screen.findAllByRole("button", { name: "Extract from website" });
    await user.click(extractButtons[0]!);
    await screen.findAllByText(new RegExp(RUN_A));
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      if (url.includes("/facilities")) return Promise.resolve(ok(200, { items: FACILITIES, identity_note: "note", limit: 50, offset: 0 }));
      if (method === "POST" && url.endsWith("/crawl/extract")) {
        posted.push({ url, body });
        return Promise.resolve(ok(500, { detail: { error: "crawl_failed", message: "Extraction failed." } }));
      }
      if (method === "POST" && url.endsWith("/comparison")) {
        posted.push({ url, body });
        return Promise.resolve(ok(200, MATRIX));
      }
      return Promise.resolve(ok(404, { detail: "nf" }));
    });
    await user.click(extractButtons[0]!);
    expect(await screen.findByText(/Failed extraction attempt/)).toBeInTheDocument();
    expect(screen.getByText(/Previous usable snapshot retained for comparison/)).toBeInTheDocument();
    expect(screen.getAllByText(new RegExp(RUN_A)).length).toBeGreaterThan(0);
    const compareButton = screen.getByRole("button", { name: "Compare hospitals" });
    await waitFor(() => expect(compareButton).toBeEnabled());
    await user.click(compareButton);
    await screen.findByRole("heading", { name: "Comparison result" });
    const compareCall = posted.find((call) => call.url.endsWith("/comparison"));
    expect(compareCall?.body).toMatchObject({ hospitals: [{ type: "registered", facility_id: FACILITY_A, crawl_run_id: RUN_A }, { type: "registered", facility_id: FACILITY_B }] });
  });

  it("11. comparison is blocked for known-unusable or unresolved references", async () => {
    sessionStorage.setItem(
      "medcompare.workspace.crawlRefs.v1",
      JSON.stringify([
        { kind: "registered", localKey: FACILITY_A, facilityId: FACILITY_A, crawlRunId: "!!!bad", facilityName: "Northgate General Hospital" },
        { kind: "registered", localKey: FACILITY_B, facilityId: FACILITY_B, crawlRunId: RUN_A2, facilityName: "Southwind Medical Centre" },
      ]),
    );
    installRouter({
      crawlRunGet: (url) => {
        if (url.includes(RUN_A2)) return { status: 200, body: crawlDetail({ crawl_run_id: RUN_A2, facility_id: FACILITY_B, status: "failed", usable_for_comparison: false, evidence: [] }) };
        return null;
      },
    });
    const user = userEvent.setup();
    render(<ComparisonWorkspace />);
    await screen.findByText(/stored crawl run reference is malformed or corrupted/);
    await screen.findByText(/Historical crawl run is not usable \(failed\)/);
    await user.click(screen.getByLabelText(/Website crawling/));
    const compareButton = screen.getByRole("button", { name: "Compare hospitals" });
    await waitFor(() => expect(compareButton).toBeEnabled());
    await user.click(compareButton);
    const err = await screen.findByTestId("compare-error");
    expect(err).toHaveTextContent(/Comparison blocked/);
    expect(err).toHaveTextContent(/malformed/);
    expect(fetchMock.mock.calls.filter((c) => String(c[0]).endsWith("/comparison")).length).toBe(0);
  });

  it("12. separate choices do not overwrite each other's session references", async () => {
    fetchMock.mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      if (url.includes("/facilities")) return Promise.resolve(ok(200, { items: FACILITIES, identity_note: "note", limit: 50, offset: 0 }));
      if (method === "POST" && url.endsWith("/crawl/extract")) {
        if (body?.facility_id === FACILITY_A) return Promise.resolve(ok(200, { ...CRAWL_RESULT_REGISTERED, crawl_run_id: RUN_A, facility_id: FACILITY_A }));
        if (body?.facility_id === FACILITY_B) return Promise.resolve(ok(200, { ...CRAWL_RESULT_REGISTERED, crawl_run_id: RUN_A2, facility_id: FACILITY_B, target_url: "https://southwind.example/" }));
        return Promise.resolve(ok(200, { ...CRAWL_RESULT_UNREGISTERED, crawl_run_id: RUN_U, comparison_subject_id: SUBJECT_U }));
      }
      return Promise.resolve(ok(404, { detail: "nf" }));
    });
    const user = userEvent.setup();
    render(<ComparisonWorkspace />);
    await selectTwoHospitals(user);
    await user.type(screen.getByLabelText("Name"), "Harbour View Hospital");
    await user.type(screen.getByLabelText(/Website URL/), "https://harbourview.example/");
    await user.click(screen.getByRole("button", { name: "Add hospital" }));
    await user.click(screen.getByLabelText(/Website crawling/));
    const extractButtons = await screen.findAllByRole("button", { name: "Extract from website" });
    await user.click(extractButtons[0]!);
    await screen.findAllByText(new RegExp(RUN_A));
    await user.click(extractButtons[1]!);
    await screen.findAllByText(new RegExp(RUN_A2));
    await user.click(extractButtons[2]!);
    await screen.findAllByText(new RegExp(RUN_U));
    const stored = JSON.parse(sessionStorage.getItem("medcompare.workspace.crawlRefs.v1") || "[]");
    expect(stored.find((r: { facilityId?: string }) => r.facilityId === FACILITY_A).crawlRunId).toBe(RUN_A);
    expect(stored.find((r: { facilityId?: string }) => r.facilityId === FACILITY_B).crawlRunId).toBe(RUN_A2);
    const unreg = stored.find((r: { kind: string }) => r.kind === "unregistered");
    expect(unreg.crawlRunId).toBe(RUN_U);
    expect(unreg.comparisonSubjectId).toBe(SUBJECT_U);
  });
});
