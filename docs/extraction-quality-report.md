# Prompt 6 & 7 — Extraction Quality & Dataset Finalization Report

Date: 2026-08-11 · Project: Universal Hospital Website Extractor
Scope: extraction quality (conflicts, provenance, confidence, coverage, warnings,
missing-data) and dataset finalization (dynamic schema, deterministic JSON,
extraction summary, live integration validation).

---

## 1. Summary

The pipeline now produces a **self-auditing dataset**: every value carries
provenance (source URL, page URL, extraction method, strength, confidence),
disagreements between sources are surfaced as conflicts that are either
resolved by evidence authority or preserved as `unresolved` (never silently
picked), and each of the 18 canonical categories is reported with an explicit
coverage state (`found` / `not_found` / `failed` / `not_applicable`) plus a
per-category missing-field analysis. JSON serialization is deterministic and
the extraction summary is printed by the CLI.

**All 290 unit tests pass; ruff and mypy (strict, 86 source files) are clean.
3 opt-in live integration tests pass against maxhealthcare.in (Max Smart
Super Speciality Hospital, Saket).**

## 2. The 18 Canonical Categories

All 18 categories from `docs/canonical-schema.md` §1–§18 are registered in
`schema/canonical.py` (`CANONICAL_CATEGORIES`) with key, worksheet label,
model, single-record flag and required fields. Verified properties:

| Property | Status |
|---|---|
| 18 categories registered, order matches §1–§18 | ✓ `canonical.py:54` |
| Dynamic dataset rule (categories appear only with records) | ✓ `schema/discovery.py` |
| Shallow entity boundary (no nested entities; Doctor has no deep bio fields) | ✓ tested |
| No fabricated values (numbers parsed only from explicit patterns) | ✓ `extraction/hospital.py` |
| `not_applicable` supported but never auto-inferred | ✓ `engine._category_coverage` |
| `required_fields=()` for name-less categories (contact/infrastructure/visiting) | ✓ `canonical.py:62` |

### Live coverage on the Max Smart Saket profile page (single-page run)

| Category | State |
|---|---|
| Hospital Overview | found — "Max Smart Super Speciality Hospital, Saket", type `tertiary care` |
| Location & Contact | found — phone `+91 926 888 0303`, address `12, Press Enclave Marg, Saket` |
| Capacity & Infrastructure | found — `total_beds = 650` |
| Facilities … Other Hospital Information | not_found (page does not publish them) |

## 3. Field-Level Provenance (P6)

Every record (base `EntityRecord`) carries: `source_url`, `page_url` (explicit
field, equals `source_url` in the current pipeline), `page_title`,
`extraction_method`, `source_strength`, `confidence`, `context`, `extracted_at`.

- Confidence is **evidence-derived, deterministic, never a probability**:
  `jsonld` 0.9 → `semantic_html` 0.8 → `card/table/faq/section` 0.7 →
  `list/heading` 0.6 → `text` 0.4, with a strength delta
  (`extraction/helpers.py` `confidence_for`).
- `provenance()` fills confidence by default, so **every** record gets a
  confidence without extractor changes.

## 4. Conflict Detection & Resolution (P6)

- New `Conflict` model (`models/conflict.py`) with `conflict_type`
  (capacity / phone / address / name / year / organization / value),
  `values`, `preferred_value`, `sources` (url + method + strength),
  `resolution_status`, and backward-compatible `status`/`source_urls`.
- Single-record categories merge candidates and flag disagreements
  (`processing/conflicts.py`); list categories do the same during
  deduplication (`processing/deduplicate.py`).
- **Resolution is conservative**: `apply_resolution` promotes a value only when
  the source is *strictly* more authoritative
  (jsonld 3 > semantic_html/card/table/faq/section 2 > list/heading 1 > text 0);
  equal authority stays `unresolved` and the first value is kept. The promoted
  value is the **raw typed value** (fixed bug: stringified "700" → int 700).
- **Provenance fields never create conflicts** (fixed: differing
  `source_url`/`page_url`/`context`/method/strength/confidence between pages
  are source metadata, not data disagreements).
- Unresolved conflicts are surfaced as warnings; resolved/unresolved counts are
  available as `DataQuality.conflicts_resolved` / `.conflicts_unresolved`.

## 5. Coverage, Warnings & Missing Data (P6)

- `DataQuality` now records crawl counters (`pages_crawled/classified/parsed`
  fed from `CrawlStats`), `category_coverage` (label → state),
  `missing_fields` (label → absent schema fields, provenance excluded), and
  `warnings` (capped at 50, never abort the pipeline).
- Coverage states: `found` (records exist), `failed` (attempted or produced
  candidates but nothing survived; also content-with-no-records pages),
  `not_found` (site does not publish), `not_applicable` (never inferred).
- Missing-data analysis (`schema/missing.py`) reports only schema fields of
  categories that have records; category-level absence is carried by the
  coverage state.
- Warnings cover: malformed JSON-LD, content-with-no-records pages, duplicates
  removed, uncertain doctor associations, unresolved conflicts, crawl failures.

## 6. Deterministic JSON & Extraction Summary (P7)

- `storage/json_store.py` serializes with `sort_keys=True`; the same dataset
  always yields identical bytes (tested; `extracted_at` is per-run by design).
- The full `quality` block (coverage, missing fields, conflicts, warnings,
  methods, counters) is part of the JSON document.
- `format_dataset_summary` and the coverage report now show category coverage
  states and missing fields; the CLI `--export` flow wires crawl stats through
  `CrawlStats` → `DataQuality`.

## 7. Integration Validation (P7)

Three live tests (`tests/test_integration.py`, `@pytest.mark.integration`) run
against `maxhealthcare.in` and pass:

1. Smart Saket profile → hospital name, `total_beds == 650`, contact extracted,
   quality non-empty.
2. The two Saket hospitals are **not merged** (input URL is the source of truth;
   the two names differ).
3. One profile page → zero self-conflicts, full provenance on every record.

Single-page run numbers: 3 records (1 overview, 1 contact, 1 infrastructure),
extraction methods `jsonld:2, text:1`, 0 conflicts, 0 duplicates, 0 warnings,
1 low-confidence record (About-text capacity).

## 8. Tests

| Area | File | Count |
|---|---|---|
| Conflict detection/resolution, dedup, confidence, provenance | `tests/test_processing.py` | 19 |
| Dynamic schema, shallow entities, deterministic JSON, coverage states, missing fields, summary, CrawlStats | `tests/test_schema.py` | 20 |
| Live Max Healthcare integration (opt-in) | `tests/test_integration.py` | 3 |
| Pre-existing suite | — | 248 |
| **Total** | | **290 unit + 3 integration** |

Commands: `uv run pytest -q`, `uv run pytest -m integration`,
`uv run ruff check .`, `uv run mypy src`.

## 9. Remaining Limitations

- Network/single-hospital default scope: procedures/conditions/technology
  network catalogs and doctor profile pages are opt-in (see
  `docs/recon-maxhealthcare-saket.md` §8).
- `not_applicable` is currently never assigned; wiring it to explicit
  per-target evidence is future work.
- The site's anti-bot behavior varies (Akamai); the crawler's configurable UA +
  probe-first flow is the mitigation, not a guarantee.
- `original_value/normalized_value` provenance is captured at the conflict level
  (values + sources); per-field dual columns were intentionally not added.

## 10. Export Readiness

The dataset, its quality metadata and the extraction summary are complete and
deterministic. The next task — building the **Data Quality worksheet** and the
conflicts/coverage/missing-field worksheets from `DataQuality` (Excel/CSV) —
can proceed without further pipeline changes.
