# Data Storage Map (forensic)

This document records **where every kind of data lives** in the MedCompare /
hospital-extractor system: which tables, which files, what is persisted vs
in-memory vs derived. It is written from the actual source, not from intent.

**Search queries are not persistently stored; results are generated from
stored hospital evidence.**

Disclaimer: Real hospital API/feed integrations require an authorized source
configuration and credentials and are not included by default.

---

## 1. SQLite databases

| Path (default) | Role | Created by |
|---|---|---|
| `data/medcompare.db` | Real development store (datasets, auth, monitoring) | `HospitalDatabase`, `AuthStore`, `MonitoringStore` on first open |
| `data/demo/medcompare-demo.db` | Clean demo store (separate file) | `hospital-extractor demo seed` |

Configuration: `HospitalDatabase.DEFAULT_DB_PATH = Path("data/medcompare.db")`.
The API resolves the store via `request.app.state.medcompare_db_path` (set by
`serve --db` / tests) or falls back to the default. `MEDCOMPARE_DB_PATH` env is
used only when spawning multi-worker uvicorn.

### Tables (SQLite)

| Table | Owner module | Contents |
|---|---|---|
| `datasets` | `storage/database.py` | Canonical hospital dataset JSON payload + provenance columns (`hospital_id`, `hospital_key`, `source_type`, `provider`, `created_at`) |
| `sources` | `storage/database.py` | Per-dataset source metadata rows (`source_id` scoped as `dataset_id::source_id`) |
| `records` | `storage/database.py` | Extracted entity records (`path`, `name`, `payload`, …) |
| `hospitals` | `storage/database.py` | Canonical hospital identity registry (`hospital_id`, `name`, `created_at`) |
| `hospital_aliases` | `storage/database.py` | `source_ref` / `name_slug` → `hospital_id` |
| `snapshots` | `storage/database.py` | Immutable captures (`content_hash`, `captured_at`, dataset payload) |
| `change_sets` | `storage/database.py` | Field-level change audit between snapshots |
| `verification_jobs` | `storage/database.py` | Scheduled verification work (status, target source, idempotent per change_set+target) |
| `verification_results` | `storage/database.py` | Outcomes (`status`, `target_source_type`, payload with per-change verdicts) |
| `reconciliation_cases` | `storage/database.py` | Open review cases from MISMATCH/UNVERIFIED results |
| `reconciliation_events` | `storage/database.py` | Append-only case state transitions |
| `reports` | `storage/database.py` | Generated evidence reports (content + references) |
| `submissions` | `storage/database.py` | Local submission records (no live external push by default) |
| `orchestration_runs` | `storage/database.py` | End-to-end workflow runs + idempotency keys + metrics |
| `users` | `auth/store.py` | Username, role, enabled, **password hash only** |
| `auth_tokens` | `auth/store.py` | **SHA-256 token hash** + expiry (raw bearer never stored) |
| `user_hospital_access` | `auth/store.py` | Role grants for non-ADMIN hospital isolation |
| `audit_events` | `auth/store.py` | Append-only security/ops audit (no password/token metadata) |
| `monitoring_policies` | `monitoring/store.py` | Schedule, frequency, source, enablement, last-run bookkeeping |

Indexes: see `_create_tables` / `ensure_schema` in the modules above
(dataset/hospital/source, snapshots, change sets, verification jobs/results,
reconciliation cases/events, reports/submissions, orchestration idempotency,
auth token hash, audit action/hospital, monitoring due time).

---

## 2. File system (non-DB)

| Path | What | Notes |
|---|---|---|
| `config/sources.toml` | Source registry config (URLs, authorization flags, credential env **names**) | No secrets; commented samples disabled by default |
| `config/` other TOML | App/extraction settings | Loaded via `load_config()` |
| `src/hospital_extractor/api/static/` | Server static assets | Served at `/static` |
| `src/hospital_extractor/api/templates/` | Jinja2 HTML templates | Server-rendered UI |
| `frontend/` (new SPA) | React+Vite app source; build output `frontend/dist` | Not served by FastAPI by default in this phase |
| `docs/*.md` | Design, validation, runbooks | Markdown only |
| `data/export/` (optional) | Export artifacts (CSV/JSON/Excel) when export commands run | Not required for demo |
| `data/demo/` | Demo SQLite + any demo artifacts | Separate from real store |

No `FileHandler` log files: application logging goes to **stdout/stderr** via
`configure_logging` (uvicorn/console). No persisted search query log.

---

## 3. In-memory only (not persisted)

| Data | Where |
|---|---|
| Login rate-limit windows | `_LoginRateLimiter` in `auth/service.py` (per-process) |
| Crawl queue / robots state during a run | `crawler/` context for one crawl |
| FastAPI request state (`request.state.request_id`, principal) | Per request |
| Comparison/search result objects | Built from DB rows, returned, discarded |
| Quality reports | Computed from stored rows on each request |
| Auth service cache | Process-level by db path + require_auth + mode |

---

## 4. Derived (never stored as source of truth)

| Derived item | Source |
|---|---|
| Search matches | `search/service.py` over `datasets` / `records` payloads |
| Field comparison cells | `search/filters.py` + dataset paths; missing → UNKNOWN (never NO) |
| Quality metrics / `overall_score` | `quality/service.py`; **`overall_score` is always `null`** by design |
| Report content HTML/JSON | `reporting/service.py` from case + evidence refs |
| Benchmark reports | `benchmark/` fixtures + runner (ephemeral unless `--output`) |

---

## 5. Credentials & secrets

| Item | Location |
|---|---|
| User passwords | bcrypt/scrypt-style hash in `users.password_hash` only |
| API bearer tokens | Random token returned **once** at login; server stores hash in `auth_tokens.token_hash` |
| Demo passwords | Environment variables `MEDCOMPARE_DEMO_ADMIN_PASSWORD`, `MEDCOMPARE_DEMO_USER_PASSWORD`, `MEDCOMPARE_DEMO_REVIEWER_PASSWORD` — **not in source or frontend** |
| Connector credentials | Environment variable **names** only in `sources.toml`; resolved at request time via `resolve_credential` |

Frontend must not put passwords/tokens in localStorage or the JS bundle for
production guidance; demo SPA keeps token in memory + `sessionStorage` with
documented risk in runbook.

---

## 6. Concurrency & isolation

| Mechanism | Module |
|---|---|
| Monitoring policy claim/lock | `monitoring/store.py` |
| Idempotency keys | `orchestration` (`monitoring:{policy_id}:{ts}`), report/report_idempotency_key |
| Correlation IDs | middleware `request_id` on API responses |
| Hospital isolation | `require_hospital_access` (ADMIN any; others grant-only) |
| SQLite | Default serialized connection per `HospitalDatabase` instance; keep `--workers 1` with SQLite (documented in `serve`) |

---

## 7. Demo environment layout

| Entity | Demo value |
|---|---|
| Hospitals | `h_demo_northstar`, `h_demo_solstice`, `h_demo_harborview`, `h_demo_lakeshore` (all fictional, labeled DEMO) |
| Sources exercised | `website` (synthetic HTML), `mock_api` (in-process demo API), `hospital_api` listed as UNAUTHORIZED (no network) |
| Users | `admin@medcompare.demo` (ADMIN), `user@medcompare.demo` (VIEWER), optional `reviewer@medcompare.demo` (REVIEWER) |
| Monitoring | 3 daily policies |
| Verification / recon | 2 jobs, 2 MISMATCH results, 2 open cases, 1 report |
| Audit | Seeded demo events + runtime login/capture/review events |

Real hospital API/feed integrations require an authorized source configuration
and credentials and are not included by default.
