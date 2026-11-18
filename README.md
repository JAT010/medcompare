# hospital-website-extractor

Universal hospital website intelligence extractor. Crawls a hospital site, extracts
structured records, and projects them onto a 25-field master intake form (CSV/XLSX).

## MedCompare full-stack application

On top of the extraction engine, this repository runs MedCompare: an evidence-based
hospital comparison and database-to-website consistency auditing application.

- `frontend/` - React 19 + TypeScript + Vite UI: capability report, natural-language
  criterion interpretation, 2-to-6 facility selection, side-by-side evidence matrix,
  consistency audit panel, and an explicitly labelled offline demonstration mode.
- `src/hospital_extractor/api/` - FastAPI service for facilities, comparison,
  interpretation, synchronous website extraction, consistency audits, and facility-scoped
  hospital integration/verification/monitoring/reconciliation endpoints.
- `src/hospital_extractor/persistence/` - PostgreSQL registry, crawl-history and
  integration/verification persistence (SQLAlchemy 2.x + Alembic; migrations are
  append-only and never edited).
- `src/hospital_extractor/consistency/` - deterministic engine comparing one stored
  facility record with one persisted website snapshot.
- `src/hospital_extractor/crawler/` and `extraction/` - SSRF-protected crawling and
  evidence extraction; missing data remains `UNKNOWN`, never a negative claim.
- `src/hospital_extractor/sources/` - authorized source connectors: the fail-closed
  authorization policy (SSRF guard, allowlist, env-backed credentials), the authorized
  hospital REST/JSON connector, and a clearly-labelled simulated connector for tests/demos.
- `src/hospital_extractor/snapshots/` - versioned, immutable, fingerprinted snapshots and
  deterministic field-level change detection.
- `src/hospital_extractor/jobs/` - persistent verification job queue (PostgreSQL
  `FOR UPDATE SKIP LOCKED` claiming, leases, bounded retries, idempotent enqueue).
- `src/hospital_extractor/verification/` - deterministic comparison of detected hospital
  changes against website evidence (`match` / `mismatch` / `insufficient_evidence`;
  absence of evidence is never a mismatch) with full provenance.
- `src/hospital_extractor/monitoring/` - scheduler that runs periodic captures and creates
  verification jobs (never crawls websites itself); every decision is logged immutably.
- `src/hospital_extractor/reconciliation/` - human-review cases for inconclusive or
  contradicted verifications with an append-only audit trail.
- `src/hospital_extractor/worker/` - separately executable verification worker
  (`python -m hospital_extractor.worker`); normal web requests never claim jobs.

Every finding carries provenance (source, URL, retrieval time, confidence where
available). No winner, clinical quality score or ranking is produced. Missing
information means "not found in that source", not proof of absence.

### Local setup

```powershell
# 1. Backend dependencies and .env (use POSTGRES_PORT=5433 to avoid host conflicts)
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip install pytest pytest-cov ruff mypy pandas-stubs types-openpyxl
Copy-Item .env.example .env

# 2. Frontend dependencies
Set-Location frontend
npm install
Set-Location ..

# 3. PostgreSQL, schema, seed data
docker-compose up -d postgres
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe scripts\seed_facilities.py
```

### Run

```powershell
# Terminal 1 - API on http://127.0.0.1:8000
.\.venv\Scripts\python.exe -m uvicorn hospital_extractor.api.main:create_app --factory --host 127.0.0.1 --port 8000 --reload

# Terminal 2 - UI on http://localhost:5173
Set-Location frontend
npm run dev
```

### Key endpoints

- `GET /api/v1/health`
- `GET /api/v1/comparison/capabilities`
- `POST /api/v1/comparison/interpret`
- `GET /api/v1/facilities`
- `POST /api/v1/comparison`
- `POST /api/v1/crawl/extract`
- `GET /api/v1/crawl/runs/{crawl_run_id}`
- `POST /api/v1/consistency/audit`
- `GET /api/v1/consistency/audit/{facility_id}`
- Facility-scoped integration endpoints (all under `/api/v1/facilities/{facility_id}/...`):
  `integrations` (list/create/update/capture — capture and writes gated by the operator
  token), `snapshots`, `change-sets`, `verification-jobs` (list/detail/manual create/cancel),
  `monitoring/schedules`, `monitoring/executions`, `reconciliation-cases` (list/detail/
  manual create/transitions).

### Hospital integration (authorized sources, simulated by default)

The hospital integration subsystem is facility-scoped and fail-closed. It ships with:

- a deterministic **simulated** connector (`mock_hospital_api_simulated`) used by the
  test suite and demos. It never performs network I/O and is always labelled SIMULATED;
