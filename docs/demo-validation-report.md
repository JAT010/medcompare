# Demo Validation Report — Full System Test + Multi-User Frontend

**Date:** 2026-09-24  
**Environment:** Windows 11, Python 3.13.15 (`.venv`), Node v24.13.0, npm 11.6.2  
**Scope:** Clean demo DB, ADMIN/USER flows, RBAC, new React SPA, docs, revalidation.  
**Backend baseline after phase:** pytest **890**, ruff **0**, mypy **0**.  
**Frontend:** typecheck **0**, vitest **24**, eslint **0**, `vite build` **ok**.

**Disclaimer:** Real hospital API/feed integrations require an authorized
source configuration and credentials and are not included by default. Demo
hospitals/users are fictional and labeled DEMO. Search queries are not
persistently stored. No ranking/winner language; `overall_score` stays null.

---

## A. Demo environment (deterministic, isolated)

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Dedicated demo DB path `data/demo/medcompare-demo.db` | PASS | `demo/seed.py` `DEMO_DB_PATH` |
| 2 | Real store `data/medcompare.db` never used as demo target | PASS | `SeedError` on reset of real path; tests `test_real_dev_db_not_touched` |
| 3 | Seed requires env passwords (no hardcoding) | PASS | `SeedError` without `MEDCOMPARE_DEMO_*_PASSWORD` |
| 4 | CLI `hospital-extractor demo seed [--reset] [--json]` | PASS | `cli.py` `_run_demo`; verified (fails closed without env) |
| 5 | Fixed hospital IDs (4) + DEMO labels | PASS | `h_demo_northstar/solstice/harborview/lakeshore` |
| 6 | Datasets / snapshots / change sets seeded | PASS | SeedSummary counts; Harborview/Lakeshore CHANGES_DETECTED |
| 7 | Verification jobs + MISMATCH results seeded | PASS | `v_demo_harborview`, `v_demo_lakeshore` + results |
| 8 | Reconciliation cases + report + monitoring policies | PASS | seed summary + admin flow tests |
| 9 | Users: ADMIN + VIEWER (+ optional REVIEWER) | PASS | `admin@medcompare.demo`, `user@medcompare.demo` |
| 10 | Reseed determinism (same counts/IDs) | PASS | `test_seed_is_resettable` |
| 11 | MOCK/SIMULATED badges for mock_api sources | PASS | admin UI + source `authorization_status` |
| 12 | Storage map documents search non-persistence | PASS | `docs/data-storage-map.md` line on search queries |

## B. Backend ADMIN flow

| # | Item | Status | Evidence |
|---|---|---|---|
| 13 | Login → token → `/auth/me` role admin | PASS | `test_admin_login_and_me` |
| 14 | List users / hospitals / registry / sources / audit | PASS | `test_admin_lists_users_hospitals_sources` |
| 15 | Search + compare (no winner/best language) | PASS | `test_admin_search_and_compare` |
| 16 | Monitoring / verification / reports / quality read paths | PASS | `test_admin_monitoring_verification_reports` |
| 17 | Quality `overall_score` is null when present | PASS | same test |
| 18 | Offline probe: unauthorized connector `fetch_allowed=false`, no network | PASS | `test_admin_connector_unauthorized_probe_offline` |

## C. Backend USER flow + RBAC

| # | Item | Status | Evidence |
|---|---|---|---|
| 19 | Viewer login; read-only hospital/search/quality | PASS | `test_user_login_and_read_only` |
| 20 | Viewer 403 on users, audit, verification create, review | PASS | `test_user_forbidden_admin_ops` |
| 21 | Anonymous 401 on protected; health/login open | PASS | `test_anonymous_401` |
| 22 | Cross-hospital grant swap 403 (OPERATOR) | PASS | `test_cross_hospital_id_swap_403` |
| 23 | Bad login 401; no token leak | PASS | `test_bad_login_401_no_token_leak` |
| 24 | Logout revokes token (subsequent `/auth/me` 401) | PASS | `test_logout_revokes_token` |
| 25 | P21 configured unauthorized sources never claim AUTHORIZED without cred | PASS | `test_configured_unauthorized_source_lists_without_network` |
| 26 | No hardcoded demo passwords in seed.py or frontend sources | PASS | `test_passwords_not_hardcoded_in_source` + frontend `no-hardcoded-secrets.test.ts` |

