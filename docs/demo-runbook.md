# Demo Runbook

How to seed, start, exercise, and reset the MedCompare demo environment.

**Disclaimer:** Real hospital API/feed integrations require an authorized source
configuration and credentials and are not included by default. All demo
hospitals and users are fictional and labeled DEMO. Search queries are not
persistently stored; results are generated from stored hospital evidence.

Requires Python 3.13 (`.venv`), Node 20+ (Node 24 tested), npm.

---

## 1. One-time setup

```powershell
# Backend (repo root)
.venv\Scripts\python -m pip install -e ".[dev]"

# Frontend
cd frontend
npm install
```

## 2. Demo passwords (environment only)

Passwords are **never** in source or the frontend bundle. Set them in the
current shell before seeding:

```powershell
$env:MEDCOMPARE_DEMO_ADMIN_PASSWORD   = "ChangeMe-Admin-Passw0rd!"
$env:MEDCOMPARE_DEMO_USER_PASSWORD    = "ChangeMe-Viewer-Passw0rd!"
$env:MEDCOMPARE_DEMO_REVIEWER_PASSWORD = "ChangeMe-Reviewer-Pass0rd!"  # optional
```

Use different values in any shared environment. Minimum length 8 characters.

## 3. Seed a clean demo database

```powershell
# First seed (or after deleting data/demo/medcompare-demo.db)
.venv\Scripts\python -m hospital_extractor.cli demo seed

# Force a fresh deterministic reseed
.venv\Scripts\python -m hospital_extractor.cli demo seed --reset

# Machine-readable summary
.venv\Scripts\python -m hospital_extractor.cli demo seed --reset --json
```

- DB path (default): `data/demo/medcompare-demo.db`
- **Never** points at `data/medcompare.db` (refuses if you pass that path with reset).
- Seeded entities: 4 hospitals, 6 datasets, snapshots/change sets, 2
  verification jobs + 2 MISMATCH results, 2 reconciliation cases, 1 report,
  3 monitoring policies, audit events, users below.

### Demo users

| Username | Role | Password env |
|---|---|---|
| `admin@medcompare.demo` | ADMIN | `MEDCOMPARE_DEMO_ADMIN_PASSWORD` |
| `user@medcompare.demo` | VIEWER | `MEDCOMPARE_DEMO_USER_PASSWORD` |
| `reviewer@medcompare.demo` | REVIEWER (optional) | `MEDCOMPARE_DEMO_REVIEWER_PASSWORD` |

## 4. Start the API against the demo DB

```powershell
$env:MEDCOMPARE_REQUIRE_AUTH = "1"
$env:MEDCOMPARE_CORS_ORIGINS = "http://localhost:5173"

.venv\Scripts\python -m hospital_extractor.cli serve `
  --db data\demo\medcompare-demo.db --host 127.0.0.1 --port 8000
```

Smoke checks (browser or curl):

- `GET http://127.0.0.1:8000/api/v1/health` → 200 without token
- `GET http://127.0.0.1:8000/api/v1/hospitals` → 401 anonymous
- `POST http://127.0.0.1:8000/api/v1/auth/login` with demo user → token

## 5. Start the React frontend (dev)

```powershell
cd frontend
npm run dev
# http://localhost:5173  (proxies /api, /health, /ready → 127.0.0.1:8000)
```

Production-style frontend build:

```powershell
cd frontend
npm run typecheck
npm run lint
npm run test
npm run build   # → frontend/dist
```

## 6. ADMIN demo path (manual)

1. Sign in as `admin@medcompare.demo`.
2. **Dashboard** — hospital/dataset/user counts, health, quality with
   `overall_score: null`.
3. **Hospitals** — registry + datasets; open a hospital for snapshots/changes
   (Harborview/Lakeshore show CHANGES_DETECTED).
4. **Sources** — registered types, MOCK/SIMULATED badges, offline probe on
   `hospital_api` (must report no external request).
5. **Monitoring / Verification / Reconciliation / Reports** — seeded policies,
   MISMATCH jobs, open cases, evidence reports.
6. **Audit** — seeded security/ops events.
7. **Users** — list/create users (backend enforces roles).
8. **Logout** — token revoked; session cleared (sessionStorage only).

## 7. USER demo path (manual)

1. Sign in as `user@medcompare.demo`.
2. Home shows role + granted hospitals; no admin nav.
3. **Search** — free text (e.g. “Knee”); empty state when no match.
4. **Compare** — pick 2–4 hospitals; side-by-side table; UNKNOWN ≠ NO;
   no ranking/winner language; no numeric overall score.
5. **My hospitals** — only grants for the account.
6. Attempts to open `/admin` redirect to the user home (frontend convenience;
   backend still returns 403 for admin APIs).

## 8. RBAC checks worth repeating

| Check | Expected |
|---|---|
| Anonymous `GET /api/v1/hospitals` | 401 |
| VIEWER `GET /api/v1/users` | 403 |
| VIEWER `GET /api/v1/audit` | 403 |
| OPERATOR monitoring create on ungranted hospital | 403 |
| Bad login | 401, body has no `access_token` |
| Logout then `GET /auth/me` | 401 |
| `GET /api/v1/health`, `/ready` | 200 |

Automated: `tests/test_demo_env.py` (seed, ADMIN/USER flows, RBAC, P21).

## 9. Reset / cleanup

```powershell
# Reseed demo only
.venv\Scripts\python -m hospital_extractor.cli demo seed --reset

# Or delete and reseed
Remove-Item data\demo\medcompare-demo.db -ErrorAction SilentlyContinue
.venv\Scripts\python -m hospital_extractor.cli demo seed
```

Do **not** delete `data/medcompare.db` as part of demo reset.

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| `SeedError: Set MEDCOMPARE_DEMO_*_PASSWORD` | Export the env vars (step 2) in the **same** shell |
| Frontend 401 loops | Confirm `MEDCOMPARE_REQUIRE_AUTH=1` and token session; re-login |
| CORS errors from :5173 | Set `MEDCOMPARE_CORS_ORIGINS=http://localhost:5173` or use Vite proxy |
| Empty demo data | Re-run `demo seed --reset`; confirm `--db` on serve points at demo DB |
| `MEDCOMPARE_DB_PATH` seems ignored | Expected for single-process serve; use `serve --db` / `app.state` |

## 11. Related docs

- `docs/data-storage-map.md` — forensic storage map
- `docs/system-summary.md` — architecture + API surface
- `docs/demo-validation-report.md` — 30-item validation checklist
- `docs/deployment.md`, `docs/architecture.md`, `docs/final-validation-report.md`