- a live authorized REST/JSON connector (`hospital_rest_json`) that only works when an
  integration is explicitly enabled with an allowlisted `base_url`/`allowed_hosts`,
  environment-backed credentials (`credential_env_ref` is a variable *name*, never the
  secret), and it still never follows redirects, bounds response size and timeouts, and
  refuses always-blocked hosts (link-local/metadata/multicast/unspecified).

What happens when a source is captured:

1. The payload is fingerprinted into an immutable, versioned snapshot.
2. A deterministic diff against the previous snapshot yields field-level changes
   (`added`/`removed`/`modified`), persisted with provenance.
3. If changes are detected, a verification job is enqueued against the facility's website.
4. A separately executable worker (`python -m hospital_extractor.worker` or
   `medcompare-worker`) claims the job (`FOR UPDATE SKIP LOCKED` on PostgreSQL), crawls
   the website with SSRF guards, and compares. A crawl failure requeues the job with
   backoff (bounded by `max_attempts`); it is never recorded as a verification verdict.
   Outcomes are `match`, `mismatch` or `insufficient_evidence` — absence of website
   evidence is never a mismatch nor a verified match; every triggering change must be
   evaluated for a verified verdict. Each sweep also runs due monitoring schedules
   (disable with `--no-monitoring`).
5. Inconclusive/contradicted results open reconciliation cases with an append-only audit
   trail; resolution records a decision about the evidence and publishes nothing to the
   hospital.

Operator operations (create/configure/enable integrations, captures, controlling jobs,
monitoring management, reconciliation transitions) require the shared operator token:

```powershell
# .env (never commit a real value)
MEDCOMPARE_OPERATOR_API_KEY=choose-a-long-random-value

# UI: enter the same token in the "Operator token" field, or send it as
#   Authorization: Bearer $env:MEDCOMPARE_OPERATOR_API_KEY
```

When `MEDCOMPARE_OPERATOR_API_KEY` is unset, all operator endpoints are closed with
`503 operator_api_key_not_configured`. Read-only lists stay open, matching the rest of the
API. Monitoring schedules run the capture pipeline on a timer and enqueue verification
jobs; they never crawl websites directly.

Simulated end-to-end flow (no real hospital credentials needed):

```powershell
# 1. Register a facility with a website (POST /api/v1/facilities is protected by
#    the existing facility-creation flow in scripts/seed_facilities.py)
# 2. Set MEDCOMPARE_OPERATOR_API_KEY, then POST .../integrations with
#    connector_type=mock_hospital_api_simulated, config_json={payload_variant: default}
# 3. POST .../integrations/{id}/capture twice; after switching payload_variant to
#    "changed" the capture detects changes and enqueues a verification job
# 4. python -m hospital_extractor.worker --once
# 5. Inspect .../verification-jobs/{job_id} for the result and provenance
```

The worker uses the real SSRF-protected crawl pipeline; for deterministic offline tests the
crawl is injected (see `tests/worker/test_worker_end_to_end.py`).

The consistency audit compares the stored MedCompare registry record with the latest
usable persisted website extraction for the same physical facility. It reports
consistent, differing, missing and potentially outdated fields. The registry is not a
live connection to a hospital's own database; verifying service fields against that
system requires an authorized hospital-server integration, which is reported as
unavailable until configured. Public internet research likewise returns `501` until a
server-side search provider is configured.

### Verification

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_intake.py tests/comparison/ tests/persistence/test_registry_sqlite.py -v
Set-Location frontend
npm test
npm run lint
npm run build
```

Or run the full backend suite and static checks:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

For the original extractor CLI, continue below.

## Quick start

```bash
# install
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"

# crawl a hospital and export
.venv\Scripts\python main.py https://www.example-hospital.in --export
```

`--export` writes the dataset JSON plus the master intake files
(`master_hospital_intake.csv`, `master_hospital_intake.xlsx`) and the
quality/provenance report into `data/`.

## What it extracts

Eleven canonical categories feed the 25-field intake:

- identity & contacts: hospital, contacts, infrastructure
- services: facilities, departments, specialities, doctors
- clinical: treatments, procedures, technologies
- credentials: accreditations

The intake (see `schema/intake.py`) is the single output contract — one row per
hospital with text/number/dropdown/boolean fields, booleans resolved to
yes/no/unknown via weighted evidence. UNKNOWN values are preserved in CSV/XLSX
by default; legacy consumers that require TRUE/FALSE can opt in explicitly with
`--boolean-unknown-policy false` or `boolean_unknown_policy = "false"` in
`config/default.toml`.

## Architecture

- `crawler/` — fetch, classify (URL + content signals), crawl loop
- `extraction/` — per-category extractors plus `engine.build_dataset`
- `processing/` — normalize, deduplicate, validate, conflict detection
- `schema/` — canonical categories, field mapping, intake schema, quality
- `export/` — master CSV/XLSX intake outputs + quality/provenance workbook
- `models/` — pydantic records, dataset container, intake model

## Development

```bash
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m mypy src
```
