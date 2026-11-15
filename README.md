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
  interpretation, synchronous website extraction, and consistency audits.
- `src/hospital_extractor/persistence/` - PostgreSQL registry and crawl-history
  persistence (SQLAlchemy 2.x + Alembic).
- `src/hospital_extractor/consistency/` - deterministic engine comparing one stored
  facility record with one persisted website snapshot.
- `src/hospital_extractor/crawler/` and `extraction/` - SSRF-protected crawling and
  evidence extraction; missing data remains `UNKNOWN`, never a negative claim.

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
