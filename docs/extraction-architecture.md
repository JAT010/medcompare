# Extraction Architecture — V1

Deterministic-first, source-aware, shallow-in-depth, safe to operate. No databases, no LLMs, no
message brokers, no containers. A local Python 3.13 + `uv` application.

## 1. Pipeline

```
URL validation
      │
      ▼
Crawler ────────────────► Page discovery (sitemaps, robots, internal links)
      │                            │
      │              ┌─────────────┼─────────────┐
      ▼              ▼             ▼             ▼
 raw pages      sitemap        URL seeds     robots policy
      │
      ▼
Page classification (HIGH / MEDIUM / LOW / IRRELEVANT)
      │
      ▼
Content extraction (JSON-LD → semantic HTML → tables → cards → headings → text)
      │
      ▼
Entity extraction (hospital-scoped, shallow)
      │
      ▼
Canonical mapping (schema/mapper.py)
      │
      ▼
Normalization (cleaning, whitespace, phones/URLs, numeric parse)
      │
      ▼
Deduplication (URLs + conservative entity matching)
      │
      ▼
Validation + conflict detection (found / not_found / unclear / conflicting)
      │
      ▼
Dynamic schema (only meaningful categories & fields)
      │
      ▼
Export: intermediate JSON ──► Excel (.xlsx) / CSV
```

## 2. Component Map (existing scaffold, now filled in)

| Module | Responsibility |
|---|---|
| `utils/urls.py` | Validation, normalization, domain scoping, asset filtering (done) |
| `utils/text.py` | Cleaning: whitespace, HTML artifacts, unicode spaces, sequential dedup (done) |
| `utils/hashing.py`, `utils/logging.py` | Stable IDs; structured logging (done) |
| `models/**` | Pydantic entities + `HospitalDataset` container (done) |
| `crawler/discovery.py` | Sitemap index → sitemaps; robots.txt parse; URL seeds; internal-link extraction |
| `crawler/url_filter.py` | Same-domain, robots-aware, path-based, asset/booking exclusion |
| `crawler/crawler.py` | Polite async fetch (httpx): retries, timeouts, delay, limits, graceful stop |
| `crawler/browser.py` | Playwright facade — **opt-in**, used only when HTTP is insufficient |
| `storage/raw.py` | Raw HTML cache under `data/raw/<run>/<hash>.html` |
| `extraction/page_analyzer.py` | Title, headings, breadcrumbs, JSON-LD block extraction, section detection |
| `extraction/jsonld.py` | Normalize schema.org `@type`s into generic shapes |
| `extraction/hospital.py` | Hospital overview + capacity from JSON-LD + About Us |
| `extraction/contact.py` | Address/phones/geo from JSON-LD (`LocalBusiness`, `hospital`) |
| `extraction/doctors.py` | Listing cards → Doctor records |
| `extraction/specialities.py`, `departments.py` | H1/heading + card signals |
| `extraction/treatments.py`, `procedures.py`, `technology.py` | Category pages |
| `extraction/facilities.py`, `services.py` | FAQ + section signals |
| `processing/normalize.py` | Value cleaning, numeric parsing, phone/URL normalization |
| `processing/deduplicate.py` | URL dedup + conservative entity matching |
| `processing/validate.py` | Tri-state, conflict detection, quality flags |
| `schema/canonical.py` | Canonical category/field definitions |
| `schema/discovery.py` | Which categories/fields have meaningful data |
| `schema/mapper.py` | website field → canonical field with confidence + fallback |
| `storage/json_store.py` | Persist `HospitalDataset` as JSON (the primary internal model) |
| `export/excel.py`, `export/csv.py` | Dynamic workbooks / CSV mirrors |
| `config.py` | Settings via pydantic-settings + `config/default.toml` |
| `cli.py` | Entry point: `uv run hospital-extractor <URL> [--max-pages N] [--timeout S] [--output-dir DIR] [--level LEVEL]` |

## 3. Interface Contracts

- **Discovery → Crawler**: `list[str]` of normalized seed URLs (from sitemaps + internal links), plus a
  robots policy object.
- **Crawler → Storage**: `(normalized_url, status_code, html_bytes, final_url)` for each fetched page.
- **Storage → Extraction**: parsed page object `(url, title, soup, jsonld_blocks, headings, sections)`.
- **Extraction → Models**: typed Pydantic records (always carrying provenance).
- **Mapper → Dataset**: `HospitalDataset` (list of records per canonical category).
- **Dataset → Export**: `HospitalDataset.model_dump_json()` for JSON; exporter iterates categories →
  discovers non-empty fields → writes worksheets.

