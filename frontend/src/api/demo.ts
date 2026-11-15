/** Explicitly labeled demonstration data - never presented as live API/crawl/registry results. */
import type {
  CapabilityReport,
  ComparisonRequest,
  ComparisonResponse,
  ConsistencyAuditResponse,
  CrawlRequest,
  CrawlResponse,
  CrawlRunDetail,
  FacilityListResponse,
  InterpretResponse,
} from "./types";

export const DEMO_MODE_KEY = "medcompare.demoMode.v1";
export const DEMO_MODE_EVENT = "medcompare:demo-mode-changed";

export function isDemoMode(): boolean {
  if (typeof window === "undefined") return false;
  const params = new URLSearchParams(window.location.search);
  if (params.get("demo") === "1") return true;
  if (params.get("demo") === "0") return false;
  try {
    return window.localStorage.getItem(DEMO_MODE_KEY) === "1";
  } catch {
    return false;
  }
}

export function setDemoMode(enabled: boolean): void {
  try {
    window.localStorage.setItem(DEMO_MODE_KEY, enabled ? "1" : "0");
  } catch {
    /* ignore */
  }
  if (typeof window !== "undefined") {
    const url = new URL(window.location.href);
    if (url.searchParams.has("demo")) {
      if (enabled) {
        url.searchParams.set("demo", "1");
      } else {
        url.searchParams.delete("demo");
      }
      window.history.replaceState({}, "", url);
    }
    window.dispatchEvent(new Event(DEMO_MODE_EVENT));
  }
}

const FAC_A = "11111111-1111-4111-8111-111111111111";
const FAC_B = "22222222-2222-4222-8222-222222222222";
const RUN_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

export const demoFacilitiesData: FacilityListResponse = {
  identity_note:
    "DEMONSTRATION DATA (not from a live MedCompare registry): sample registered facilities for UI walkthrough only.",
  limit: 50,
  offset: 0,
  items: [
    {
      id: FAC_A,
      name: "Northgate General Hospital (demo)",
      organization_id: null,
      organization_name: "Northgate Health Group",
      canonical_url: "https://northgate.example/",
      website_domain: "northgate.example",
      address: "12 Sector 12",
      city: "Northbridge",
      state: "Northbridge State",
      country: "Testland",
      postal_code: "411001",
      status: "active",
    },
    {
      id: FAC_B,
      name: "Southwind Medical Centre (demo)",
      organization_id: null,
      organization_name: null,
      canonical_url: null,
      website_domain: null,
      address: null,
      city: "Southmere",
      state: "Southmere State",
      country: "Testland",
      postal_code: null,
      status: "active",
    },
  ],
};

export function demoInterpretData(prompt: string): InterpretResponse {
  const lower = prompt.toLowerCase();
  const criteria: InterpretResponse["criteria"] = [];
  const add = (key: string, label: string, text: string) =>
    criteria.push({
      key,
      label,
      origin: "custom",
      evidence_needed: `A citable source stating "${label.toLowerCase()}" for this exact facility (demonstration criterion).`,
      matched_source: null,
      overlaps_standard: null,
      original_text: text,
    });
  if (lower.includes("parking")) add("parking", "Parking", "parking");
  if (lower.includes("private")) add("private_rooms", "Private rooms", "private rooms");
  if (lower.includes("visiting")) add("visiting_hours", "Visiting hours", "visiting hours");
  if (criteria.length === 0) add("custom_focus", "Custom focus", prompt.trim() || "custom");
  return {
    criteria,
    clarification_questions: [],
    interpreter: "demo_deterministic_parser (demonstration only)",
    interpretation_note:
      "DEMONSTRATION DATA: locally generated for demo purposes, not from the live API/model.",
  };
}

export const demoCapabilitiesData: CapabilityReport = {
  capabilities: [
    {
      key: "registered_hospital_retrieval",
      label: "Registered hospital retrieval",
      available: true,
      detail: "DEMONSTRATION DATA: sample facilities are supplied locally; no registry is queried.",
      implementation: "demo mode (not live registry)",
      required_integration: null,
    },
    {
      key: "standard_comparison",
      label: "Standard comparison",
      available: true,
      detail: "DEMONSTRATION DATA: sample capability state for UI walkthrough.",
      implementation: "demo mode (not live backend)",
      required_integration: null,
    },
    {
      key: "custom_prompt_interpretation",
      label: "Custom prompt interpretation",
      available: true,
      detail: "DEMONSTRATION DATA: local deterministic parsing only (no live API).",
      implementation: "demo mode",
      required_integration: null,
    },
    {
      key: "database_website_consistency_audit",
      label: "Database-to-website consistency audit",
      available: true,
      detail: "DEMONSTRATION DATA: simulated audit findings; no internal database or website is read.",
      implementation: "demo mode (not live audit)",
      required_integration: null,
    },
    {
      key: "authorized_hospital_system_integration",
      label: "Authorized hospital-system integration",
      available: false,
      detail: "DEMONSTRATION DATA: no hospital API or database connection is configured.",
      implementation: null,
      required_integration:
        "An authorized hospital API/database connection with scoped credentials and an agreed data contract.",
    },
    {
      key: "public_internet_search",
      label: "Public internet research",
      available: false,
      detail: "Not implemented in this deployment; demo cannot perform live research.",
      implementation: null,
      required_integration: "A server-side search provider with an API key.",
    },
    {
      key: "website_crawling_on_request",
      label: "Website crawling (on request)",
      available: true,
      detail: "DEMONSTRATION DATA: simulates a crawl result locally; no live website is fetched.",
      implementation: "demo mode (not live crawl)",
      required_integration: null,
    },
  ],
};

