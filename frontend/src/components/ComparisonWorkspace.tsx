import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  ApiError,
  compareHospitals,
  extractFromWebsite,
  getCrawlRun,
  interpretPrompt,
  searchFacilities,
  toMessage,
} from "../api/client";
import type {
  ComparisonRequest,
  ComparisonResponse,
  ComparisonScope,
  CrawlRequest,
  CrawlResponse,
  CrawlRunDetail,
  CrawlRunStatusValue,
  EvidenceItem,
  EvidenceSource,
  Facility,
  HospitalInput,
  InterpretResponse,
} from "../api/types";
import ComparisonMatrix from "./ComparisonMatrix";

const MAX_HOSPITALS = 6;
const MIN_HOSPITALS = 2;
const SESSION_KEY = "medcompare.workspace.crawlRefs.v1";

const SCOPE_OPTIONS: { value: ComparisonScope; label: string; hint: string }[] = [
  {
    value: "standard_12",
    label: "Standard comparison (12 criteria)",
    hint: "All 12 standard criteria from the MedCompare registry.",
  },
  {
    value: "standard_12_plus_custom",
    label: "Standard + custom criteria",
    hint: "The 12 standard criteria plus criteria interpreted from your own prompt.",
  },
  {
    value: "custom_only",
    label: "Custom criteria only",
    hint: "Only the criteria interpreted from your own prompt.",
  },
];

const SOURCE_OPTIONS: { value: EvidenceSource; label: string; hint: string }[] = [
  {
    value: "medcompare_server",
    label: "MedCompare server",
    hint: "Registered facilities only, read from this server's registry.",
  },
  {
    value: "public_research",
    label: "Public internet research",
    hint: "Research the public web for these hospitals.",
  },
  {
    value: "website_crawl",
    label: "Website crawling (on request)",
    hint: "Extract a hospital's own website when you ask for it, synchronously.",
  },
];

interface CrawlSummary {
  target_url: string;
  pages_fetched: number;
  evidence_count: number;
  status: string;
  execution: string;
  job_store_available: boolean;
  criteria_without_evidence: string[];
  warnings: string[];
}

type ReferenceStateKind =
  | "none"
  | "recovering"
  | "recovered_ok"
  | "missing_reference"
  | "malformed_reference"
  | "inaccessible"
  | "ownership_mismatch"
  | "unusable_historical";

interface ReferenceState {
  kind: ReferenceStateKind;
  message: string | null;
}

interface UsableSnapshot {
  crawlRunId: string;
  crawlStatus: CrawlRunStatusValue;
  crawlSummary: CrawlSummary;
  evidence: EvidenceItem[];
}

interface FailedAttempt {
  message: string;
  crawlRunId: string | null;
  crawlStatus: CrawlRunStatusValue | null;
  at: number;
}

interface PersistedChoiceRef {
  kind: "registered" | "unregistered";
  localKey: string;
  facilityId?: string;
  comparisonSubjectId?: string;
  crawlRunId?: string;
  name?: string;
  location?: string | null;
  websiteUrl?: string | null;
  facilityName?: string;
  facilityCity?: string | null;
  facilityState?: string | null;
  facilityCanonicalUrl?: string | null;
  facilityOrganizationName?: string | null;
  malformedCrawlRunId?: boolean;
}

interface BaseChoice {
  evidence: EvidenceItem[];
  crawlSummary: CrawlSummary | null;
  crawlRunId: string | null;
  crawlStatus: CrawlRunStatusValue | null;
  usableForComparison: boolean | null;
  referenceState: ReferenceState;
  failedAttempt: FailedAttempt | null;
  previousUsableSnapshot: UsableSnapshot | null;
}

interface RegisteredChoice extends BaseChoice {
  kind: "registered";
  facility: Facility;
}

interface UnregisteredChoice extends BaseChoice {
  kind: "unregistered";
  localKey: string;
  comparisonSubjectId: string | null;
  name: string;
  location: string;
  websiteUrl: string;
}

type Choice = RegisteredChoice | UnregisteredChoice;

function choiceKey(choice: Choice): string {
  return choice.kind === "registered" ? choice.facility.id : choice.localKey;
}

function choiceName(choice: Choice): string {
  return choice.kind === "registered" ? choice.facility.name : choice.name;
}

function choiceUrl(choice: Choice): string {
  if (choice.kind === "registered") return choice.facility.canonical_url ?? "";
  return choice.websiteUrl.trim();
}

function choiceLocation(choice: Choice): string {
  if (choice.kind === "registered") {
    return [choice.facility.city, choice.facility.state]
      .filter((part): part is string => Boolean(part))
      .join(", ");
  }
  return choice.location.trim();
}

function isUsableStatus(status: CrawlRunStatusValue | string | null | undefined): boolean {
  return status === "completed" || status === "completed_with_warnings";
}

function isTerminalUnusableStatus(status: CrawlRunStatusValue | string | null | undefined): boolean {
  return status === "failed" || status === "timed_out" || status === "empty";
}

function summaryFromCrawlResponse(response: CrawlResponse): CrawlSummary {
  return {
    target_url: response.target_url,
    pages_fetched: response.pages_fetched,
    evidence_count: response.evidence_count ?? response.evidence.length,
    status: response.crawl_status ?? response.status,
    execution: response.execution,
    job_store_available: response.job_store_available,
    criteria_without_evidence: response.criteria_without_evidence ?? [],
    warnings: response.warnings ?? [],
  };
}

function summaryFromDetail(detail: CrawlRunDetail): CrawlSummary {
  return {
    target_url: detail.target_url,
    pages_fetched: detail.pages_fetched,
    evidence_count: detail.evidence_count,
    status: detail.status,
    execution: detail.execution_mode,
    job_store_available: false,
    criteria_without_evidence: detail.criteria_without_evidence ?? [],
    warnings: detail.warnings ?? [],
  };
}

