# Deployment guide (P25)

This document distinguishes **what is implemented in code** from **what an
operator must provide**. Nothing here claims a live cloud deployment, a
managed database, hospital API agreements, or government integration
credentials.

## Environments

| Environment | Auth mode | Notes |
|---|---|---|
| **LOCAL / DEVELOPMENT** | `MEDCOMPARE_AUTH_MODE=development` (default) | `require_auth=false` by default; optional `X-Medcompare-Dev-User` bypass labeled `development_bypass` for convenience. |
| **TEST** | test fixtures / `create_app(auth_enabled=True)` | Fail-closed 401/403; used by `tests/`. |
| **PRODUCTION-READY CONFIGURATION** | `MEDCOMPARE_AUTH_MODE=production` | Fail-closed: `require_auth=true`, dev header disabled, startup verification refuses invalid config (no silent dev-auth fallback). Requires external secrets via env. |
| **ACTUALLY DEPLOYED** | — | **NOT CLAIMED.** No cloud, Kubernetes, SLA, or managed DB is provisioned by this repository. |

Disclaimer: Real hospital API/feed integrations require an authorized source
configuration and credentials and are not included by default.

## Startup commands

```bash
# API (after install)
hospital-extractor serve --host 0.0.0.0 --port 8000

# Or uvicorn directly (factory)
uvicorn hospital_extractor.api.app:create_app --factory --host 0.0.0.0 --port 8000

# Startup verification only (JSON report, exit 0/1)
hospital-extractor verify-startup

# Workers (explicit — never started by the API)
hospital-extractor worker                  # verification worker (continuous)
hospital-extractor worker --once           # one cycle then exit
hospital-extractor monitoring worker       # monitoring worker (continuous)
hospital-extractor monitoring worker --once
```

Workers handle `SIGINT` / `SIGTERM` for graceful drain where the platform
supports it; otherwise `KeyboardInterrupt` still stops them cleanly.

## Required production environment

| Variable | Purpose | Required in production? |
|---|---|---|
| `MEDCOMPARE_AUTH_MODE=production` | Fail-closed auth + startup checks | **Yes** |
| `MEDCOMPARE_BOOTSTRAP_ADMIN_PASSWORD` | One-time initial ADMIN password (env only, never in repo) | Recommended on first boot |
| `MEDCOMPARE_BOOTSTRAP_ADMIN_USERNAME` | Initial ADMIN username (default `admin`) | Optional |
| `MEDCOMPARE_TRUSTED_HOSTS` | Comma-separated Host allowlist (explicit hosts, no `*`) | **Yes** |
| `MEDCOMPARE_CORS_ORIGINS` | Comma-separated browser origins (empty = no CORS) | Optional |
| `MEDCOMPARE_REQUIRE_AUTH` | Force auth on/off (production implies on; do **not** set `0` in prod) | Leave unset in prod |
| `MEDCOMPARE_AUTH_TOKEN_TTL_SECONDS` | Token lifetime (default 12h) | Optional |
| `MEDCOMPARE_LOGIN_RATE_LIMIT` / `MEDCOMPARE_LOGIN_RATE_WINDOW_SECONDS` | Login brute-force throttle (default 10/60s) | Optional |
| `MEDCOMPARE_DB_PATH` (via `--db` / code default `data/medcompare.db`) | SQLite location | Set to a durable volume path |
| `VERIFICATION_DELAY_MINUTES` | Cool-down before verification capture (default 0; set >0 in prod) | Recommended |
| `VERIFICATION_MAX_ATTEMPTS` / `VERIFICATION_BACKOFF_MINUTES` | Retry policy | Optional |
| `MONITORING_MAX_ATTEMPTS` / `MONITORING_BACKOFF_MINUTES` / `MONITORING_MIN_INTERVAL_MINUTES` / `MONITORING_WORKER_INTERVAL_SECONDS` / `MONITORING_MAX_POLICIES_PER_CYCLE` | Monitoring tuning | Optional |
| `ORCHESTRATION_*` / `RECONCILIATION_*` / `REPORTING_*` | Workflow policy (external submission stays unauthorized by default) | Optional |
| `MEDCOMPARE_CONNECTOR_<NAME>_TOKEN` (etc.) | Source connector credentials — **names only** in `config/sources.toml` | Only if enabling authorized connectors |

No secret values belong in `config/sources.toml`, Dockerfiles, or the repo.

## Health probes

| Endpoint | Meaning | Auth |
|---|---|---|
| `GET /health` | Liveness — process is up | Open |
| `GET /api/v1/health` | Liveness (API namespace) | Open |
| `GET /ready` | Readiness — DB reachable, config sane, templates/static present (503 if not) | Open |

Responses are deterministic JSON and never include secrets or stack traces.

## Docker

```bash
docker build -t medcompare-api .
# API
docker run --rm -p 8000:8000 \
  -e MEDCOMPARE_AUTH_MODE=production \
  -e MEDCOMPARE_TRUSTED_HOSTS=localhost,127.0.0.1 \
  -e MEDCOMPARE_BOOTSTRAP_ADMIN_PASSWORD=... \
  -v "$PWD/data:/app/data" \
  medcompare-api
# Worker
docker run --rm -v "$PWD/data:/app/data" medcompare-api \
  hospital-extractor worker --once
```

Image runs as non-root (`USER app`). No secrets are baked into layers.
Docker smoke was executed only if a local Docker daemon was available;
otherwise the Dockerfile is validated by static CI checks only.

## SQLite operations

- **Location:** default `data/medcompare.db` (or `--db` / durable mount).
- **Backup:** stop writers (or use `sqlite3 backup`), copy the file + `-wal`/`-shm` if present.
- **Restore:** place the file back, ensure ownership for the app user, start API/worker.
- **Migration:** schema is created with `CREATE TABLE IF NOT EXISTS` on open (additive). Do not hand-edit `database.py` history; point at a copy first.
- **Integrity:** `PRAGMA integrity_check;` via `sqlite3 data/medcompare.db`.
- **Safe shutdown:** SIGINT/SIGTERM the worker (drains current cycle); for the API, stop accepting traffic then close — SQLite WAL/rollback handles partial writes.

## Security hardening checklist (P24 + P25)

- [x] Production requires auth (`require_auth=true`) — verified at startup
- [x] Dev header disabled in production (fail closed)
- [x] Trusted hosts configured explicitly (no wildcard in production)
- [x] CORS empty by default (restrictive when set)
- [x] Secrets only via environment references
- [x] Debug/tracebacks not returned to clients (sanitized handlers)
- [x] Login rate limit active
- [x] P21 connectors fail closed without authorization
- [x] Startup refuses invalid production config (no silent fallback)

## What is IMPLEMENTED vs NOT CLAIMED

| Item | Status |
|---|---|
| Package build (wheel/sdist) + entry points | IMPLEMENTED |
| Dockerfile (non-root, env config) | IMPLEMENTED (image build smoke optional) |
| Health / readiness endpoints | IMPLEMENTED |
| Production fail-closed startup verification | IMPLEMENTED |
| Worker graceful shutdown | IMPLEMENTED |
| Structured logging + secret scrubbing | IMPLEMENTED |
| CI: pytest, ruff, mypy, build, clean-install smoke | IMPLEMENTED (local command parity; remote GitHub run not claimed unless executed) |
| Live cloud / K8s / managed DB / SLA | **NOT CLAIMED** |
| Hospital API agreements / government integration credentials | **NOT CLAIMED / REQUIRES AUTHORIZATION** |
| Autonomous medical decisions | **NOT IMPLEMENTED** |
