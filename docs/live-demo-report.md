# Live Demo Report — Browser-Level ADMIN/USER Walkthrough

**Date:** 2026-09-25
**Environment:** Windows 11, Python 3.13.15 (`.venv`), Node v24.13.0, npm 11.6.2
**Browser:** system Google Chrome 154.0.8037.57 driven by `playwright-core` 1.63.0
(script kept outside the repo in the OS temp dir, so no new runtime dependency was added to the project).
**Scope:** start the running demo stack, drive real browser sessions as ADMIN and USER,
verify role-differentiated React UIs, run a hospital search and a multi-hospital comparison,
validate 401/403 behaviour, capture screenshots, re-run every test suite, and document the result.

**Baselines after this phase (all preserved):**

| Suite | Result |
|---|---|
| Backend `pytest -q` | **912 passed** (115 s) |
| Backend `ruff check src tests` | **0** |
| Backend `mypy src` | **0 issues / 179 files** |
| Frontend `tsc -b` | **0** |
| Frontend `eslint src --max-warnings 0` | **0** |
| Frontend `vitest run` | **29 passed** (6 files) |
| Frontend `vite build` | **ok** (315.89 kB / 95.04 kB gzip) |

**Disclaimer:** real hospital API/feed integrations require an authorized source
configuration and credentials and are not included by default. Demo hospitals, users
and datasets are fictional and labelled DEMO. No ranking, winner, or
medical-superiority language is produced anywhere in the UI; `overall_score` is always
`null` by design. No password or token appears in any file, screenshot, or report below.

---

## A. Running environment

| # | Item | Status | Evidence |
|---|---|---|---|
| A1 | Backend live on `http://127.0.0.1:8000` (`serve`, `require_auth=True`, `mode=development`) | PASS | `GET /health` → `200 {"status":"ok"}`; `GET /ready` → `200` with `config/database/templates/static` all `ok` |
| A2 | Frontend live on `http://localhost:5173` (Vite dev server, proxies `/api`, `/health`, `/ready`) | PASS | `GET /` → `200` |
| A3 | Demo DB `data/demo/medcompare-demo.db`; real store `data/medcompare.db` untouched | PASS | server started with `--db data\demo\medcompare-demo.db` |
| A4 | CORS limited to `http://localhost:5173` | PASS | server env `MEDCOMPARE_CORS_ORIGINS` |
| A5 | Demo credentials supplied only through `MEDCOMPARE_DEMO_*_PASSWORD` env vars | PASS | secret scan (section G) found none of them in the repo |

## B. ADMIN browser session (screenshots `01`–`15`)