function getDetailString(detail: unknown, key: string): string | null {
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) return null;
  const value = (detail as Record<string, unknown>)[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function extractCrawlRefsFromError(
  error: unknown,
  choice: Choice,
): { crawlRunId: string | null; comparisonSubjectId: string | null; errorCode: string | null } {
  if (!(error instanceof ApiError)) {
    return { crawlRunId: null, comparisonSubjectId: null, errorCode: null };
  }
  const errorCode = getDetailString(error.detail, "error");
  const crawlRunId = getDetailString(error.detail, "crawl_run_id");
  const rawSubject = getDetailString(error.detail, "comparison_subject_id");
  let comparisonSubjectId: string | null = null;
  if (choice.kind === "unregistered") {
    if (choice.comparisonSubjectId && rawSubject === choice.comparisonSubjectId) {
      comparisonSubjectId = rawSubject;
    }
  }
  return { crawlRunId, comparisonSubjectId, errorCode };
}

function statusFromDetail(detail: CrawlRunDetail): CrawlRunStatusValue {
  return detail.status;
}

function isValidCrawlRunId(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-fA-F-]{8,}$/.test(value.trim());
}

function safeReadRefs(): { refs: PersistedChoiceRef[]; storageMalformed: boolean } {
  try {
    if (typeof sessionStorage === "undefined") return { refs: [], storageMalformed: false };
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (raw === null) return { refs: [], storageMalformed: false };
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return { refs: [], storageMalformed: true };
    }
    if (!Array.isArray(parsed)) return { refs: [], storageMalformed: true };
    const refs: PersistedChoiceRef[] = [];
    for (const item of parsed) {
      if (!item || typeof item !== "object") continue;
      const obj = item as Record<string, unknown>;
      const kind = obj.kind === "registered" || obj.kind === "unregistered" ? obj.kind : null;
      const localKey = typeof obj.localKey === "string" ? obj.localKey : null;
      if (!kind || !localKey) continue;
      const ref: PersistedChoiceRef = { kind, localKey };
      if (typeof obj.facilityId === "string") ref.facilityId = obj.facilityId;
      if (typeof obj.comparisonSubjectId === "string") ref.comparisonSubjectId = obj.comparisonSubjectId;
      if ("crawlRunId" in obj) {
        if (isValidCrawlRunId(obj.crawlRunId)) {
          ref.crawlRunId = String(obj.crawlRunId);
        } else if (obj.crawlRunId !== undefined && obj.crawlRunId !== null) {
          ref.malformedCrawlRunId = true;
        }
      }
      if (typeof obj.name === "string") ref.name = obj.name;
      if (typeof obj.location === "string" || obj.location === null) ref.location = obj.location as string | null;
      if (typeof obj.websiteUrl === "string" || obj.websiteUrl === null) ref.websiteUrl = obj.websiteUrl as string | null;
      if (typeof obj.facilityName === "string") ref.facilityName = obj.facilityName;
      if (typeof obj.facilityCity === "string" || obj.facilityCity === null) ref.facilityCity = obj.facilityCity as string | null;
      if (typeof obj.facilityState === "string" || obj.facilityState === null) ref.facilityState = obj.facilityState as string | null;
      if (typeof obj.facilityCanonicalUrl === "string" || obj.facilityCanonicalUrl === null)
        ref.facilityCanonicalUrl = obj.facilityCanonicalUrl as string | null;
      if (typeof obj.facilityOrganizationName === "string" || obj.facilityOrganizationName === null)
        ref.facilityOrganizationName = obj.facilityOrganizationName as string | null;
      refs.push(ref);
    }
    return { refs, storageMalformed: false };
  } catch {
    return { refs: [], storageMalformed: true };
  }
}

function safeWriteRefs(refs: PersistedChoiceRef[]): void {
  try {
    if (typeof sessionStorage === "undefined") return;
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(refs));
  } catch {
    // best-effort
  }
}

function toPersistedRefs(choices: Choice[]): PersistedChoiceRef[] {
  return choices.map((choice) => {
    if (choice.kind === "registered") {
      const ref: PersistedChoiceRef = {
        kind: "registered",
        localKey: choice.facility.id,
        facilityId: choice.facility.id,
        name: choice.facility.name,
        facilityName: choice.facility.name,
        facilityCity: choice.facility.city,
        facilityState: choice.facility.state,
        facilityCanonicalUrl: choice.facility.canonical_url,
        facilityOrganizationName: choice.facility.organization_name,
      };
      if (choice.crawlRunId) ref.crawlRunId = choice.crawlRunId;
      return ref;
    }
    const ref: PersistedChoiceRef = {
      kind: "unregistered",
      localKey: choice.localKey,
      name: choice.name,
      location: choice.location,
      websiteUrl: choice.websiteUrl,
    };
    if (choice.comparisonSubjectId) ref.comparisonSubjectId = choice.comparisonSubjectId;
    if (choice.crawlRunId) ref.crawlRunId = choice.crawlRunId;
    return ref;
  });
}

function noneState(): ReferenceState {
  return { kind: "none", message: null };
}

