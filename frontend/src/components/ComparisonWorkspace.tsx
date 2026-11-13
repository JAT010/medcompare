import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, compareHospitals, extractFromWebsite, interpretPrompt, searchFacilities, toMessage } from "../api/client";
import type {
  ComparisonResponse,
  ComparisonScope,
  CrawlResponse,
  EvidenceItem,
  EvidenceSource,
  Facility,
  HospitalInput,
  InterpretResponse,
} from "../api/types";
import ComparisonMatrix from "./ComparisonMatrix";

const MAX_HOSPITALS = 6;
const MIN_HOSPITALS = 2;

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

interface BaseChoice {
  evidence: EvidenceItem[];
  crawl: CrawlResponse | null;
}

interface RegisteredChoice extends BaseChoice {
  kind: "registered";
  facility: Facility;
}

interface UnregisteredChoice extends BaseChoice {
  kind: "unregistered";
  id: string;
  name: string;
  location: string;
  websiteUrl: string;
}

type Choice = RegisteredChoice | UnregisteredChoice;

function choiceKey(choice: Choice): string {
  return choice.kind === "registered" ? choice.facility.id : choice.id;
}

function choiceName(choice: Choice): string {
  return choice.kind === "registered" ? choice.facility.name : choice.name;
}

function choiceUrl(choice: Choice): string {
  if (choice.kind === "registered") {
    return choice.facility.canonical_url ?? "";
  }
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

function toHospitalInput(choice: Choice): HospitalInput {
  if (choice.kind === "registered") {
    return { type: "registered", facility_id: choice.facility.id, evidence: choice.evidence };
  }
  return {
    type: "unregistered",
    name: choice.name.trim(),
    location: choice.location.trim() || null,
    website_url: choice.websiteUrl.trim() || null,
    evidence: choice.evidence,
  };
}

/** Workspace holding the whole comparison flow: hospitals, scope, source, result. */
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
  const [crawlBusyKey, setCrawlBusyKey] = useState<string | null>(null);
  const [crawlErrors, setCrawlErrors] = useState<Record<string, string>>({});

  const [comparing, setComparing] = useState(false);
  const [compareError, setCompareError] = useState<ApiError | Error | null>(null);
  const [result, setResult] = useState<ComparisonResponse | null>(null);

  async function loadFacilities(search: string): Promise<void> {
    setLoadingFacilities(true);
    setFacilitiesError(null);
    try {
      const data = await searchFacilities(search);
      setFacilities(data.items);
      setIdentityNote(data.identity_note);
    } catch (cause: unknown) {
      setFacilitiesError(toMessage(cause));
    } finally {
      setLoadingFacilities(false);
    }
  }

  useEffect(() => {
    void loadFacilities("");
  }, []);

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
        return previous.filter((_, index) => index !== existing);
      }
      if (previous.length >= MAX_HOSPITALS) {
        return previous;
      }
      return [...previous, { kind: "registered", facility, evidence: [], crawl: null }];
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
    setChoices((previous) => [
      ...previous,
      {
        kind: "unregistered",
        id: `custom-${Date.now()}-${previous.length}`,
        name,
        location: newLocation.trim(),
        websiteUrl: newUrl.trim(),
        evidence: [],
        crawl: null,
      },
    ]);
    setNewName("");
    setNewLocation("");
    setNewUrl("");
  }

  function removeChoice(key: string): void {
    setChoices((previous) => previous.filter((choice) => choiceKey(choice) !== key));
  }

  function handleInterpret(): void {
    if (!prompt.trim()) {
      setInterpretError("Describe what to compare before interpreting.");
      return;
    }
    setInterpreting(true);
    setInterpretError(null);
    interpretPrompt(prompt)
      .then((response) => {
        setInterpretation(response);
      })
      .catch((cause: unknown) => {
        setInterpretation(null);
        setInterpretError(toMessage(cause));
      })
      .finally(() => {
        setInterpreting(false);
      });
  }

  async function runCrawl(choice: Choice): Promise<void> {
    const key = choiceKey(choice);
    const url = choiceUrl(choice);
    if (!url) {
      setCrawlErrors((previous) => ({
        ...previous,
        [key]: "Enter a website URL before extracting evidence.",
      }));
      return;
    }
    setCrawlBusyKey(key);
    setCrawlErrors((previous) => ({ ...previous, [key]: "" }));
    try {
      const response = await extractFromWebsite({ url, max_pages: 15 });
      setChoices((previous) =>
        previous.map((item) =>
          choiceKey(item) === key
            ? { ...item, evidence: response.evidence, crawl: response }
            : item,
        ),
      );
    } catch (cause: unknown) {
      setCrawlErrors((previous) => ({ ...previous, [key]: toMessage(cause) }));
    } finally {
      setCrawlBusyKey(null);
    }
  }

  const needsInterpretation = scope !== "standard_12";
  const atCapacity = choices.length >= MAX_HOSPITALS;
  const unregisteredChoices = choices.filter(
    (choice): choice is UnregisteredChoice => choice.kind === "unregistered",
  );
  const interpretationIncomplete =
    needsInterpretation && (interpretation === null || interpretation.criteria.length === 0);
  const canCompare =
    choices.length >= MIN_HOSPITALS && !interpretationIncomplete && !comparing;

  function handleCompare(): void {
    if (!canCompare) {
      return;
    }
    setComparing(true);
    setCompareError(null);
    const request = {
      scope,
      source,
      hospitals: choices.map(toHospitalInput),
      ...(needsInterpretation && interpretation
        ? { criteria: interpretation.criteria }
        : {}),
    };
    compareHospitals(request)
      .then((response) => {
        setResult(response);
      })
      .catch((cause: unknown) => {
        setResult(null);
        setCompareError(cause instanceof Error ? cause : new Error(toMessage(cause)));
      })
      .finally(() => {
        setComparing(false);
      });
  }

  const compareHint = interpretationIncomplete
    ? "Interpret your prompt first: this scope needs custom criteria."
    : choices.length < MIN_HOSPITALS
      ? `Choose at least ${MIN_HOSPITALS} hospitals to compare.`
      : null;

  return (
    <section aria-labelledby="workspace-heading" className="panel">
      <h2 id="workspace-heading">Compare hospitals</h2>

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
                    {[
                      facility.city,
                      facility.state,
                      facility.organization_name ? `part of ${facility.organization_name}` : "",
                    ]
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
            <label htmlFor="custom-prompt">
              What should be compared in addition to the standard criteria?
            </label>
            <textarea
              id="custom-prompt"
              rows={3}
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="Compare parking, private rooms and visiting hours"
            />
            <button
              type="button"
              onClick={handleInterpret}
              disabled={interpreting || !prompt.trim()}
            >
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
            {unregisteredChoices.length === 1 ? "is" : "are"} not registered on this
            server. Server evidence cannot describe{" "}
            {unregisteredChoices.length === 1 ? "it" : "them"} — switch to website
            crawling (with a URL) or public research.
          </p>
        ) : null}

        {source === "website_crawl" ? (
          <div className="crawl-box">
            <h4>Extract from a hospital website</h4>
            <p className="muted">
              Crawls run when you press the button, inside this request. There is no
              job to poll yet: results arrive with this page, or the request reports
              that the window was exceeded.
            </p>
            <ul className="plain-list">
              {choices.map((choice) => {
                const key = choiceKey(choice);
                const url = choiceUrl(choice);
                const busy = crawlBusyKey === key;
                const error = crawlErrors[key];
                const summary = choice.crawl;
                return (
                  <li key={key} className="crawl-item">
                    <strong>{choiceName(choice)}</strong>
                    <code>{url || "no URL set"}</code>
                    <button
                      type="button"
                      onClick={() => void runCrawl(choice)}
                      disabled={busy || !url}
                    >
                      {busy ? "Extracting…" : "Extract from website"}
                    </button>
                    {error ? (
                      <p className="callout callout-error" role="alert">
                        {error}
                      </p>
                    ) : null}
                    {summary ? (
                      <div className="crawl-summary">
                        <p>
                          {summary.pages_fetched} page(s) fetched from{" "}
                          <code>{summary.target_url}</code> ·{" "}
                          {summary.evidence.length} evidence item(s)
                        </p>
                        <p className="muted">
                          Execution: {summary.execution} · persistent job store:{" "}
                          {summary.job_store_available ? "available" : "not available"}
                        </p>
                        {summary.criteria_without_evidence.length > 0 ? (
                          <p className="muted">
                            No evidence found for:{" "}
                            {summary.criteria_without_evidence.join(", ")}
                          </p>
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
        <button
          type="button"
          className="primary"
          onClick={handleCompare}
          disabled={!canCompare}
        >
          {comparing ? "Comparing…" : "Compare hospitals"}
        </button>
        {compareHint ? <p className="muted">{compareHint}</p> : null}
      </div>

      {compareError ? (
        <div className="callout callout-error" role="alert" data-testid="compare-error">
          <p>{compareError.message}</p>
          {compareError instanceof ApiError && compareError.requiredIntegration ? (
            <p className="required-integration">
              Missing integration: {compareError.requiredIntegration}
            </p>
          ) : null}
        </div>
      ) : null}

      {result ? <ComparisonMatrix result={result} /> : null}
    </section>
  );
}