| # | Check | Status | Evidence |
|---|---|---|---|
| B1 | Login page renders, submits, never echoes the password | PASS | `01-login-page.png` |
| B2 | Admin enters the **AdminLayout** via the header `Admin` link | PASS | `entered_admin_via = header Admin link`; `MedCompare Admin`; `admin@medcompare.demo (admin)` |
| B3 | Admin nav exposes every admin destination | PASS | dashboard, hospitals, sources, monitoring, verification, reconciliation, reports, quality, audit, users, health (`03-admin-*.png`) |
| B4 | Dashboard shows stored counts **and** the new "Operational status" cards | PASS | `Hospitals 4 / Datasets 4 / Users 3 / Health ok`; `Monitoring policies 3 (pending=3)`, `Change sets 6`, `Snapshots 6`, `Reports 1`, `Verification jobs 2 (mismatch=2)`, `Reconciliation cases 2 (open=2)`, `Authorization states SIMULATED / MOCK=2, UNKNOWN=2` |
| B5 | Dashboard lists sources & connectors + recent audit activity | PASS | `02-admin-dashboard.png` |
| B6 | Hospitals page: registry of 4 DEMO hospitals + per-hospital snapshots/changes drill-down | PASS | `06-admin-hospital-snapshots-changes.png`, `snapshots-panel` captured |
| B7 | Sources page shows status per source type | PASS | `website` PUBLIC, `json_feed` PUBLIC, `mock_api` **SIMULATED / MOCK**, `hospital_api` **UNAUTHORIZED** (`04-admin-sources-connector-test.png`) |
| B8 | Offline connector probes make **no** external request and expose no credentials | PASS | `GET /api/v1/sources/{t}/test` → `website/json_feed/mock_api` `200`, `hospital_api` **`401`**; every probe returns `fetch_allowed=false`, `credential_ref=null`, note *"offline probe only — no external request is made"* |
| B9 | Monitoring policies show full schedule/health detail | PASS | `mp_b75c21760567 daily mock_api pending`; schedule `daily (UTC)`, target, next run, last run `never`, last failure `none`, consecutive failures `0`, orchestration run `not run yet` (`05-admin-monitoring.png`) |
| B10 | Verification drill-down (hospital selector) shows real seeded jobs | PASS | `h_demo_harborview` → `v_demo_harborview mismatch target=website — result mismatch / DEMO mismatch for teacher walkthrough`; `h_demo_lakeshore` → `v_demo_lakeshore mismatch`; hospitals without jobs render the empty state (`07-admin-verification.png`) |
| B11 | Reconciliation drill-down shows real seeded cases | PASS | `rec_b437b02ed971 open Start review` (harborview), `rec_39501914dbbd open` (lakeshore) (`08-admin-reconciliation.png`) |
| B12 | Reports list + detail view with the full evidence chain | PASS | `rpt_3b40b173971c reconciliation_report generated` → detail links `reconciliation_case_id → verification_result_id → change_set_id`, plus verification job/result and 3 snapshot references (`09-admin-reports.png`) |
| B13 | Quality page keeps `overall_score` null | PASS | `overall_score: null`; "Metrics are independent dimensions. No composite or overall quality score is computed or implied." (`10-admin-quality.png`) |
| B14 | Audit table shows actor, outcome, resource **and correlation id** | PASS | `auth.login u_demo_admin (admin) success user / u_demo_admin req_45f57b8ab8a94d9e` (`11-admin-audit.png`) |
| B15 | Users page lists roles + hospital grants | PASS | admin → 4 grants, reviewer → 2, viewer → `h_demo_harborview` (`12-admin-users.png`) |
| B16 | Health page renders raw `/api/v1/health` and `/ready` payloads | PASS | `13-admin-health.png` |
| B17 | Logout returns to the login page and clears the session | PASS | `landed_on http://localhost:5173/login`, `login_page_visible = true` (`14-after-logout-login-page.png`) |
| B18 | Anonymous browser hitting `/admin/users` is redirected to login | PASS | `redirected_to_login = true`, no admin layout, no users table (`15-anon-admin-route-blocked.png`) |

## C. USER browser session (screenshots `16`–`20`)

| # | Check | Status | Evidence |
|---|---|---|---|
| C1 | Viewer lands in the **UserLayout**, not the admin shell | PASS | `admin=false, user=true`; nav `Home · Search · Compare · My hospitals · Quality · Reports`; role text `Role: viewer`; no `Admin` header link (`16-user-dashboard.png`) |
| C2 | User pages mount and call only their own APIs | PASS | `17-user-home/hospitals/quality/reports.png` |
| C3 | Hospital detail renders for a **granted** hospital | PASS | `/hospitals/h_demo_harborview`: snapshots table (2 website captures, `detected` + `initial`), freshness line, evidence list showing `420-bed → 440-bed` with source URL and change set `c_5fc71e921c91` (`17-user-hospital-detail.png`) |
| C4 | Hospital detail refuses a **non-granted** hospital | PASS | `/hospitals/h_demo_northstar` → *"This hospital is not granted to your account."*, snapshots table absent (`17b-user-hospital-detail-not-granted.png`) |
| C5 | **Search** `cardiology` returns real backend data with provenance | PASS | `Total matches: 2` (Northstar, Solstice); each card shows `source: mock_api`, `id`, `captured`, `records: 43 / 18`; provenance line `http://127.0.0.1:8001/demo/hospital/demo-northstar#/hospital · mock_api · captured … · api-json · confidence 1.00` (`18-user-search.png`) |
| C6 | **Compare** 4 hospitals → side-by-side table with real field values | PASS | 14 field rows × 4 hospital columns: `Hospital name, Hospital type, Established, Bed count, Doctor count, City, State, Phone, Email, Total beds, ICU beds, Source type, Provider, Records extracted` (`19-user-comparison.png`) |
| C7 | `UNKNOWN` is rendered distinctly and never blank/NO | PASS | **10 `UNKNOWN` cells** (harborview/lakeshore `Bed count`, `Email`, `ICU beds`, `Source type`, `Provider`) vs **46 concrete value cells**; legend *"UNKNOWN means the field was not published for that hospital — never treat it as NO."* |
| C8 | No ranking / winner / score language in the comparison | PASS | ranking-language scan over the rendered table = **∅** (`best hospital`, `ranked #`, `top hospital`, `is superior`, `we recommend`, `winner is`, `overall score`); backend note present: *"Field comparison only. No ranking, scoring, or medical superiority claim is made."* |
| C9 | User cannot reach any admin route through the UI | PASS | `/admin`, `/admin/hospitals`, `/admin/sources`, `/admin/monitoring`, `/admin/audit`, `/admin/users`, `/admin/health` all redirect to `/` with `admin_layout = false`, `user_layout = true` (`20-user-admin-route-blocked.png`) |