export function demoCrawlData(body: CrawlRequest): CrawlResponse {
  const url = body.url || "https://example.demo/";
  return {
    status: "completed",
    execution: "synchronous",
    job_store_available: false,
    target_url: url,
    pages_fetched: 3,
    hospital_name: body.name || "Demo Hospital",
    evidence: [
      {
        criterion_key: "location",
        value: "Harbour Side, Northbridge (demonstration evidence)",
        source: "website_crawl",
        source_name: "Hospital website (demo)",
        source_url: url,
        retrieved_at: "2026-11-14T00:00:00Z",
        published_at: null,
        status: "available",
        confidence: null,
        note: "DEMONSTRATION DATA: not fetched from a live website.",
      },
    ],
    criteria_without_evidence: ["affordability"],
    warnings: ["DEMONSTRATION DATA: locally simulated crawl result, not a live extraction."],
    crawl_run_id: RUN_A,
    facility_id: body.facility_id ?? null,
    comparison_subject_id: body.comparison_subject_id ?? "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    crawl_status: "completed",
    evidence_count: 1,
    completed_at: "2026-11-14T00:00:01Z",
    retrieved_at: "2026-11-14T00:00:01Z",
  };
}

export function demoCrawlRunData(url: string): CrawlRunDetail {
  const crawl = demoCrawlData({ url });
  return {
    crawl_run_id: RUN_A,
    facility_id: null,
    comparison_subject_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    subject_name: "Demo Hospital",
    subject_location: null,
    subject_website_url: url,
    status: "completed",
    execution_mode: "synchronous",
    target_url: url,
    requested_url: url,
    hospital_name: "Demo Hospital",
    pages_fetched: 3,
    evidence_count: 1,
    evidence: crawl.evidence,
    criteria_without_evidence: ["affordability"],
    warnings: ["DEMONSTRATION DATA: not a persisted server crawl run."],
    usable_for_comparison: true,
    requested_at: "2026-11-14T00:00:00Z",
    completed_at: "2026-11-14T00:00:01Z",
    retrieved_at: "2026-11-14T00:00:01Z",
    failure_code: null,
    failure_message: null,
  };
}

export function demoComparisonData(body: ComparisonRequest): ComparisonResponse {
  const criterion =
    body.criteria?.[0] ??
    ({
      key: "location",
      label: "Location",
      origin: "standard",
      evidence_needed: "Address",
      matched_source: null,
      overlaps_standard: null,
      original_text: null,
    } as const);
  return {
    scope: body.scope,
    source: body.source,
    criteria: body.criteria ?? [criterion],
    columns: [
      {
        column_id: FAC_A,
        kind: "registered",
        name: "Northgate General Hospital (demo)",
        location: "Northbridge, Northbridge State",
        organization_name: "Northgate Health Group",
        website_url: "https://northgate.example/",
        identity_note: "DEMONSTRATION DATA - sample facility only.",
        crawl_run_id: null,
        comparison_subject_id: null,
      },
      {
        column_id: FAC_B,
        kind: "registered",
        name: "Southwind Medical Centre (demo)",
        location: "Southmere, Southmere State",
        organization_name: null,
        website_url: null,
        identity_note: "DEMONSTRATION DATA - sample facility only.",
        crawl_run_id: null,
        comparison_subject_id: null,
      },
    ],
    rows: [
      {
        criterion,
        cells: [
          {
            status: "available",
            value: "Sample evidence value (demonstration)",
            source: "medcompare_server",
            source_name: "MedCompare (demo)",
            source_url: null,
            retrieved_at: "2026-11-14",
            published_at: null,
            confidence: null,
            explanation: "DEMONSTRATION DATA: sample evidence, not from live registry/crawl.",
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
            explanation: "No evidence available (demonstration). This is not evidence that it lacks it.",
          },
        ],
      },
    ],
    source_statuses: [
      {
        source: "medcompare_server",
        status: "active",
        detail: "DEMONSTRATION DATA: simulated server evidence.",
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
        detail: "DEMONSTRATION DATA: simulated crawl (no live fetch).",
        required_integration: null,
      },
    ],
    coverage: [
      { column_id: FAC_A, criteria_total: 1, criteria_with_evidence: 1, coverage_ratio: 1 },
      { column_id: FAC_B, criteria_total: 1, criteria_with_evidence: 0, coverage_ratio: 0 },
    ],
    warnings: [
      "DEMONSTRATION DATA: comparison output is locally generated for UI walkthrough and is not from the live backend or real hospital sources.",
    ],
    disclaimer:
      "DEMONSTRATION DATA - not clinical advice and not live evidence. No winner, clinical quality score or overall ranking is produced.",
  };
}

