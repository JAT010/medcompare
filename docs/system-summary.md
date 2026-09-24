# System Summary — MedCompare

**Date:** 2026-09-24  
**Stack:** Python 3.13 FastAPI backend + React 19 / TypeScript / Vite SPA  
**Backend is the source of truth.** Frontend guards are convenience only.

**Disclaimer:** Real hospital API/feed integrations require an authorized
source configuration and credentials and are not included by default. Demo
data is fictional and labeled DEMO. Search queries are not persistently
stored. No ranking, winner, or composite `overall_score` is ever computed.

---

## 1. What the system does

Source-agnostic hospital evidence platform:

1. **Capture** — website crawl, simulated demo API, authorized hospital API
   (optional), JSON feed → one canonical `HospitalDataset` with provenance.
2. **Snapshot & change** — immutable snapshots; field-level change sets.
3. **Verify** — compare change against a target source; VERIFIED / MISMATCH /
   UNVERIFIED outcomes.
4. **Reconcile** — open review cases for non-clean outcomes; append-only
   events; human review/resolve/dismiss/reopen.
5. **Monitor** — scheduled policies (enable/disable/run) + worker.
6. **Report** — evidence reports (JSON content in DB); no live external push.
7. **Search & compare** — structured filters + free text; side-by-side for
   2–4 hospitals; UNKNOWN never coerced to NO.
8. **Quality** — pipeline/hospital/dataset metrics from stored rows only;
   `overall_score` always `null` when present.
9. **AuthN/AuthZ** — users, roles (ADMIN/OPERATOR/REVIEWER/VIEWER), hospital
   grants, bearer tokens (hashed at rest), audit log.

---

## 2. Process topology

| Process | Command | Port / path |
|---|---|---|
| API (dev) | `hospital-extractor serve --db …` | `127.0.0.1:8000` |
| Vite dev SPA | `cd frontend && npm run dev` | `5173` → proxy `/api`,`/health`,`/ready` |
| SPA production build | `npm run build` → `frontend/dist` | Not auto-served by FastAPI in this phase |
| Demo seed | `hospital-extractor demo seed [--reset]` | `data/demo/medcompare-demo.db` |
| Mock demo API | `medcompare-demo-server` | local simulated hospital API |
| Monitoring/verify worker | `hospital-extractor worker` / `monitoring worker` | CLI |

Default DB: `data/medcompare.db`. Demo DB: `data/demo/medcompare-demo.db`.

---

## 3. Backend layout

```
src/hospital_extractor/
  api/          app, auth, profile, monitoring, verification, reconciliation,
                reporting, sources, search, quality, ops, routes, templates
  auth/         models, store (users/tokens/grants/audit), service
  demo/         seed.py deterministic DEMO fixture loader
  storage/      database.py HospitalDatabase (SQLite schema + migrations)
  snapshots/ change detection, verification/, reconciliation/, monitoring/
  reporting/    exporters/, search/, quality/, orchestration/
  sources/      website | mock_api | hospital_api | json_feed registry
  benchmark/    P26 harness (fixtures A–U, runner, formatters)
  cli.py        all subcommands including demo seed
```

Routers are mounted in `api/app.py` with shared auth dependencies when
`auth_enabled` (demo/runbook uses `MEDCOMPARE_REQUIRE_AUTH=1`).

---

## 4. API surface used by the SPA (prefix `/api/v1` unless noted)

| Area | Methods | Notes |
|---|---|---|
| Auth | `POST /auth/login`, `POST /auth/logout` (204), `GET /auth/me` | Bearer token; logout revokes |
| Users | `GET/POST /users`, `PATCH /users/{id}` | ADMIN |
| Audit | `GET /audit` | ADMIN; returns `{events:[…]}` or list |
| Hospitals | `GET /hospitals`, `GET /hospitals/registry` | datasets vs registry |
| Hospital detail | `GET /hospitals/{id}/snapshots`, `/changes`, `/monitoring-policies`, `/verification-jobs`, `/reconciliation-cases`, `/reports` | some POSTs require grants |
| Sources | `GET /sources`, `/connectors`, `/sources/configured`, `GET /sources/{type}/test` | offline probe; unauthorized → no network |
| Monitoring | `POST /hospitals/{id}/monitoring-policies`, `POST /monitoring-policies/{id}/enable\|disable\|run` | RBAC + grants |
| Verification | create/list jobs under hospital | RBAC |
| Reconciliation | list/get/review/resolve/report under case id | RBAC |
| Search | `POST /search`, `POST /search/compare` | no ranking language |
| Quality | `GET /quality/pipeline`, `/quality/hospitals/{id}`, `/quality/datasets/{id}` | `overall_score` null |
| Health | `GET /api/v1/health`, `GET /health`, `GET /ready` | open |