### C10 — YES / NO / UNKNOWN distinctness (requirement detail)

| Path | Result |
|---|---|
| Live seeded comparison | **UNKNOWN** proven live (10 cells) and visually distinct from 46 concrete values. |
| Live `YES` / `NO` cells | **Not produced by the seeded data** — the demo datasets contain no boolean-valued fields (verified: zero `true`/`false` literals in hospital detail and snapshot payloads). No data was invented to force the display. |
| Renderer contract | Proven by `frontend/src/pages/UserCompare.test.tsx` → *"renders YES, NO and UNKNOWN cells distinctly from `fields[].cells`"*, which feeds the real `UserComparePage` a `true`/`null+unknown`/`false` trio and asserts `data-testid` `yes` → `YES`, `no` → `NO`, `unknown` ×2, alongside concrete values. |
| Component contract | `components/ui.test.tsx` → *"EvidenceValue distinguishes UNKNOWN / NO / YES / literal"*; `LoginPage.test.tsx` → *"EvidenceValue UNKNOWN vs NO"*. |

## D. Security checks driven from inside the browser

All calls below were issued from a real page context, so they exercise the deployed
auth stack rather than a test fixture.

| # | Check | Status | Result |
|---|---|---|---|
| D1 | Viewer `GET /api/v1/users` | PASS | `403 {"detail":"Permission denied"}` |
| D2 | Viewer `POST /api/v1/hospitals/h_demo_northstar/monitoring-policies` (foreign hospital) | PASS | `403 {"detail":"Permission denied"}` |
| D3 | Viewer `POST /api/v1/users` | PASS | `403 {"detail":"Permission denied"}` |
| D4 | Viewer `GET /api/v1/audit?limit=5` | PASS | `403 {"detail":"Permission denied"}` |
| D5 | Viewer `PATCH /api/v1/users/u_demo_admin` | PASS | `403 {"detail":"Permission denied"}` |
| D6 | Anonymous `GET /api/v1/auth/me` | PASS | `401 {"detail":"Authentication required"}` |
| D7 | Anonymous `GET /api/v1/users` | PASS | `401 {"detail":"Authentication required"}` |
| D8 | Anonymous `POST /api/v1/search` | PASS | `401 {"detail":"Authentication required"}` |
| D9 | Anonymous `GET /api/v1/audit?limit=1` | PASS | `401 {"detail":"Authentication required"}` |
| D10 | Bad password at login | PASS | `401` (backend probe) |

## E. Issues found during the walkthrough and how they were fixed

| # | Issue | Fix (frontend only — backend untouched) | Verification |
|---|---|---|---|
| E1 | **Compare table rendered no values.** `UserComparePage` read `result.hospitals`, but `POST /api/v1/search/compare` returns `hospital_ids`, `hospital_names` and `fields[].cells[]`. Every cell came out empty. | Added `normalizeCompare()` in `frontend/src/pages/user.tsx` that maps the stored-field shape (`fields[].cells[]`) into columns/rows, and still accepts the dimension shape (`hospitals[].dimensions[].decision`) used by the existing fixture. | Live table now has 14 rows × 4 columns with 56 populated cells; `UserCompare.test.tsx` (new) passes; existing `App.test.tsx` compare test still passes unchanged. |
| E2 | **Search showed no evidence.** `UserSearchPage` read `m.matches[]`, which the API never returns; real matches carry `fields[]`, `evidence[]`, `captured_at`, `record_count`. | Added `FieldList`/`EvidenceList` renderers reading the real keys; meta line now shows source, id, captured time and record count. | Live search shows 2 match cards, 2 provenance blocks with source URL / method / confidence; new test asserts the same. |
| E3 | **A `401` during source probing logged the user out** of the UI (admin probing `hospital_api` gets `401` by design). | `frontend/src/api/client.ts`: `request()` now accepts `sessionFailure?: boolean`; `onUnauthorized` fires only when the call is a session call. `testSource()` passes `sessionFailure: false`. | `client.test.ts` (3 tests) + full suite green; live admin stayed logged in through the `401` probe. |
| E4 | **Anonymous 401 probe was inconclusive** (`Failed to parse URL` — the harness never navigated before `evaluate`). | Harness fix: navigate to the app origin first. | D6–D9 now return real `401`s (section D). |
| E5 | Audit correlation id, monitoring run health, and dashboard operational status were not visible in the admin UI. | Added the `Correlation` column to the audit table; expanded the monitoring policy card (schedule/target/next/last run/success/failure/consecutive failures/error/orchestration/change set); added Operational status, Sources & connectors and Recent audit activity cards to the dashboard; extended `MonitoringPolicy` in `types/api.ts`. | Screens `02`, `05`, `11`; `tsc`/`eslint` clean. |

