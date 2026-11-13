import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import ComparisonWorkspace from "./ComparisonWorkspace";

const fetchMock = vi.fn();

const FACILITY_A = "11111111-1111-1111-1111-111111111111";
const FACILITY_B = "22222222-2222-2222-2222-222222222222";

const FACILITIES = [
  {
    id: FACILITY_A,
    name: "Northgate General Hospital",
    organization_id: null,
    organization_name: "Northgate Health Group",
    canonical_url: null,
    website_domain: null,
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
    canonical_url: null,
    website_domain: null,
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
          explanation:
            "Nothing was found for this facility, which is not evidence that it lacks it.",
        },
      ],
    },
  ],
  source_statuses: [
    {
      source: "medcompare_server",
      status: "active",
      detail: "Evidence read from the MedCompare hospital registry.",
      required_integration: null,
    },
    {
      source: "public_research",
      status: "not_configured",
      detail: "No search provider client exists in this deployment.",
      required_integration: "A server-side search provider with an API key.",
    },
    {
      source: "website_crawl",
      status: "active",
      detail: "Extraction runs synchronously when requested.",
      required_integration: null,
    },
  ],
  coverage: [
    { column_id: FACILITY_A, criteria_total: 1, criteria_with_evidence: 1, coverage_ratio: 1 },
    { column_id: FACILITY_B, criteria_total: 1, criteria_with_evidence: 0, coverage_ratio: 0 },
  ],
  warnings: [],
  disclaimer:
    "Comparison reports evidence only. No winner, clinical quality score or overall ranking is produced.",
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
  interpretation_note:
    "No language model is configured, so your wording was parsed deterministically.",
};

const CRAWL_RESULT = {
  status: "completed",
  execution: "synchronous",
  job_store_available: false,
  target_url: "https://harbourview.example/",
  pages_fetched: 4,
  hospital_name: "Harbour View Hospital",
  evidence: [
    {
      criterion_key: "location",
      value: "Harbour Side, Testville",
      source: "website_crawl",
      source_name: "Hospital website (crawled)",
      source_url: "https://harbourview.example/",
      retrieved_at: "2026-11-13T10:00:00Z",
      published_at: null,
      status: "available",
      confidence: null,
      note: null,
    },
  ],
  criteria_without_evidence: ["affordability"],
  warnings: [
    "Extraction ran synchronously in this request. This deployment has no persistent crawl job store, so no job id or status endpoint exists yet.",
  ],
};

interface PostedCall {
  url: string;
  body: unknown;
}

function ok(status: number, body: unknown) {
  return { ok: status < 300, status, json: async () => body };
}

function installRouter(options?: {
  compareStatus?: number;
  compareBody?: unknown;
  interpretBody?: unknown;
  crawlBody?: unknown;
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
      return Promise.resolve(
        ok(options?.compareStatus ?? 200, options?.compareBody ?? MATRIX),
      );
    }
    if (method === "POST" && url.endsWith("/crawl/extract")) {
      posted.push({ url, body });
      return Promise.resolve(ok(200, options?.crawlBody ?? CRAWL_RESULT));
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
  expect(screen.getByText("MedCompare registry")).toBeInTheDocument();
  expect(screen.getByText(/2026-11-13/)).toBeInTheDocument();

  expect(posted).toHaveLength(1);
  expect(posted[0]?.body).toMatchObject({
    scope: "standard_12",
    source: "medcompare_server",
    hospitals: [
      { type: "registered", facility_id: FACILITY_A },
      { type: "registered", facility_id: FACILITY_B },
    ],
  });
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

  await user.type(
    screen.getByLabelText(/What should be compared in addition/),
    "compare parking",
  );
  await user.click(screen.getByRole("button", { name: "Interpret prompt" }));

  const criteriaList = await screen.findByLabelText("Interpreted criteria");
  expect(criteriaList).toHaveTextContent("Parking");
  expect(screen.getByText(/No language model is configured/)).toBeInTheDocument();
  await waitFor(() => expect(compareButton).toBeEnabled());

  await user.click(compareButton);
  await screen.findByRole("heading", { name: "Comparison result" });
  expect(posted[0]?.body).toMatchObject({
    scope: "custom_only",
    criteria: [{ key: "parking", label: "Parking", origin: "custom" }],
  });
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
  expect(screen.getByText(/no persistent crawl job store/)).toBeInTheDocument();

  const crawlCall = posted.find((call) => call.url.endsWith("/crawl/extract"));
  expect(crawlCall?.body).toMatchObject({
    url: "https://harbourview.example/",
    max_pages: 15,
  });
});