## 4. Key Behaviors

### 4.1 Single-hospital scope
A run accepts one hospital URL. The hospital slug is derived from the input URL. Discovery prioritizes
hospital-slug pages, then city-scoped pages (`/doctor/*-in-<location>`), then whitelisted network pages
(contact, awards filtered by hospital name). Everything else is depth-capped.

### 4.2 Extraction strategy ladder (per page)
1. JSON-LD (`LocalBusiness`, `hospital`, `Physician`, `@graph`)
2. Semantic HTML (H1/H2, `div.site-content`, card classes, accordion Q&A)
3. Tables
4. Lists / cards
5. Headings + section text
6. Visible-text patterns (numeric capacity patterns)
7. Rendered browser content — only if a `RendererRequired` signal fires (flag-driven)

The ladder guarantees the same pipeline behaves differently for a JSON-LD-rich hospital page vs. a
plain-HTML page on another site.

### 4.3 Provenance
Every record stores `source_url`, `page_title`, `extracted_at` (UTC), `context` snippet. The exporter
generates a `Sources` worksheet from these. Data Quality worksheet reports pages discovered/crawled/
skipped/failed, records extracted, duplicates removed, conflicts, categories with no usable data,
warnings.

### 4.4 Conflict & missing data
- Conflicting values are preserved with their sources and flagged `conflicting` — never silently merged.
- Missing = `None` (not_found). `unclear` = ambiguous signal. `found` = extracted with its confidence.
- Empty categories are dropped from output (no blank worksheets).

## 5. Dynamic Schema & Export

`schema/discovery.py` computes, per category, the set of non-null fields. Excel layout:

- One worksheet per non-empty category (dynamic).
- Header row: canonical field names actually present.
- Frozen header row, autofilters, wrapped text, sensible column widths, consistent styling, `Sources`
  and `Data Quality` sheets appended last.
- File name: `<Hospital_Name>.xlsx` in the output dir.

## 6. Risks & Mitigations (V1-safe)

| Risk | Mitigation |
|---|---|
| JS-rendered content | Server-rendered for the target site; Playwright behind flag, never automatic |
| Pagination | Not present on target listings; discovery treats `rel=next`/`?page=` generically if seen |
| Duplicate URLs | Normalize + dedupe pre/post fetch; `is_same_page` ignores tracking params |
| Boilerplate | `utils/text.py` sequential dedup; template-FAQ answer drop; JSON-LD precedence |
| Inconsistent cards | Per-card independent extraction; missing fields → `None`; partial records kept |
| Conflicting values | Preserve + flag; two Saket hospitals never merged (slug is authority) |
| Missing data | Tri-state; dynamic schema; no fabrication |
| Robots / rate limits | Robots-aware filter; configurable delay/retry/limits; graceful Ctrl-C |
| Very large sites | Hospital-scoped default; catalogs opt-in; page/depth limits |
| Akamai 403 | Configurable UA; probe before crawl; clear error, no retry storm |

## 7. Testing Strategy

- **Fixtures** (primary): canned HTML for hospital profile, doctor listing, doctor profile, speciality,
  contact, awards, robots.txt, sitemap index, malformed/empty pages. No live site in unit tests.
- Unit tests: URL validation/normalization, domain restriction, robots filtering, duplicate URLs, HTML
  parsing, JSON-LD parsing, page classification, entity extraction, canonical mapping, normalization,
  deduplication, conflict detection, dynamic schema, Excel generation, empty/missing fields, HTTP errors.
- **Integration** (opt-in, `@pytest.mark.integration`, tolerant assertions): live run against Max Smart
  Saket with a tiny page budget.

## 8. Implementation Order (incremental, per §25 of the master prompt)

1. ✅ `utils` + `models` + toolchain (done, 85 tests green)
2. ✅ `crawler/discovery` + `url_filter` (sitemap/robots parsing, seeds) — fixture tests
3. ✅ `crawler/crawler` + `storage/raw` (polite fetch, retry, cache) — fixture + mock transport tests
4. ✅ `extraction/page_analyzer` + `jsonld` (page signals, structured data)
5. ✅ Entity extractors (hospital → doctors → specialities → procedures → technology → services)
6. ✅ `processing/*` (normalize, dedupe, validate)
7. ✅ `schema/*` (canonical, discovery, mapper)
8. ✅ `export/*` (JSON store, Excel, CSV)
9. ✅ `config` + `cli` wiring
10. ✅ Live integration test (Max Smart Saket) + cross-site smoke test — see `docs/recon-maxhealthcare-saket.md`