Request/response shapes: see `frontend/src/types/api.ts` and
`frontend/src/api/client.ts`.

---

## 5. Frontend layout

```
frontend/
  src/
    api/client.ts          typed fetch + ApiError + bearer token (module state)
    auth/AuthContext.tsx   login/logout/refresh; token in sessionStorage only
    auth/RequireAuth.tsx   anonymous → /login; role gate for /admin
    layouts/Layouts.tsx    AdminLayout / UserLayout
    pages/LoginPage.tsx
    pages/admin.tsx        dashboard, hospitals, sources, monitoring,
                           verification, reconciliation, reports, quality,
                           audit, users, health
    pages/user.tsx         home, search, compare, my-hospitals, quality, reports
    components/ui.tsx      Badge, Card, Loading, Empty, Error, EvidenceValue,
                           Disclaimer
    hooks/useAsync.ts      loading / error / reload
    types/api.ts           shared types
  tests: login, RBAC routing, admin/user pages, UNKNOWN vs NO, no hardcoded
         secrets (24 tests)
```

**UX rules enforced in UI + tests**

- Loading, empty, 401, 403 (redirect/role), 404, and error states.
- `EvidenceValue` renders **UNKNOWN** distinctly from **NO**.
- Disclaimer on data pages: no ranking / no composite score / DEMO label /
  credentials not included by default.
- No password literals in `frontend/**` (asserted by backend + frontend tests).
- Token in `sessionStorage` + memory; not localStorage; not in URL.

---

## 6. Security model (authoritative on backend)

- Passwords: hashed in `users`; demo passwords only via
  `MEDCOMPARE_DEMO_*_PASSWORD` env at seed time.
- Tokens: random; **SHA-256 hash** stored in `auth_tokens`; raw bearer only
  to client.
- Roles → permissions matrix in `auth` package; hospital isolation via
  `user_hospital_access`.
- Audit: append-only `audit_events` (no password/token material).
- Unauthorized connectors: configured without `authorized=true` → probe
  returns `fetch_allowed: false` and **zero outbound network**.
- Frontend `RequireAuth` is not a security boundary.

---

## 7. Storage (summary)

Full forensic map: `docs/data-storage-map.md`.

- SQLite tables for datasets, hospitals, snapshots, change sets, verification,
  reconciliation, reports, submissions, orchestration, auth, audit, monitoring.
- Config: `config/sources.toml` (credential **names** only).
- Logs: stdout/stderr only. Search queries not persisted.
- Demo vs real DB are **separate files**.

---

## 8. Validation status (this phase)

| Check | Result |
|---|---|
| Backend pytest | **890 passed** |
| ruff (`src` + `tests`) | **0** |
| mypy (`src`) | **0** (178 files) |
| Frontend typecheck | **0 errors** |
| Frontend vitest | **24 passed** |
| Frontend eslint | **0** |
| Frontend production build | **ok** (`frontend/dist`) |
| Demo seed CLI | **ok** (env passwords required) |
| Docker image runtime | **NOT MEASURED** (docker not on PATH) |
| Remote CI | **NOT MEASURED** |
| Composite overall score | **NEVER COMPUTED** (by design) |

Prior phases: see `docs/final-validation-report.md` (P26), validation-report
post-P19c/P19d/P25.

---

## 9. Related documentation

| Doc | Purpose |
|---|---|
| `docs/demo-runbook.md` | Operate the demo |
| `docs/demo-validation-report.md` | 30-item demo validation |
| `docs/data-storage-map.md` | Where data lives |
| `docs/architecture.md` | Design history |
| `docs/deployment.md` | Deploy notes |
| `docs/final-validation-report.md` | P26 benchmark/final report |
| `README.md` | Install + CLI overview |
