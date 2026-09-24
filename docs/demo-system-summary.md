# Demo System Summary — MedCompare

**Date:** 2026-09-25
**Purpose:** a single reference for how the demo stack is composed, started, secured,
seeded, verified, and torn down. Companion documents:
`docs/live-demo-report.md` (this phase's browser walkthrough),
`docs/demo-runbook.md`, `docs/demo-validation-report.md`, `docs/system-summary.md`.

**Disclaimer:** demo hospitals, users and datasets are fictional and labelled DEMO.
Real hospital API/feed integrations require an authorized source configuration and
credentials and are not included by default. No ranking, winner, or
medical-superiority claim is produced; `overall_score` stays `null` by design.

---

## 1. Components

| Component | Location | Runs on | Notes |
|---|---|---|---|
| API / auth / RBAC | `src/hospital_extractor/api/**`, `src/hospital_extractor/auth/**` | `127.0.0.1:8000` | FastAPI, `MEDCOMPARE_REQUIRE_AUTH=1`, `mode=development` |
| React SPA | `frontend/` (Vite + TypeScript) | `localhost:5173` | dev server proxies `/api`, `/health`, `/ready` to `127.0.0.1:8000` |
| Demo seeder | `src/hospital_extractor/demo/seed.py` | CLI | writes only `data/demo/medcompare-demo.db` |
| Evidence | `demo-evidence/` | — | 36 screenshots + `browser-report.json` + read-only probes |

## 2. Starting the stack

```powershell
# backend (uses the dedicated demo DB — never data/medcompare.db)
$env:MEDCOMPARE_REQUIRE_AUTH   = "1"
$env:MEDCOMPARE_AUTH_MODE      = "development"
$env:MEDCOMPARE_CORS_ORIGINS   = "http://localhost:5173"
$env:MEDCOMPARE_DEMO_ADMIN_PASSWORD   = "<from the operator>"   # never written to disk
$env:MEDCOMPARE_DEMO_USER_PASSWORD    = "<from the operator>"
$env:MEDCOMPARE_DEMO_REVIEWER_PASSWORD= "<from the operator>"

.venv\Scripts\python.exe -m hospital_extractor.cli demo seed --reset
.venv\Scripts\python.exe -m hospital_extractor.cli serve `
    --db data\demo\medcompare-demo.db --host 127.0.0.1 --port 8000

# frontend
cd frontend; npm run dev          # http://localhost:5173
```

Credentials exist only as environment variables. The seeder fails closed
(`SeedError`) when they are missing, so no password can ever be hardcoded or logged.

## 3. Accounts, roles, grants

| Username | Role | Hospital grants | UI shell |
|---|---|---|---|
| `admin@medcompare.demo` | admin | all 4 demo hospitals | `AdminLayout` (`/admin/**`) |
| `user@medcompare.demo` | viewer | `h_demo_harborview` | `UserLayout` (`/`, `/search`, `/compare`, `/my-hospitals`, `/quality`, `/reports`, `/hospitals/:id`) |
| `reviewer@medcompare.demo` | reviewer | `h_demo_harborview`, `h_demo_lakeshore` | `UserLayout` |

Behaviour proven in the live run: anonymous → `401`; viewer on any admin API →
`403 Permission denied`; viewer navigating to `/admin/**` → redirected to `/`;
viewer opening a non-granted hospital detail → *"This hospital is not granted to your
account."*

## 4. Demo data inventory (seeded, deterministic IDs)

| Kind | Count | IDs / notes |
|---|---|---|
| Hospitals | 4 | `h_demo_northstar`, `h_demo_solstice`, `h_demo_harborview`, `h_demo_lakeshore` (labelled DEMO) |
| Datasets | 4 | one per hospital — `mock_api` (northstar 43, solstice 18 records), `website` (harborview 3, lakeshore 3) |
| Snapshots | 6 | harborview/lakeshore carry `initial` + `detected`, northstar/solstice `initial` |
| Change sets | 6 | e.g. `c_5fc71e921c91` (420 → 440 beds, `https://demo-harborview.example.test/`) |
| Verification jobs | 2 | `v_demo_harborview`, `v_demo_lakeshore`, both `mismatch` |
| Reconciliation cases | 2 | `rec_b437b02ed971`, `rec_39501914dbbd`, both `open` |
| Reports | 1 | `rpt_3b40b173971c` → links case → verification result → change set → 3 snapshots |
| Monitoring policies | 3 | e.g. `mp_b75c21760567` (`daily`, `mock_api`, `pending`) |
| Users | 3 | table above |

Per-hospital drill-downs are intentionally scoped: verification/reconciliation data
exists for `h_demo_harborview` and `h_demo_lakeshore`, a report for
`h_demo_harborview`; other hospitals show the empty state. The admin pages expose a
hospital `<select>` (`verification-hospital`, `recon-hospital`, `reports-hospital`)
and the demo walks every option.

## 5. Source types and connectors

| Source | Status badge | Connector | Probe |
|---|---|---|---|
| `website` | PUBLIC | `WebsiteDataSource` | offline, `200`, `fetch_allowed=false` |
| `json_feed` | PUBLIC | `JSONFeedDataSource` | offline, `200`, `fetch_allowed=false` |
| `mock_api` | **SIMULATED / MOCK** | `MockHospitalAPIDataSource` | offline, `200`, `fetch_allowed=false` |
| `hospital_api` | **UNAUTHORIZED** | `HospitalAPIDataSource` | offline, **`401`**, `fetch_allowed=false` |

Every probe response carries `credential_ref: null` and the note
*"offline probe only — no external request is made"*, so an unauthorized connector can
be demonstrated without any network egress or credential exposure.

## 6. API surface exercised by the demo

| Purpose | Endpoint |
|---|---|
| Auth | `POST /api/v1/auth/login`, `GET /api/v1/auth/me`, logout |
| Registry | `GET /api/v1/hospitals/registry`, `GET /api/v1/hospitals` |
| Search / compare | `POST /api/v1/search`, `POST /api/v1/search/compare` |
| Hospital detail | `GET /api/v1/hospitals/{id}/snapshots`, `/changes`, `GET /api/v1/quality/hospitals/{id}` |
| Admin | `/api/v1/users`, `/api/v1/audit`, `/api/v1/sources`, `/api/v1/sources/{type}/test`, `/api/v1/hospitals/{id}/monitoring-policies`, `/verification-jobs`, `/reconciliation-cases`, `/reports`, `/api/v1/quality/pipeline` |
| Ops | `GET /health`, `GET /ready` |

Response shapes the UI depends on:

* **Search match** — `dataset_id`, `hospital_id`, `hospital_name`, `source_type`,
  `provider`, `captured_at`, `record_count`, `fields[] {field, value, unknown, evidence[]}`,
  `evidence[] {source_url, source_type, page_title, captured_at, confidence, extraction_method, source_strength, context}`.
* **Comparison** — `hospital_ids[]`, `hospital_names{}`, `note`,
  `fields[] {field, label, cells[] {hospital_id, hospital_name, value, unknown, source_type, captured_at, evidence[]}}`,
  `compared_at`. The UI normalises this (and the dimension-shaped
  `hospitals[].dimensions[].decision` variant) in `normalizeCompare()`.

## 7. Safety rules enforced during the demo

1. Demo DB only — `data/medcompare.db` is never the demo target.
2. Passwords only via `MEDCOMPARE_DEMO_*_PASSWORD`; never written to a file, report or screenshot.
3. Tokens held in browser `sessionStorage` only; no token file is ever created.
4. Probes are offline — no external HTTP request is made by connector tests.
5. Nothing committed or pushed; working tree left as found.
6. Evidence is scanned for secrets before being considered complete.

## 8. Quality baselines (unchanged by this phase)

| Check | Baseline | Command |
|---|---|---|
| Backend tests | **912 passed** | `.venv\Scripts\python.exe -m pytest -q` |
| Lint | **0** | `.venv\Scripts\python.exe -m ruff check src tests` |
| Types | **0 / 179 files** | `.venv\Scripts\python.exe -m mypy src` |
| Frontend types | **0** | `npm run typecheck` |
| Frontend lint | **0** | `npx eslint src --max-warnings 0` |
| Frontend tests | **29 passed** | `npx vitest run` |
| Frontend build | **ok** | `npm run build` |

## 9. Evidence layout

```
demo-evidence/
  screenshots/          36 PNG (01…20) from the final run against the fixed UI
  browser-report.json   machine-readable walkthrough: page captures, per-hospital
                        drill-downs, API status log, 401/403 matrix
  api_probe.py          read-only API probe (credentials from env only)
  api_introspect.py     read-only model/introspection probe
  frontend.out.log      Vite dev-server output
  frontend.err.log      Vite dev-server errors
```

`demo-evidence/` is generated locally and is **gitignored** — screenshots, probe
scripts and dev-server logs are session artifacts, not project source. Nothing
inside it is committed.

Browser automation (`browser-demo.js` + `playwright-core`) lives in the OS temp
directory, deliberately outside the repository, so the project gains no new
dependency.