## F. Regression results (after all fixes)

```
backend   pytest -q                     912 passed, 1 warning in 115.07s
backend   ruff check src tests          All checks passed!
backend   mypy src                      Success: no issues found in 179 source files
frontend  tsc -b                        (no output = clean)
frontend  eslint src --max-warnings 0   (no output = clean)
frontend  vitest run                    6 files, 29 tests passed
frontend  vite build                    built in 1.08s
```

New frontend tests added in this phase: `api/client.test.ts` (3) and
`pages/UserCompare.test.tsx` (2). No existing test was modified to reach green.

## G. Evidence hygiene / secret scan

| # | Check | Status | Evidence |
|---|---|---|---|
| G1 | No `DemoAdmin/DemoViewer/DemoReviewer` password string anywhere in the repo | PASS | repo-wide scan → **0 hits** (crawl fixtures under `data/raw/**` and `data/medanta_validation/**` excluded as pre-existing corpus) |
| G2 | No JWT / bearer token material in the repo | PASS | scan for `eyJ…` → **0 hits** |
| G3 | No leftover credential-token files on disk | PASS | scan for role-named `.token` artifacts → **0 hits**; token-file writes were removed from the probe scripts early in this phase |
| G4 | `demo-evidence/browser-report.json` contains no token, password or authorization header | PASS | grep for `token|password|bearer|authorization` matches only UI copy ("authorization status", disclaimer text) |
| G5 | Screenshots contain no secrets | PASS | UI never renders tokens; login field is masked; no credential file was opened in the browser |
| G6 | Ephemeral token files from the earlier probe deleted | PASS | both credential-token files under `demo-evidence/` removed in this phase |
| G7 | Evidence folder contains only safe artifacts | PASS | 36 screenshots, `browser-report.json`, two read-only probe scripts, two dev-server logs |

## H. Honest limitations

1. **No live `YES`/`NO` cell from seeded data.** The demo datasets hold no boolean
   fields, so a live comparison yields `UNKNOWN` vs concrete values only. See C10 for
   how the tri-state rendering is proven without fabricating data.
2. **Stored provenance URLs point at `127.0.0.1:8001`.** That local mock API is not
   running in this session; the URLs are historical capture references stored inside
   the seeded datasets, which is why every probe is offline-only and
   `fetch_allowed=false`.
3. **The `mock_api` comparison-engine endpoint (`POST /api/v1/compare`) was not
   demonstrated.** It crawls live URLs; with no mock server listening it cannot
   complete, and the demo is scoped to stored-hospital comparison.
4. **Playwright lives outside the repo** (OS temp dir) so the project gains no new
   dependency and `package.json` stays unchanged.

## I. Evidence index

* Screenshots: `demo-evidence/screenshots/` (36 PNG, `01`…`20`, all captured in the
  final run against the fixed UI).
* Machine-readable observations: `demo-evidence/browser-report.json`
  (admin/user page captures, per-hospital drill-downs, API status log, 401/403 matrix).
* Read-only API probes: `demo-evidence/api_probe.py`, `demo-evidence/api_introspect.py`
  (credentials accepted from the environment only).
* Frontend harness: `browser-demo.js` in the OS temp directory (not part of the repo).

`demo-evidence/` is generated locally and is **gitignored**; screenshots, probe
scripts and dev-server logs are session artifacts, not project source.

**Git:** the release of this project to GitHub is recorded separately in
`docs/github-release-report.md`.