function choiceFromRef(ref: PersistedChoiceRef): Choice | null {
  const hasStoredRun = Boolean(ref.crawlRunId);
  const malformed = ref.malformedCrawlRunId === true;
  let referenceState: ReferenceState = noneState();
  if (malformed) {
    referenceState = {
      kind: "malformed_reference",
      message:
        "Exact historical recovery is unavailable because the stored crawl run reference is malformed or corrupted. Re-run extraction.",
    };
  } else if (hasStoredRun) {
    referenceState = { kind: "recovering", message: "Recovering the exact selected crawl run…" };
  } else {
    referenceState = noneState();
  }

  const base = {
    evidence: [] as EvidenceItem[],
    crawlSummary: null as CrawlSummary | null,
    crawlRunId: ref.crawlRunId ?? null,
    crawlStatus: null as CrawlRunStatusValue | null,
    usableForComparison: null as boolean | null,
    referenceState,
    failedAttempt: null as FailedAttempt | null,
    previousUsableSnapshot: null as UsableSnapshot | null,
  };

  if (ref.kind === "registered" && ref.facilityId) {
    const facility: Facility = {
      id: ref.facilityId,
      name: ref.facilityName ?? ref.name ?? "Registered facility",
      organization_id: null,
      organization_name: ref.facilityOrganizationName ?? null,
      canonical_url: ref.facilityCanonicalUrl ?? null,
      website_domain: null,
      address: null,
      city: ref.facilityCity ?? null,
      state: ref.facilityState ?? null,
      country: null,
      postal_code: null,
      status: "active",
    };
    return { kind: "registered", facility, ...base };
  }
  if (ref.kind === "unregistered" && ref.localKey) {
    return {
      kind: "unregistered",
      localKey: ref.localKey,
      comparisonSubjectId: ref.comparisonSubjectId ?? null,
      name: ref.name ?? "Unregistered hospital",
      location: ref.location ?? "",
      websiteUrl: ref.websiteUrl ?? "",
      ...base,
    };
  }
  return null;
}

function ownershipParamsForChoice(choice: Choice): { facility_id?: string; comparison_subject_id?: string } {
  if (choice.kind === "registered") return { facility_id: choice.facility.id };
  return choice.comparisonSubjectId ? { comparison_subject_id: choice.comparisonSubjectId } : {};
}

function snapshotFromChoice(choice: Choice): UsableSnapshot | null {
  if (
    choice.usableForComparison === true &&
    choice.crawlRunId &&
    choice.crawlStatus &&
    isUsableStatus(choice.crawlStatus) &&
    choice.crawlSummary
  ) {
    return {
      crawlRunId: choice.crawlRunId,
      crawlStatus: choice.crawlStatus,
      crawlSummary: choice.crawlSummary,
      evidence: choice.evidence,
    };
  }
  return null;
}

function applyRecoveredDetail(choice: Choice, detail: CrawlRunDetail): Choice {
  if (choice.kind === "registered") {
    if (detail.facility_id !== choice.facility.id) {
      return {
        ...choice,
        evidence: [],
        crawlSummary: null,
        crawlStatus: statusFromDetail(detail),
        usableForComparison: false,
        referenceState: {
          kind: "ownership_mismatch",
          message: "Recovered crawl run does not belong to this facility.",
        },
      };
    }
  } else {
    if (detail.facility_id !== null) {
      return {
        ...choice,
        evidence: [],
        crawlSummary: null,
        crawlStatus: statusFromDetail(detail),
        usableForComparison: false,
        referenceState: {
          kind: "ownership_mismatch",
          message: "Recovered crawl run is not bound to this unregistered subject.",
        },
      };
    }
    if (choice.comparisonSubjectId && detail.comparison_subject_id !== choice.comparisonSubjectId) {
      return {
        ...choice,
        evidence: [],
        crawlSummary: null,
        crawlStatus: statusFromDetail(detail),
        usableForComparison: false,
        referenceState: {
          kind: "ownership_mismatch",
          message: "Recovered crawl run does not belong to this subject.",
        },
      };
    }
  }

  const status = statusFromDetail(detail);
  const usable = detail.usable_for_comparison === true && isUsableStatus(status);
  const base = {
    ...choice,
    crawlRunId: detail.crawl_run_id,
    crawlStatus: status,
    crawlSummary: summaryFromDetail(detail),
    usableForComparison: usable,
    evidence: usable ? detail.evidence : [],
    referenceState: usable
      ? ({ kind: "recovered_ok", message: null } as ReferenceState)
      : ({
          kind: "unusable_historical",
          message:
            detail.failure_message
              ? `Historical crawl run is not usable (${status}): ${detail.failure_message}`
              : `Historical crawl run is not usable (${status}). Re-run extraction.`,
        } as ReferenceState),
  };
  if (choice.kind === "unregistered" && detail.comparison_subject_id) {
    return {
      ...(base as UnregisteredChoice),
      comparisonSubjectId: choice.comparisonSubjectId ?? detail.comparison_subject_id,
    };
  }
  return base as Choice;
}

function toHospitalInput(choice: Choice): HospitalInput | null {
  if (choice.kind === "registered") {
    const payload: HospitalInput = { type: "registered", facility_id: choice.facility.id };
    if (choice.crawlRunId && choice.usableForComparison === true) {
      payload.crawl_run_id = choice.crawlRunId;
    }
    return payload;
  }
  if (choice.crawlRunId && choice.usableForComparison === true) {
    if (!choice.comparisonSubjectId) return null;
    return {
      type: "unregistered",
      comparison_subject_id: choice.comparisonSubjectId,
      crawl_run_id: choice.crawlRunId,
      name: choice.name.trim(),
      location: choice.location.trim() || null,
      website_url: choice.websiteUrl.trim() || null,
    };
  }
  return {
    type: "unregistered",
    ...(choice.comparisonSubjectId ? { comparison_subject_id: choice.comparisonSubjectId } : {}),
    name: choice.name.trim(),
    location: choice.location.trim() || null,
    website_url: choice.websiteUrl.trim() || null,
  };
}

function generateLocalKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `local-${crypto.randomUUID()}`;
  }
  return `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function comparisonBlockReason(choice: Choice, source: EvidenceSource): string | null {
  const name = choiceName(choice);
  const rs = choice.referenceState;
  if (rs.kind === "recovering") {
    return `${name}: crawl reference recovery is still unresolved. Wait for recovery or re-run extraction.`;
  }
  if (rs.kind === "malformed_reference") {
    return `${name}: stored crawl run reference is malformed/corrupted. Exact historical recovery is unavailable; re-run extraction.`;
  }
  if (rs.kind === "missing_reference" && source === "website_crawl") {
    return `${name}: no exact crawl run reference is available for website-crawl comparison. Re-run extraction (latest runs are not substituted).`;
  }
  if (rs.kind === "inaccessible") {
    return `${name}: the selected crawl run reference is inaccessible. Re-run extraction.`;
  }
  if (rs.kind === "ownership_mismatch") {
    return `${name}: selected crawl run reference ownership/identity does not match this hospital. Re-run extraction.`;
  }
  if (rs.kind === "unusable_historical") {
    return `${name}: the selected historical crawl run is not usable. Re-run extraction.`;
  }
  if (choice.usableForComparison === false && isTerminalUnusableStatus(choice.crawlStatus)) {
    return `${name}: selected crawl run is ${choice.crawlStatus} and cannot be used for comparison. Re-run extraction.`;
  }
  if (choice.kind === "unregistered" && choice.crawlRunId && !choice.comparisonSubjectId) {
    return `${name}: unregistered persisted crawl run is missing its server-issued comparison subject id. Re-run extraction.`;
  }
  if (choice.kind === "unregistered" && choice.crawlRunId && choice.usableForComparison !== true) {
    return `${name}: unregistered crawl run reference is unresolved or not usable. Re-run extraction.`;
  }
  if (source === "website_crawl" && choice.kind === "unregistered") {
    if (!(choice.crawlRunId && choice.usableForComparison === true && choice.comparisonSubjectId)) {
      return `${name}: website-crawl comparison requires a validated, subject-bound persisted crawl run. Re-run extraction.`;
    }
  }
  return null;
}

export default function ComparisonWorkspace() {
  const [choices, setChoices] = useState<Choice[]>([]);
  const [facilities, setFacilities] = useState<Facility[]>([]);
  const [identityNote, setIdentityNote] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [loadingFacilities, setLoadingFacilities] = useState(false);
  const [facilitiesError, setFacilitiesError] = useState<string | null>(null);

  const [newName, setNewName] = useState("");
  const [newLocation, setNewLocation] = useState("");
  const [newUrl, setNewUrl] = useState("");
  const [addError, setAddError] = useState<string | null>(null);

  const [scope, setScope] = useState<ComparisonScope>("standard_12");
  const [prompt, setPrompt] = useState("");
  const [interpretation, setInterpretation] = useState<InterpretResponse | null>(null);
  const [interpreting, setInterpreting] = useState(false);
  const [interpretError, setInterpretError] = useState<string | null>(null);

  const [source, setSource] = useState<EvidenceSource>("medcompare_server");
  const [crawlBusyKeys, setCrawlBusyKeys] = useState<Record<string, boolean>>({});
  const [crawlErrors, setCrawlErrors] = useState<Record<string, string>>({});

  const [comparing, setComparing] = useState(false);
  const [compareError, setCompareError] = useState<ApiError | Error | null>(null);
  const [result, setResult] = useState<ComparisonResponse | null>(null);

  const requestSeqRef = useRef<Record<string, number>>({});
  const recoverySeqRef = useRef<Record<string, number>>({});
  const recoveredRef = useRef(false);

  function bumpRequestSeq(key: string): number {
    const next = (requestSeqRef.current[key] ?? 0) + 1;
    requestSeqRef.current[key] = next;
    recoverySeqRef.current[key] = (recoverySeqRef.current[key] ?? 0) + 1;
    return next;
  }
  function bumpRecoverySeq(key: string): number {
    const next = (recoverySeqRef.current[key] ?? 0) + 1;
    recoverySeqRef.current[key] = next;
    return next;
  }
  function isCurrentRequest(key: string, seq: number): boolean {
    return requestSeqRef.current[key] === seq;
  }
  function isCurrentRecovery(key: string, seq: number): boolean {
    return recoverySeqRef.current[key] === seq;
  }
  function persistChoices(next: Choice[]): void {
    safeWriteRefs(toPersistedRefs(next));
  }
  function refState(kind: ReferenceStateKind, message: string | null): ReferenceState {
    return { kind, message };
  }

  async function loadFacilities(search: string): Promise<void> {
    setLoadingFacilities(true);
    setFacilitiesError(null);
    try {
      const data = await searchFacilities(search);
      setFacilities(data.items);
      setIdentityNote(data.identity_note);
      setChoices((previous) =>
        previous.map((choice) => {
          if (choice.kind !== "registered") return choice;
          const fresh = data.items.find((f) => f.id === choice.facility.id);
          return fresh ? { ...choice, facility: fresh } : choice;
        }),
      );
    } catch (cause: unknown) {
      setFacilitiesError(toMessage(cause));
    } finally {
      setLoadingFacilities(false);
    }
  }

  useEffect(() => {
    void (async () => {
      await loadFacilities("");
    })();
  }, []);

  useEffect(() => {
    if (recoveredRef.current) return;
    recoveredRef.current = true;
    const { refs } = safeReadRefs();
    if (refs.length === 0) return;
    const initial: Choice[] = refs
      .map(choiceFromRef)
      .filter((c): c is Choice => c !== null)
      .slice(0, MAX_HOSPITALS);
    if (initial.length === 0) return;
    void Promise.resolve().then(() => {
      setChoices(initial);
      persistChoices(initial);
    });

    initial.forEach((choice) => {
      const key = choiceKey(choice);
      if (choice.referenceState.kind !== "recovering") return;
      if (!choice.crawlRunId) return;
      if (choice.kind === "unregistered" && !choice.comparisonSubjectId) {
        setChoices((prev) => {
          const next = prev.map<Choice>((c) =>
            choiceKey(c) === key
              ? {
                  ...c,
                  referenceState: refState(
                    "inaccessible",
                    "Cannot recover the exact crawl run without a server-issued comparison subject id. Re-run extraction.",
                  ),
                } as Choice
              : c,
          );
          persistChoices(next);
          return next;
        });
        return;
      }
      const seq = bumpRecoverySeq(key);
      void (async () => {
        try {
          const detail = await getCrawlRun(choice.crawlRunId!, ownershipParamsForChoice(choice));
          if (!isCurrentRecovery(key, seq)) return;
          setChoices((prev) => {
            const next = prev.map((c) => {
              if (choiceKey(c) !== key) return c;
              if (c.referenceState.kind === "recovered_ok" && c.crawlRunId && c.crawlRunId !== choice.crawlRunId) {
                return c;
              }
              if (c.referenceState.kind !== "recovering" && c.crawlRunId !== choice.crawlRunId) return c;
              return applyRecoveredDetail(c, detail);
            });
            persistChoices(next);
            return next;
          });
        } catch (cause: unknown) {
          if (!isCurrentRecovery(key, seq)) return;
          const msg = toMessage(cause);
          const code = cause instanceof ApiError ? getDetailString(cause.detail, "error") : null;
          const state: ReferenceState =
            code === "crawl_run_ownership_mismatch"
              ? refState("ownership_mismatch", `Ownership/identity mismatch: ${msg}`)
              : code === "crawl_run_not_found"
                ? refState("inaccessible", `Selected crawl run reference is inaccessible: ${msg}`)
                : refState("inaccessible", `Could not recover exact crawl run: ${msg}`);
          setChoices((prev) => {
            const next = prev.map<Choice>((c) =>
              choiceKey(c) === key
                ? ({ ...c, evidence: [], usableForComparison: false, referenceState: state } as Choice)
                : c,
            );
            persistChoices(next);
            return next;
          });
        }
      })();
    });
  }, []);

  useEffect(() => {
    persistChoices(choices);
  }, [choices]);

  function handleSearch(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    void loadFacilities(query);
  }

  function toggleFacility(facility: Facility): void {
    setChoices((previous) => {
      const existing = previous.findIndex(
        (choice) => choice.kind === "registered" && choice.facility.id === facility.id,
      );
      if (existing >= 0) {
        const key = choiceKey(previous[existing]!);
        bumpRequestSeq(key);
        const next = previous.filter((_, index) => index !== existing);
        persistChoices(next);
        setCrawlErrors((pe) => {
          const n = { ...pe };
          delete n[key];
          return n;
        });
        setCrawlBusyKeys((pb) => {
          const n = { ...pb };
          delete n[key];
          return n;
        });
        return next;
      }
      if (previous.length >= MAX_HOSPITALS) return previous;
      const next: Choice[] = [
        ...previous,
        {
          kind: "registered",
          facility,
          evidence: [],
          crawlSummary: null,
          crawlRunId: null,
          crawlStatus: null,
          usableForComparison: null,
          referenceState: noneState(),
          failedAttempt: null,
          previousUsableSnapshot: null,
        },
      ];
      persistChoices(next);
      return next;
    });
  }

  function addUnregistered(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const name = newName.trim();
    if (!name) {
      setAddError("Enter the hospital's name.");
      return;
    }
    if (choices.length >= MAX_HOSPITALS) {
      setAddError(`You can compare up to ${MAX_HOSPITALS} hospitals at once.`);
      return;
    }
    if (
      choices.some(
        (choice) => choice.kind === "unregistered" && choice.name.toLowerCase() === name.toLowerCase(),
      )
    ) {
      setAddError(`${name} is already in this comparison.`);
      return;
    }
    setAddError(null);
    const choice: UnregisteredChoice = {
      kind: "unregistered",
      localKey: generateLocalKey(),
      comparisonSubjectId: null,
      name,
      location: newLocation.trim(),
      websiteUrl: newUrl.trim(),
      evidence: [],
      crawlSummary: null,
      crawlRunId: null,
      crawlStatus: null,
      usableForComparison: null,
      referenceState: noneState(),
      failedAttempt: null,
      previousUsableSnapshot: null,
    };
    setChoices((previous) => {
      const next = [...previous, choice];
      persistChoices(next);
      return next;
    });
    setNewName("");
    setNewLocation("");
    setNewUrl("");
  }

  function removeChoice(key: string): void {
    bumpRequestSeq(key);
    setCrawlErrors((previous) => {
      const next = { ...previous };
      delete next[key];
      return next;
    });
    setCrawlBusyKeys((previous) => {
      const next = { ...previous };
      delete next[key];
      return next;
    });
    setChoices((previous) => {
      const next = previous.filter((choice) => choiceKey(choice) !== key);
      persistChoices(next);
      return next;
    });
  }

  function handleInterpret(): void {
    if (!prompt.trim()) {
      setInterpretError("Describe what to compare before interpreting.");
      return;
    }
    setInterpreting(true);
    setInterpretError(null);
    interpretPrompt(prompt)
      .then((response) => setInterpretation(response))
      .catch((cause: unknown) => {
        setInterpretation(null);
        setInterpretError(toMessage(cause));
      })
      .finally(() => setInterpreting(false));
  }

  async function runCrawl(choice: Choice): Promise<void> {
    const key = choiceKey(choice);
    const url = choiceUrl(choice);
    if (!url) {
      setCrawlErrors((previous) => ({ ...previous, [key]: "Enter a website URL before extracting evidence." }));
      return;
    }
    const seq = bumpRequestSeq(key);
    setCrawlBusyKeys((prev) => ({ ...prev, [key]: true }));
    setCrawlErrors((previous) => ({ ...previous, [key]: "" }));

    const request: CrawlRequest = {
      url,
      max_pages: 15,
      ...(choice.kind === "registered" ? { facility_id: choice.facility.id } : {}),
      ...(choice.kind === "unregistered"
        ? {
            name: choice.name.trim() || null,
            location: choice.location.trim() || null,
            ...(choice.comparisonSubjectId ? { comparison_subject_id: choice.comparisonSubjectId } : {}),
          }
        : {}),
    };

    try {
      const response = await extractFromWebsite(request);
      if (!isCurrentRequest(key, seq)) return;
      setChoices((previous) => {
        const next = previous.map((item) => {
          if (choiceKey(item) !== key) return item;
          const status = (response.crawl_status ?? null) as CrawlRunStatusValue | null;
          const usable = isUsableStatus(status);
          const updatedBase = {
            ...item,
            crawlRunId: response.crawl_run_id ?? null,
            crawlStatus: status,
            usableForComparison: usable,
            crawlSummary: summaryFromCrawlResponse(response),
            evidence: usable ? response.evidence : [],
            failedAttempt: null,
            previousUsableSnapshot: null,
            referenceState: usable
              ? ({ kind: "recovered_ok", message: null } as ReferenceState)
              : ({
                  kind: "unusable_historical",
                  message: "Extraction completed but did not produce a usable evidence snapshot. Re-run extraction.",
                } as ReferenceState),
          };
          if (item.kind === "unregistered") {
            return {
              ...(updatedBase as UnregisteredChoice),
              comparisonSubjectId: response.comparison_subject_id ?? item.comparisonSubjectId,
            };
          }
          return updatedBase as Choice;
        });
        persistChoices(next);
        return next;
      });
    } catch (cause: unknown) {
      if (!isCurrentRequest(key, seq)) return;
      const refs = extractCrawlRefsFromError(cause, choice);
      const msg = toMessage(cause);
      setCrawlErrors((previous) => ({ ...previous, [key]: msg }));
      setChoices((previous) => {
        const next = previous.map((item) => {
          if (choiceKey(item) !== key) return item;
          const prevSnapshot = snapshotFromChoice(item);
          const attemptStatus: CrawlRunStatusValue | null =
            refs.errorCode === "crawl_exceeded_request_window" ? "timed_out" : null;
          const failedAttempt: FailedAttempt = {
            message: msg,
            crawlRunId: refs.crawlRunId,
            crawlStatus: attemptStatus,
            at: Date.now(),
          };
          if (prevSnapshot) {
            return {
              ...item,
              crawlRunId: prevSnapshot.crawlRunId,
              crawlStatus: prevSnapshot.crawlStatus,
              crawlSummary: prevSnapshot.crawlSummary,
              evidence: prevSnapshot.evidence,
              usableForComparison: true,
              previousUsableSnapshot: prevSnapshot,
              failedAttempt,
              referenceState: { kind: "recovered_ok", message: null },
            } as Choice;
          }
          const refState: ReferenceState = refs.crawlRunId
            ? {
                kind: "unusable_historical",
                message:
                  refs.errorCode === "crawl_exceeded_request_window"
                    ? "Extraction attempt timed out and is not usable. Re-run extraction."
                    : "Extraction attempt failed and is not usable. Re-run extraction.",
              }
            : { kind: "none", message: null };
          const updatedBase = {
            ...item,
            crawlRunId: refs.crawlRunId ?? item.crawlRunId,
            crawlStatus: refs.crawlRunId ? attemptStatus : item.crawlStatus,
            usableForComparison: refs.crawlRunId ? false : item.usableForComparison,
            evidence: refs.crawlRunId ? [] : item.evidence,
            failedAttempt,
            previousUsableSnapshot: null,
            referenceState: refState,
          };
          if (item.kind === "unregistered" && refs.comparisonSubjectId) {
            return {
              ...(updatedBase as UnregisteredChoice),
              comparisonSubjectId: item.comparisonSubjectId ?? refs.comparisonSubjectId,
            };
          }
          return updatedBase as Choice;
        });
        persistChoices(next);
        return next;
      });
    } finally {
      if (isCurrentRequest(key, seq)) {
        setCrawlBusyKeys((prev) => {
          const next = { ...prev };
          delete next[key];
          return next;
        });
      }
    }
  }

  const needsInterpretation = scope !== "standard_12";
  const atCapacity = choices.length >= MAX_HOSPITALS;
  const unregisteredChoices = choices.filter(
    (choice): choice is UnregisteredChoice => choice.kind === "unregistered",
  );
  const interpretationIncomplete =
    needsInterpretation && (interpretation === null || interpretation.criteria.length === 0);
  const canCompare = choices.length >= MIN_HOSPITALS && !interpretationIncomplete && !comparing;

  function handleCompare(): void {
    if (!canCompare) return;
    const blockReasons = choices
      .map((c) => comparisonBlockReason(c, source))
      .filter((r): r is string => Boolean(r));
    if (blockReasons.length > 0) {
      setCompareError(new Error(`Comparison blocked: ${blockReasons.join(" ")}`));
      return;
    }
    const hospitals = choices.map(toHospitalInput);
    if (hospitals.some((h) => h === null)) {
      setCompareError(
        new Error(
          "Comparison blocked: an unregistered persisted crawl run is missing its server-issued comparison subject id. Re-run extraction.",
        ),
      );
      return;
    }
    setComparing(true);
    setCompareError(null);
    const request: ComparisonRequest = {
      scope,
      source,
      hospitals: hospitals as HospitalInput[],
      ...(needsInterpretation && interpretation ? { criteria: interpretation.criteria } : {}),
    };
    compareHospitals(request)
      .then((response) => setResult(response))
      .catch((cause: unknown) => {
        setResult(null);
        setCompareError(cause instanceof Error ? cause : new Error(toMessage(cause)));
      })
      .finally(() => setComparing(false));
  }

  const compareHint = interpretationIncomplete
    ? "Interpret your prompt first: this scope needs custom criteria."
    : choices.length < MIN_HOSPITALS
      ? `Choose at least ${MIN_HOSPITALS} hospitals to compare.`
      : null;

  return (
    <section aria-labelledby="workspace-heading" className="panel workspace">
      <h2 id="workspace-heading">Compare hospitals</h2>
      <p className="muted intro">
        Workflow: describe criteria (NL) → interpret &amp; review → select/add hospitals → choose evidence source and crawl
        if needed → compare with provenance. Missing/unavailable evidence is shown honestly.
      </p>

      <section aria-labelledby="hospitals-heading" className="step">
        <h3 id="hospitals-heading">1. Choose hospitals ({choices.length}/{MAX_HOSPITALS})</h3>
        <form className="search-form" onSubmit={handleSearch} role="search">
          <label htmlFor="facility-search">Search registered facilities</label>
          <div className="search-row">
            <input
              id="facility-search"
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Facility name, e.g. Northgate"
            />
            <button type="submit" disabled={loadingFacilities}>
              {loadingFacilities ? "Searching…" : "Search"}
            </button>
          </div>
        </form>

        {facilitiesError ? (
          <p className="callout callout-error" role="alert">
            Could not load registered facilities: {facilitiesError}
          </p>
        ) : null}
        {identityNote ? <p className="muted">{identityNote}</p> : null}

        <ul className="facility-results">
          {facilities.map((facility) => {
            const selected = choices.some(
              (choice) => choice.kind === "registered" && choice.facility.id === facility.id,
            );
            return (
              <li key={facility.id}>
                <input
                  type="checkbox"
                  id={`facility-${facility.id}`}
                  checked={selected}
                  disabled={!selected && atCapacity}
                  onChange={() => toggleFacility(facility)}
                />
                <label htmlFor={`facility-${facility.id}`}>
                  {facility.name}
                  <span className="muted">
                    {[facility.city, facility.state, facility.organization_name ? `part of ${facility.organization_name}` : ""]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </label>
              </li>
            );
          })}
          {!loadingFacilities && facilities.length === 0 && !facilitiesError ? (
            <li className="muted">No registered facilities matched.</li>
          ) : null}
        </ul>

        <form className="unregistered-form" onSubmit={addUnregistered}>
          <h4>Add a hospital that is not registered</h4>
          <div className="form-grid">
            <label htmlFor="unregistered-name">
              Name
              <input
                id="unregistered-name"
                type="text"
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                placeholder="Harbour View Hospital"
              />
            </label>
            <label htmlFor="unregistered-location">
              Location
              <input
                id="unregistered-location"
                type="text"
                value={newLocation}
                onChange={(event) => setNewLocation(event.target.value)}
                placeholder="Testville"
              />
            </label>
            <label htmlFor="unregistered-url">
              Website URL (for website crawling)
              <input
                id="unregistered-url"
                type="url"
                value={newUrl}
                onChange={(event) => setNewUrl(event.target.value)}
                placeholder="https://example-hospital.org/"
              />
            </label>
          </div>
          <button type="submit" disabled={atCapacity}>
            Add hospital
          </button>
          {atCapacity ? <p className="muted">The comparison is full.</p> : null}
          {addError ? (
            <p className="callout callout-error" role="alert">
              {addError}
            </p>
          ) : null}
        </form>

        {choices.length > 0 ? (
          <ul className="chip-list" aria-label="Selected hospitals">
            {choices.map((choice) => (
              <li key={choiceKey(choice)}>
                <span>
                  {choiceName(choice)}
                  {choiceLocation(choice) ? ` — ${choiceLocation(choice)}` : ""}
                </span>
                <button
                  type="button"
                  className="chip-remove"
                  onClick={() => removeChoice(choiceKey(choice))}
                  aria-label={`Remove ${choiceName(choice)}`}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        {choices.some((choice) => choice.referenceState.kind !== "none" || choice.crawlRunId) ? (
          <div className="crawl-status-summary">
            {choices.map((choice) => {
              if (choice.referenceState.kind === "none" && !choice.crawlRunId) return null;
              const showMessage = choice.referenceState.message;
              const statusText =
                choice.referenceState.kind === "recovered_ok" && !showMessage
                  ? `selected crawl run ${choice.crawlStatus ?? "recovered"}`
                  : null;
              if (!showMessage && !statusText && !choice.crawlRunId) return null;
              return (
                <p
                  key={`recovery-${choiceKey(choice)}`}
                  className={
                    choice.referenceState.kind === "recovered_ok" || choice.referenceState.kind === "none"
                      ? "muted"
                      : "callout callout-warning"
                  }
                  role={
                    choice.referenceState.kind === "recovered_ok" || choice.referenceState.kind === "none"
                      ? undefined
                      : "status"
                  }
                >
                  {choiceName(choice)}: {showMessage ?? statusText}
                  {choice.crawlRunId ? (
                    <>
                      {" "}
                      <code>{choice.crawlRunId}</code>
                    </>
                  ) : null}
                </p>
              );
            })}
          </div>
        ) : null}
      </section>

      <section aria-labelledby="scope-heading" className="step">
        <h3 id="scope-heading">2. Comparison scope</h3>
        <fieldset className="radio-group">
          <legend>What should be compared?</legend>
          {SCOPE_OPTIONS.map((option) => (
            <label key={option.value} htmlFor={`scope-${option.value}`}>
              <input
                id={`scope-${option.value}`}
                type="radio"
                name="scope"
                value={option.value}
                checked={scope === option.value}
                onChange={() => setScope(option.value)}
              />
              <span>
                <strong>{option.label}</strong>
                <span className="muted">{option.hint}</span>
              </span>
            </label>
          ))}
        </fieldset>

        {needsInterpretation ? (
          <div className="prompt-box">
            <label htmlFor="custom-prompt">What should be compared in addition to the standard criteria?</label>
            <textarea
              id="custom-prompt"
              rows={3}
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="Compare parking, private rooms and visiting hours"
            />
            <button type="button" onClick={handleInterpret} disabled={interpreting || !prompt.trim()}>
              {interpreting ? "Interpreting…" : "Interpret prompt"}
            </button>
            {interpretError ? (
              <p className="callout callout-error" role="alert">
                {interpretError}
              </p>
            ) : null}
            {interpretation ? (
              <div className="interpretation">
                <p className="muted">{interpretation.interpretation_note}</p>
                <p className="muted">Interpreter: {interpretation.interpreter}</p>
                {interpretation.criteria.length > 0 ? (
                  <ul className="chip-list" aria-label="Interpreted criteria">
                    {interpretation.criteria.map((criterion) => (
                      <li key={criterion.key}>
                        <span>{criterion.label}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
                {interpretation.clarification_questions.length > 0 ? (
                  <div className="callout callout-warning" role="status">
                    <p>This prompt needs clarification before criteria can be used:</p>
                    <ul className="plain-list">
                      {interpretation.clarification_questions.map((question) => (
                        <li key={question}>{question}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
      </section>

      <section aria-labelledby="source-heading" className="step">
        <h3 id="source-heading">3. Evidence source</h3>
        <fieldset className="radio-group">
          <legend>Where should evidence come from?</legend>
          {SOURCE_OPTIONS.map((option) => (
            <label key={option.value} htmlFor={`source-${option.value}`}>
              <input
                id={`source-${option.value}`}
                type="radio"
                name="source"
                value={option.value}
                checked={source === option.value}
                onChange={() => setSource(option.value)}
              />
              <span>
                <strong>{option.label}</strong>
                <span className="muted">{option.hint}</span>
              </span>
            </label>
          ))}
        </fieldset>

        {source === "medcompare_server" && unregisteredChoices.length > 0 ? (
          <p className="callout callout-warning" role="status">
            {unregisteredChoices.map((choice) => choice.name).join(", ")}{" "}
            {unregisteredChoices.length === 1 ? "is" : "are"} not registered on this server. Server evidence cannot describe{" "}
            {unregisteredChoices.length === 1 ? "it" : "them"} — switch to website crawling (with a URL) or public research.
          </p>
        ) : null}

        {source === "website_crawl" ? (
          <div className="crawl-box">
            <h4>Extract from a hospital website</h4>
            <p className="muted">
              Crawls run when you press the button, inside this request. Server-issued crawl run references are stored and
              used for comparison/recovery.
            </p>
            <ul className="plain-list">
              {choices.map((choice) => {
                const key = choiceKey(choice);
                const url = choiceUrl(choice);
                const busy = Boolean(crawlBusyKeys[key]);
                const error = crawlErrors[key];
                const summary = choice.crawlSummary;
                return (
                  <li key={key} className="crawl-item">
                    <strong>{choiceName(choice)}</strong>
                    <code>{url || "no URL set"}</code>
                    <button type="button" onClick={() => void runCrawl(choice)} disabled={!url}>
                      {busy ? "Extracting…" : "Extract from website"}
                    </button>
                    {choice.crawlRunId ? (
                      <p className="muted">
                        Crawl run: <code>{choice.crawlRunId}</code>
                        {choice.kind === "unregistered" && choice.comparisonSubjectId ? ` · subject ${choice.comparisonSubjectId}` : ""}
                        {choice.crawlStatus ? ` · ${choice.crawlStatus}` : ""}
                      </p>
                    ) : null}
                    {choice.failedAttempt ? (
                      <p className="callout callout-warning" role="status">
                        Failed extraction attempt: {choice.failedAttempt.message}
                        {choice.failedAttempt.crawlRunId ? (
                          <>
                            {" "}
                            (attempt run <code>{choice.failedAttempt.crawlRunId}</code>
                            {choice.failedAttempt.crawlStatus ? `, ${choice.failedAttempt.crawlStatus}` : ""}, not usable)
                          </>
                        ) : null}
                        {choice.previousUsableSnapshot ? (
                          <>
                            {" "}
                            Previous usable snapshot retained for comparison: <code>{choice.previousUsableSnapshot.crawlRunId}</code> (
                            {choice.previousUsableSnapshot.crawlStatus}).
                          </>
                        ) : null}
                      </p>
                    ) : null}
                    {error && !choice.failedAttempt ? (
                      <p className="callout callout-error" role="alert">
                        {error}
                      </p>
                    ) : null}
                    {choice.referenceState.kind !== "none" &&
                    choice.referenceState.kind !== "recovered_ok" &&
                    choice.referenceState.message ? (
                      <p className="callout callout-warning" role="status">
                        {choice.referenceState.message}
                      </p>
                    ) : null}
                    {summary ? (
                      <div className="crawl-summary">
                        <p>
                          {summary.pages_fetched} page(s) fetched from <code>{summary.target_url}</code> · {summary.evidence_count} evidence item(s)
                        </p>
                        <p className="muted">
                          Execution: {summary.execution} · persistent job store: {summary.job_store_available ? "available" : "not available"}
                        </p>
                        {summary.criteria_without_evidence.length > 0 ? (
                          <p className="muted">No evidence found for: {summary.criteria_without_evidence.join(", ")}</p>
                        ) : null}
                        {summary.warnings.map((warning) => (
                          <p key={warning} className="callout callout-warning" role="status">
                            {warning}
                          </p>
                        ))}
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </div>
        ) : null}
      </section>

      <div className="compare-actions">
        <button type="button" className="primary" onClick={handleCompare} disabled={!canCompare}>
          {comparing ? "Comparing…" : "Compare hospitals"}
        </button>
        {compareHint ? <p className="muted">{compareHint}</p> : null}
      </div>

      {compareError ? (
        <div className="callout callout-error" role="alert" data-testid="compare-error">
          <p>{compareError.message}</p>
          {compareError instanceof ApiError && compareError.requiredIntegration ? (
            <p className="required-integration">Missing integration: {compareError.requiredIntegration}</p>
          ) : null}
        </div>
      ) : null}

      {result ? <ComparisonMatrix result={result} /> : null}
    </section>
  );
}