## D. Frontend (React + TS + Vite)

| # | Item | Status | Evidence |
|---|---|---|---|
| 27 | App structure `components/layouts/pages/auth/api/hooks/types/utils` | PASS | `frontend/src/**` (utils folded into ui/client where tiny) |
| 28 | Typed API client; 401 handler; bearer + sessionStorage only | PASS | `src/api/client.ts`, `AuthContext.tsx` |
| 29 | Login page: demo note, error state, no password echo | PASS | `LoginPage.test.tsx` |
| 30 | RBAC routing: anon→login, non-admin blocked from `/admin`, admin layout | PASS | `LoginPage.test.tsx` routing suite |
| 31 | Admin dashboard counts + null overall score + disclaimer | PASS | `App.test.tsx` |
| 32 | User home: role, grants, no ranking/winner claims | PASS | `App.test.tsx` |
| 33 | Compare: 2–4 selection gate; UNKNOWN ≠ NO; no numeric score | PASS | `App.test.tsx` |
| 34 | Search empty state | PASS | `App.test.tsx` |
| 35 | Logout clears session → login | PASS | `App.test.tsx` |
| 36 | UI primitives: loading/empty/error/badge/disclaimer | PASS | `ui.test.tsx` |
| 37 | Loading / empty / 401 (session clear) / 500 error / role 403 redirect | PASS | combined frontend tests |
| 38 | `npm run typecheck` | PASS | `tsc -b` 0 errors |
| 39 | `npm run lint` (eslint) | PASS | 0 problems after config |
| 40 | `npm run test` | PASS | 24/24 |
| 41 | `npm run build` production bundle | PASS | `frontend/dist` emitted |

## E. Docs & revalidation

| # | Item | Status | Evidence |
|---|---|---|---|
| 42 | `docs/data-storage-map.md` | PASS | present |
| 43 | `docs/demo-runbook.md` | PASS | present |
| 44 | `docs/system-summary.md` | PASS | present |
| 45 | `docs/demo-validation-report.md` | PASS | this file |
| 46 | Backend `pytest -q` ≥ 873 (actual **890**) | PASS | full suite |
| 47 | `ruff check src tests` → 0 | PASS | |
| 48 | `mypy src` → 0 | PASS | 178 files |
| 49 | No commit / no push this phase | PASS | HEAD still `a651c14` |
| 50 | Docker runtime | **NOT MEASURED** | `docker` not on PATH |
| 51 | Remote GitHub Actions | **NOT MEASURED** | not executed remotely |
| 52 | Composite overall score | **NEVER COMPUTED** | by design |

---

## F. Known limitations / honest gaps

1. **Docker / remote CI:** not measured on this host.
2. **SPA not mounted by FastAPI by default:** serve API + Vite proxy (dev) or
   host `frontend/dist` on any static host; wiring is documented in the runbook.
3. **Browser E2E (Playwright/Cypress):** not added; covered by vitest + API
   integration tests instead.
4. **Pre-existing TODO:** `src/hospital_extractor/orchestration/service.py:553`.
5. **database.py SHA** differs from P26 baseline due to intentional demo-blocking
   bugfixes (`save_orchestration_run` columns; `verification_results.target_source_type`).
6. Real-world site figures remain **OBSERVED** from prior runs
   (`docs/validation-report-post-p19c.md`, `…-p19d.md`) — not re-executed.

---

## G. Sign-off

| Gate | Result |
|---|---|
| Demo isolated from real DB | **PASS** |
| Passwords env-only | **PASS** |
| Backend RBAC tested (401/403/swap/probe) | **PASS** |
| Frontend multi-user admin + user UI | **PASS** |
| UNKNOWN distinct from NO; no ranking | **PASS** |
| Quality overall_score null | **PASS** |
| Full revalidation green | **PASS** (890 / 0 / 0 + FE 24 / 0 / 0) |
| Deliverable docs present | **PASS** |
| Repo hygiene (no commit/push) | **PASS** |