export function demoConsistencyAuditData(
  facilityId: string,
): ConsistencyAuditResponse {
  const facility =
    demoFacilitiesData.items.find((item) => item.id === facilityId) ??
    demoFacilitiesData.items[0]!;
  const run = demoCrawlData({ url: facility.canonical_url ?? "https://northgate.example/" });
  const hasSnapshot = facility.id === FAC_A;
  return {
    facility_id: facility.id,
    facility_name: facility.name,
    organization_name: facility.organization_name,
    branch_identity_note:
      "DEMONSTRATION DATA: audit is scoped to this physical facility, not the parent brand.",
    internal_record: {
      facility_id: facility.id,
      name: facility.name,
      organization_name: facility.organization_name,
      canonical_url: facility.canonical_url,
      address: facility.address,
      city: facility.city,
      state: facility.state,
      postal_code: facility.postal_code,
      country: facility.country,
      updated_at: "2026-11-14T00:00:00Z",
    },
    website_snapshot: hasSnapshot
      ? {
          crawl_run_id: RUN_A,
          status: "completed",
          target_url: run.target_url,
          requested_url: run.target_url,
          hospital_name: facility.name,
          pages_fetched: run.pages_fetched,
          evidence_count: run.evidence.length,
          usable_for_comparison: true,
          requested_at: "2026-11-14T00:00:00Z",
          completed_at: "2026-11-14T00:00:01Z",
          retrieved_at: "2026-11-14T00:00:01Z",
        }
      : null,
    findings: hasSnapshot
      ? [
          {
            field_key: "facility_name",
            label: "Facility name",
            status: "consistent",
            internal: {
              source: "internal_record",
              source_name: "MedCompare registry (stored facility record)",
              value: facility.name,
              source_url: facility.canonical_url,
              retrieved_at: "2026-11-14T00:00:00Z",
              confidence: 1,
              note: "DEMONSTRATION DATA: sample stored record.",
            },
            website: {
              source: "website_crawl",
              source_name: "Hospital website (demo crawl)",
              value: facility.name,
              source_url: run.target_url,
              retrieved_at: "2026-11-14T00:00:01Z",
              confidence: null,
              note: "DEMONSTRATION DATA: simulated website extraction.",
            },
            explanation:
              "DEMONSTRATION DATA: both simulated sources agree for this sample facility.",
            requires_authorized_integration: false,
          },
          {
            field_key: "location",
            label: "Address / locality",
            status: "differs",
            internal: {
              source: "internal_record",
              source_name: "MedCompare registry (stored facility record)",
              value: `${facility.address ?? ""}, ${facility.city ?? ""}, ${facility.state ?? ""}`.replace(/^, |, $/g, ""),
              source_url: facility.canonical_url,
              retrieved_at: "2026-11-14T00:00:00Z",
              confidence: 1,
              note: "DEMONSTRATION DATA: sample stored record.",
            },
            website: {
              source: "website_crawl",
              source_name: "Hospital website (demo crawl)",
              value: "Harbour Side, Northbridge (demonstration evidence)",
              source_url: run.target_url,
              retrieved_at: "2026-11-14T00:00:01Z",
              confidence: 0.6,
              note: "DEMONSTRATION DATA: simulated website extraction.",
            },
            explanation:
              "DEMONSTRATION DATA: simulated address values differ; manual review would be required.",
            requires_authorized_integration: false,
          },
          {
            field_key: "infrastructure",
            label: "Infrastructure",
            status: "missing_from_internal_record",
            internal: null,
            website: {
              source: "website_crawl",
              source_name: "Hospital website (demo crawl)",
              value: "ICU available (demonstration evidence)",
              source_url: run.target_url,
              retrieved_at: "2026-11-14T00:00:01Z",
              confidence: 0.6,
              note: "DEMONSTRATION DATA: simulated website extraction.",
            },
            explanation:
              "DEMONSTRATION DATA: the website reports this field, but the sample registry does not store it. This is not a database error.",
            requires_authorized_integration: true,
          },
        ]
      : [
          {
            field_key: "facility_name",
            label: "Facility name",
            status: "unverifiable",
            internal: null,
            website: null,
            explanation:
              "DEMONSTRATION DATA: no simulated website snapshot exists, so nothing can be compared.",
            requires_authorized_integration: false,
          },
        ],
    summary: {
      findings_total: hasSnapshot ? 3 : 1,
      consistent: hasSnapshot ? 1 : 0,
      differs: hasSnapshot ? 1 : 0,
      missing_from_website: 0,
      missing_from_internal_record: hasSnapshot ? 1 : 0,
      potentially_outdated: 0,
      unverifiable: hasSnapshot ? 0 : 1,
    },
    warnings: [
      "DEMONSTRATION DATA: not a live consistency audit. No real internal database or website was read.",
    ],
    disclaimer:
      "DEMONSTRATION DATA - not a live audit. Missing information is not proof of absence.",
  };
}
