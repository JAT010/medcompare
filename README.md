# MedCompare

**Owner/Author:** Devansh Dhama
**Repository:** https://github.com/JAT010/medcompare

Crawls a hospital website, extracts structured records, and projects them onto a
25-field master hospital intake form (JSON/CSV/XLSX). Also compares multiple
hospitals side-by-side for a target procedure or department.

The platform is **source-agnostic**: hospital websites, a simulated demo API,
an authorized hospital API, and persisted JSON feeds all land in one canonical
dataset with record-level provenance. Profiles persist to SQLite, every fact
traces back to a source, and contradictory evidence is surfaced rather than
silently resolved. See `docs/architecture.md` for the full design.

Requires **Python 3.13** (`pyproject.toml` pins `>=3.13,<3.14`).

## Install

```bash
# create the environment (uv or venv-pip)
python -m uv venv .venv
# or: python -m venv .venv

# install the package and dev tooling
.venv\Scripts\python -m uv pip install -e ".[dev]"
# or: .venv\Scripts\pip install -e ".[dev]"
```

## Extract a hospital

```bash
# crawl and export in one step
.venv\Scripts\python main.py https://www.example-hospital.in --export

# equivalent via the installed entry point
.venv\Scripts\hospital-extractor https://www.example-hospital.in --export
```

`--export` writes (default output dir `data/`):

```
data/export/<name>.json                     dataset JSON
data/export/<name>_master/master.csv        final 25-column master intake CSV
data/export/<name>_master/master.xlsx       master intake workbook
data/export/<name>_master/master_quality.md provenance / quality report
```

`<name>` derives from the extracted hospital name (`--output-dir` overrides the
default `data/`). Useful crawl flags: `--max-pages`, `--max-depth`, `--delay`,
`--timeout`, `--include-low-value`. See `hospital-extractor --help`.

## Compare hospitals

```bash
# procedure comparison (matched by treatment/procedure records)
.venv\Scripts\hospital-extractor compare \
  --procedure "Knee Replacement" https://host-a.example https://host-b.example

# department comparison (matched against department records)
.venv\Scripts\hospital-extractor compare \
  --procedure "Cardiology" --category department https://host-a.example https://host-b.example

# machine-readable output
.venv\Scripts\hospital-extractor compare \
  --procedure "Knee Replacement" --json https://host-a.example https://host-b.example
```

2–4 URLs are allowed. Each hospital is crawled fresh during the comparison.
`--category` is optional; without it the target is auto-detected from the name.
`--json` prints the full result, `--json-file FILE` writes it to disk.

### Other data sources

Comparisons can run over any registered source.  `--source` takes one of
`website | mock_api | hospital_api | json_feed`:

```bash
# demo: consume the simulated hospital API (start it first)
.venv\Scripts\medcompare-demo-server            # terminal 1
.venv\Scripts\hospital-extractor compare --source mock_api \
  --procedure "Knee Replacement" demo-northstar demo-solstice

# persisted JSON feeds (paths into the filesystem)
.venv\Scripts\hospital-extractor compare --source json_feed \
  --procedure "Knee Replacement" data/export/demo-mock/feed.json ...
```

For mock_api / hospital_api the "URLs" are hospital IDs; for json_feed they are
dataset JSON paths.  Source adapters are configured in `config/sources.toml`
and registered via `SourceRegistry` (`sources/registry.py`).  `--gov-export`
additionally writes a simulated `submission.json` + `manifest.json` for the
government portal exporter (`exporters/`).

### One-shot source-agnostic demo (no server, no internet)

`integration-demo` runs the whole stack in-process — it pulls both demo
hospitals from the simulated API over an in-process ASGI transport, persists
them to the local SQLite store, and runs a real comparisons:

```bash
.venv\Scripts\hospital-extractor integration-demo
```

Output shows per-source fetch timings and ends with the same comparison the web
UI renders.  Resulting profiles are browsable at `/hospitals`; the stored data
is reusable by any later `compare --source mock_api demo-northstar` run.

## API + web frontend

Start the FastAPI app (live extraction on each request):

```bash
# preferred: verified startup (P25)
.venv\Scripts\hospital-extractor serve --host 127.0.0.1 --port 8000

# or uvicorn factory directly
.venv\Scripts\python -m uvicorn hospital_extractor.api.app:create_app --factory

# production-style config check only (JSON report, exit 0/1)
.venv\Scripts\hospital-extractor verify-startup
```

Workers are **never** started by the API — run them explicitly:

```bash
.venv\Scripts\hospital-extractor worker                 # verification (continuous)
.venv\Scripts\hospital-extractor worker --once          # one cycle
.venv\Scripts\hospital-extractor monitoring worker      # monitoring (continuous)
.venv\Scripts\hospital-extractor monitoring worker --once
```

Both continuous workers stop cleanly on `SIGINT`/`SIGTERM` (or Ctrl+C).
See `docs/deployment.md` for production environment variables, Docker, SQLite
ops, and the IMPLEMENTED vs NOT CLAIMED checklist.

- `GET  /`            — web form (two URLs, optional category)
- `POST /compare`     — same form via HTMX, returns a rendered result fragment
- `POST /api/v1/compare` — JSON API
- `GET  /health`      — liveness probe (P25, unauthenticated)
- `GET  /ready`       — readiness: DB + config + assets (P25, 503 if not ready)
- `GET  /api/v1/health` — health check
- `GET  /api/v1/hospitals/{id}/verification-jobs` — verification jobs + results
- `GET  /api/v1/verification-jobs/{job_id}` — one job + its latest result
- `POST /api/v1/hospitals/{id}/verification-jobs` — schedule a manual job
- `GET  /api/v1/hospitals/{id}/reconciliation-cases` — list a hospital's cases
- `GET /api/v1/reconciliation-cases/{case_id}` — one case + evidence + audit trail
- `POST /api/v1/reconciliation-cases/{case_id}/review` — OPEN → UNDER_REVIEW
- `POST /api/v1/reconciliation-cases/{case_id}/resolve` — resolve with a recorded decision
- `POST .../dismiss`, `POST .../reopen` — dismiss / reopen (never a duplicate case)
- `GET/POST /api/v1/hospitals/{id}/monitoring-policies` — list / create P20 policies
- `GET /api/v1/monitoring-policies/{id}` — one policy
- `POST /api/v1/monitoring-policies/{id}/enable|disable|run` — pause / resume / run now
- `POST /api/v1/search` — structured search over stored datasets (P22)
- `POST /api/v1/search/compare` — field-by-field compare of 2–4 stored hospitals (P22)
- `GET/POST /search` — server-rendered search + compare UI (P22)
- `GET /api/v1/quality/pipeline` — whole-store quality metrics (P23)
- `GET /api/v1/quality/hospitals/{id}` — hospital quality report; `?format=markdown` (P23)
- `GET /api/v1/quality/hospitals/{id}/sources` — per-source quality rollups (P23)
- `GET /api/v1/quality/datasets/{id}` — dataset quality report; `?format=markdown` (P23)
- `GET /api/v1/quality/format/{scope}` — Markdown/JSON export (`pipeline|hospital|dataset`) (P23)
- `GET /quality` — quality & observability overview UI (P23)
- `POST /api/v1/auth/login` — exchange username/password for a bearer token (P24)
- `POST /api/v1/auth/logout` — revoke the current bearer token (P24)
- `GET /api/v1/auth/me` — current principal + permissions (P24)
- `GET/POST /api/v1/users`, `PATCH /api/v1/users/{id}` — ADMIN user management (P24)
- `GET /api/v1/audit`, `GET /api/v1/audit/{event_id}` — security audit log (P24)

`POST /api/v1/compare` body (both fields optional, 1–4 URLs):

```json
{
  "urls": ["https://host-a.example", "https://host-b.example"],
  "procedure": "Knee Replacement",
  "category": "procedure"
}
```

`category` is one of `procedure`, `treatment`, `speciality`, `department`; any
other value is rejected with HTTP 422.

The JSON body also accepts `"source"` (`website` default, `mock_api`,
`hospital_api`, `json_feed`).  Every comparison automatically persists the
harvested canonical datasets to the SQLite store.

### Hospital profiles & demo mode

```bash
.venv\Scripts\medcompare-demo-server   # fully-simulated hospital API on :8001
```

- `GET  /`                    — web form (source selector + “Load demo
                                comparison” button)
- `GET  /hospitals/`          — stored-hospital gallery (from SQLite)
- `GET  /hospitals/{id}`      — single hospital profile with source badges
- `GET/POST/DELETE /api/v1/hospitals[...]` — list / read / import / delete

Demo facilities are fictional (`demo-northstar`, `demo-solstice`) and every
demo surface is labelled SIMULATED.  Demo data must be verified before any
real-world use.

## Verify captured changes

When a capture detects real changes to a hospital, the platform can prove they
really happened by re-harvesting the published target (by default the
hospital's own website) and comparing it against the changed records — without
touching the normal read path.  A capture event becomes a **VerificationJob**;
a background **worker** executes it and stores one **VerificationResult**:

- `VERIFIED`   — the changed fact is present on the target source.
- `MISMATCH`   — the target source contradicts the change (e.g. a "removed"
  service is still listed, a bed count differs).
- `UNVERIFIED` — the target source is silent (absence is never treated as NO).
- `FAILED`     — the target could not be checked; temporary errors retry with
  backoff up to a bounded attempt limit, permanent or authorization-required
  sources are never attempted.

```bash
# process every due verification job once
.venv\Scripts\hospital-extractor worker --once

# or keep the worker running in the background
.venv\Scripts\hospital-extractor worker

# schedule a manual verification job and show the audit trail
.venv\Scripts\hospital-extractor verify demo-northstar
.venv\Scripts\hospital-extractor verification-jobs demo-northstar

# deterministic offline demo (no server, no internet)
.venv\Scripts\hospital-extractor verification-demo
```

API endpoints: `GET/POST /api/v1/hospitals/{id}/verification-jobs` and
`GET /api/v1/verification-jobs/{job_id}`; the profile page renders a
"Verification status" panel per job.

## Continuous monitoring (P20)

Attach a fixed UTC schedule (`hourly` / `daily` / `weekly`) to a hospital so
the existing capture/verification pipeline runs on a timer.  Policies are keyed
by the canonical hospital id and delegate to the orchestration service under a
deterministic idempotency key — monitoring never reimplements capture,
verification, reconciliation or reporting, and it never starts itself: you run
the explicit worker.

```bash
# create (or update) a daily policy for a known hospital
.venv\Scripts\hospital-extractor monitoring add demo-northstar \
  --frequency daily --target-ref https://demo.northstar.example.in

# list / pause / resume
.venv\Scripts\hospital-extractor monitoring list
.venv\Scripts\hospital-extractor monitoring disable mp_…
.venv\Scripts\hospital-extractor monitoring enable mp_…

# one due policies now, or keep the worker running
.venv\Scripts\hospital-extractor monitoring worker --once
.venv\Scripts\hospital-extractor monitoring worker

# trigger a single policy immediately (still atomic + idempotent)
.venv\Scripts\hospital-extractor monitoring run mp_…
```

API: `GET/POST /api/v1/hospitals/{id}/monitoring-policies`,
`GET /api/v1/monitoring-policies/{id}`,
`POST …/{id}/enable|disable|run`.  The profile page renders a "Monitoring"
panel.  **Limitation:** monitoring does not add provider credentials; an
authorized source fails closed until a real integration exists (the government
provider stays mock/authorized-only).

## Authorized source connectors (P21)

The `sources/` registry now has an explicit **authorization boundary** for
API and remote-feed connectors.  Fail-closed by default: a `hospital_api` or
remote `json_feed` source refuses to open a socket unless configuration says
`authorized = true`, the target host is allowlisted, and (when required) an
environment credential is present.  Website crawls and local feed files are
unchanged.

```bash
# list registered source types + authorization flags
.venv\Scripts\hospital-extractor sources definitions

# list configured sources from config/sources.toml (credential names only)
.venv\Scripts\hospital-extractor sources list

# list connector classes
.venv\Scripts\hospital-extractor connectors list

# offline authorization/config probe (never fetches)
.venv\Scripts\hospital-extractor connectors test hospital_api
# → exit 1, UNAUTHORIZED — fail closed (no external request)

# acquire only with an explicit ref (and only if authorized)
.venv\Scripts\hospital-extractor connectors test hospital_api \
  --ref north-star --fetch
```

API (read-only introspection; **no arbitrary URL is ever fetched**):

- `GET /api/v1/sources` — registered source definitions
- `GET /api/v1/connectors` — resolved connector classes
- `GET /api/v1/sources/configured` — `config/sources.toml` entries
  (credential *env names* only)
- `GET /api/v1/sources/{source_type}/test` — offline probe
  (404 unknown type, 401 unauthorized)

The profile page renders a **Source connector** card with connector class,
provider, and an `UNAUTHORIZED` / `SIMULATED / MOCK` / `AUTHORIZED` badge.

Configuration (`config/sources.toml`) — **no secrets in the file**:

```toml
[[sources]]
name = "northstar-api"
source_type = "hospital_api"
hospital_ref = "north-star"
base_url = "https://api.example-hospital.in"
provider = "Northstar Hospital (authorized integration)"
authorized = true                 # fail closed unless true
allowed_hosts = ["api.example-hospital.in"]   # SSRF allowlist
credential_env = "MEDCOMPARE_CONNECTOR_NORTHSTAR_TOKEN"  # env var *name*
credential_required = true
timeout_seconds = 15
max_response_bytes = 5000000
```

```bash
# set the secret at runtime (never stored in the repo or database)
export MEDCOMPARE_CONNECTOR_NORTHSTAR_TOKEN=…   # Windows: $env:…
```

Credentials are resolved only at request time and never appear in results,
logs, CLI output, API responses, or SQLite.  `MockAuthorizedApiConnector`
provides a clearly labelled **SIMULATED / MOCK** authorized-API path for
demos and tests (scenarios: success / unchanged / changed / unauthorized /
transient_failure).

**Real hospital API/feed integrations require an authorized source
configuration and credentials and are not included by default.**

## Search & field comparison (P22)

Structured search and side-by-side field comparison over **already stored**
canonical datasets — no re-crawl, no ranking, no winner.

```bash
# free text + field filters (deterministic operators)
.venv\Scripts\hospital-extractor search --text "cardiology"
.venv\Scripts\hospital-extractor search \
  --filter hospital.bed_count gte 500 \
  --filter contacts.city equals Delhi

# field-by-field comparison of 2–4 stored hospitals (UNKNOWN stays UNKNOWN)
.venv\Scripts\hospital-extractor search \
  --compare h_abc h_def \
  --fields hospital.name,hospital.bed_count,contacts.city

# JSON for tooling
.venv\Scripts\hospital-extractor search --filter hospital.name contains Max --json
```

Filter operators: `equals`, `not_equals`, `contains`, `not_contains`,
`exists`, `not_exists`, `in`, `not_in`, `gt`, `gte`, `lt`, `lte`, `after`,
`before`.  A missing field is **UNKNOWN** — it never satisfies `equals` /
`not_equals` (never invents a NO), and comparison cells render `UNKNOWN`
explicitly.  Every match and cell keeps hospital identity, source type,
provider, capture time and per-field evidence.

API / UI:

- `POST /api/v1/search` — body: `{filters, hospital_ids, text, limit, offset}`
- `POST /api/v1/search/compare` — body: `{hospital_ids: [2–4], fields: [...]}`
- `GET/POST /search` — search form + stored-hospital multi-select compare

Field paths use dotted canonical sections: `hospital.name`,
`hospital.bed_count`, `contacts.city`, `specialities.name`,
`infrastructure.total_beds`, `source.source_type`, `quality.records_extracted`.
Unknown paths are rejected with HTTP 400 / CLI exit 1 (`InvalidQueryError`).

## Data quality & observability (P23)

Independent quality metrics computed from **stored rows only** — datasets,
snapshots, change sets, verification jobs, reconciliation cases, monitoring
policies, reports, and orchestration runs.  There is intentionally **no**
opaque overall score: each metric carries its own definition so reviewers can
audit exactly what was counted.

```bash
# whole-store pipeline report (JSON)
.venv\Scripts\hospital-extractor quality-report --scope pipeline

# one hospital, human-readable
.venv\Scripts\hospital-extractor quality-report \
  --scope hospital --id h_abc --format markdown

# one stored dataset
.venv\Scripts\hospital-extractor quality-report \
  --scope dataset --id ds_xyz --format json
```

Metric dimensions (all independent — never rolled into one number):

| Dimension | Examples |
| --- | --- |
| Field coverage | hospital field coverage rate, collection coverage, empty categories |
| Provenance | dataset/hospital source_url flags, entity provenance rate |
| Unknown rate | unknown field count/rate (UNKNOWN ≠ NO) |
| Conflict | conflicts detected/unresolved, conflict rate |
| Duplicate | duplicates removed, duplicate rate |
| Extraction / crawl | records extracted/rejected, pages crawled, crawl success rate |
| Source health | source type, authorization status (PUBLIC / AUTHORIZED / SIMULATED) |
| Freshness | age since capture (days) |
| Verification | job counts + status mix |
| Reconciliation | case counts + status mix |
| Monitoring | policy counts + status mix |
| Change frequency | change sets, changes detected, span days, rate/day |
| Snapshot health | snapshot counts, source/hospital coverage |
| Pipeline | orchestration runs, reports, success/failure counters |

API / UI:

- `GET /api/v1/quality/pipeline` — `PipelineQualityReport`
- `GET /api/v1/quality/hospitals/{id}` — `HospitalQualityReport` (404 unknown)
- `GET /api/v1/quality/hospitals/{id}/sources` — `list[SourceQualityReport]`
- `GET /api/v1/quality/datasets/{id}` — `DatasetQualityReport` (404 unknown)
- `GET /api/v1/quality/format/{scope}?id=…&format=markdown|json`
- `GET /quality` — pipeline overview page
- Profile page gains a **Quality metrics (P23)** panel (dataset + hospital metrics)

`overall_score` is always `null` on every report type.  Reports never invent
counts: empty stores yield zeroed metrics with definitions, and null-denominator
rates stay `null` (or are documented as 0.0 only where the definition says so).

## Security, RBAC & audit (P24)

Two independent authorization layers, both fail-closed:

1. **P24 user authorization** — may *this authenticated actor* call this API?
2. **P21 source authorization** — may *this application* contact that external
   source?  An ADMIN user does **not** bypass a P21 `UNAUTHORIZED` source; the
   probe still returns 401 with `fetch_allowed: false` and performs **zero**
   outbound I/O.

### Auth mode (IMPLEMENTED / DEVELOPMENT-TEST ONLY / REQUIRES PRODUCTION CONFIGURATION)

| Status | Meaning |
| --- | --- |
| **IMPLEMENTED** | Bearer-token login/logout, scrypt password hashing, SHA-256 token digests, RBAC roles + granular permissions, resource-level hospital grants, append-only audit log, 401/403, sanitized errors, request-ID correlation |
| **DEVELOPMENT-TEST ONLY** | Default `require_auth=false` development bypass (synthetic ADMIN labeled `development_bypass`); optional `X-Medcompare-Dev-User` header (never honored when `MEDCOMPARE_AUTH_MODE=production`) |
| **REQUIRES PRODUCTION CONFIGURATION** | `MEDCOMPARE_AUTH_MODE=production` (implies fail-closed `require_auth`), `MEDCOMPARE_BOOTSTRAP_ADMIN_PASSWORD` / `MEDCOMPARE_BOOTSTRAP_ADMIN_USERNAME` (env only — no hard-coded admin password), optional `MEDCOMPARE_TRUSTED_HOSTS`, `MEDCOMPARE_CORS_ORIGINS` |
| **FUTURE** | SSO / MFA / social login / external IdP — **not implemented** |

Roles (closed set): `admin`, `operator`, `reviewer`, `viewer`.

| Permission | admin | operator | reviewer | viewer |
| --- | :---: | :---: | :---: | :---: |
| `view_hospital`, `view_quality` | ✓ | ✓ | ✓ | ✓ |
| `capture_source` | ✓ | ✓ | | |
| `manage_monitoring` | ✓ | ✓ | | |
| `verify_change` | ✓ | ✓ | ✓ | |
| `review_reconciliation` | ✓ | | ✓ | |
| `generate_report` | ✓ | ✓ | ✓ | |
| `manage_sources`, `manage_configuration`, `submit_report`, `manage_users` | ✓ | | | |

Resource isolation: ADMIN may access any `hospital_id`; other roles need an
explicit grant (settable on user create/update).  Access is always checked by
canonical ID — never by hospital name — so swapping IDs fails with 403.

### Audit events (append-only)

Actions include `auth.login` (success **and** failure), `auth.logout`,
`user.create`, `user.update`, `hospital.capture`, `hospital.import`,
`hospital.delete`, `hospital.access_denied`, `monitoring.create|enable|disable|run`,
`verification.create`, `reconciliation.review|resolve|dismiss|reopen`,
`report.generate`, `report.submit`.  Metadata is scrubbed of password/token/
secret keys before insert; the log never stores passwords, raw tokens, headers,
or stack traces.

### Security notes

- **Single auth mechanism**: opaque bearer token (returned once at login).
  The server stores only a SHA-256 digest. No session cookie dual-path.
- **No plaintext passwords**: scrypt + per-password salt + `hmac.compare_digest`.
- **Sanitized errors**: unhandled exceptions return
  `{"detail": "Internal server error"}` — never stack traces, paths, SQL, or secrets.
- **Login rate limit**: in-memory sliding window (default 10/min per client).
- **Trusted hosts**: `localhost`, `127.0.0.1`, `testserver` by default
  (`MEDCOMPARE_TRUSTED_HOSTS` comma-separated).
- **CORS**: disabled by default; enable only via `MEDCOMPARE_CORS_ORIGINS`.
- **`GET /api/v1/health`** stays unauthenticated (readiness).
- **UI**: hiding a button is not security — every mutating endpoint re-checks
  permission server-side and writes an audit event.

```bash
# force fail-closed auth locally (or set MEDCOMPARE_REQUIRE_AUTH=1)
# then: login, call APIs with Authorization: Bearer <token>
```

CLI commands do not print passwords, tokens, or credential env values.

## Reconciliation workflow (P19-C)

A verification verdict that needs human judgement becomes a
**ReconciliationCase** — one case per verification result, auto-created while a
job completes (idempotent).  Cases are pure database objects: they *reference*
the existing ChangeSet, VerificationJob, VerificationResult and the immutable
snapshots, and they never write anything back to any hospital source.

Automatic creation policy:

| Verification status | Case created? |
| --- | --- |
| `MISMATCH` | always |
| `UNVERIFIED` | only when `RECONCILIATION_CREATE_UNVERIFIED_CASES=1` (or opt-in settings) |
| `VERIFIED` / `FAILED` | never (an explicit manual request can still open one) |

Each case owns field-level **items** (same path / entity / field used by change
detection) and moves through a small state machine:

```
OPEN ──► UNDER_REVIEW ──► RESOLVED ──► REOPENED ──► UNDER_REVIEW / RESOLVED / DISMISSED
  │            │            │
  └─► RESOLVED/DISMISSED    └────► REOPENED (re-opening never creates a new case)
```

Every transition records an audit event (actor, action, reason, from/to state).
Resolving records one of five decisions *about the evidence* — never a claim of
"which source is truth": `authoritative_source_confirmed`,
`website_confirmed`, `source_disagreement`, `insufficient_evidence`,
`false_positive`.

```bash
# list one hospital's cases, show a case with evidence + audit trail
.venv\Scripts\hospital-extractor reconciliation-cases demo-solstice
.venv\Scripts\hospital-extractor reconciliation rec_… --db data/medcompare.db

# advance a case
.venv\Scripts\hospital-extractor review   rec_… --reason "Picking this up"
.venv\Scripts\hospital-extractor resolve  rec_… --resolution source_disagreement --reason "…"
.venv\Scripts\hospital-extractor dismiss  rec_… --reason "Not actionable"
.venv\Scripts\hospital-extractor reopen   rec_… --reason "New evidence"

# full SIMULATED walkthrough (A verified→no case, B mismatch→resolved, C unverified→open)
.venv\Scripts\hospital-extractor verification-demo
```

The web UI renders both a per-hospital "Reconciliation" panel on the profile
page and a full case review screen at `/reconciliation-cases/{case_id}` with a
server-side action form (no JavaScript).  JSON endpoints mirror every action.

## Decision semantics (YES / NO / UNKNOWN)

Boolean intake fields are resolved from weighted evidence:

- **YES** — net positive evidence reaches the decision threshold.
- **NO** — net negative evidence reaches the threshold (e.g. an explicit
  statement that the service is not offered / not available).
- **UNKNOWN** — the site explicitly says nothing, or the evidence is weak or
  balanced. **Absence of a mention never means NO.**

The comparison table, the web UI, and the master CSV all distinguish UNKNOWN
from a verified NO. A dimension with contradictory real evidence (a `yes` from
one source and a `no` from another) is surfaced as a **conflict** — the
platform never silently picks a winner.

## Evidence and provenance

Every extracted record carries its source URL, title, extraction method and
context text. The 25 master columns stay clean; the accompanying
`master_quality.md` keeps the full provenance trail, quality counters,
warnings, conflicts and category coverage for the run.

## What it extracts

Eleven canonical categories feed the 25-field intake:

- identity & contacts: hospital, contacts, infrastructure
- services: facilities, departments, specialities, doctors
- clinical: treatments, procedures, technologies
- credentials: accreditations

The intake (`src/hospital_extractor/schema/intake.py`) is the single output
contract — one row per hospital with text/number/dropdown/boolean fields.

## Architecture

- `src/hospital_extractor/crawler/` — fetch, classify (URL + content signals), crawl loop
- `src/hospital_extractor/extraction/` — per-category extractors plus `engine.build_dataset`
- `src/hospital_extractor/processing/` — normalize, deduplicate, validate, conflict detection
- `src/hospital_extractor/schema/` — canonical categories, field mapping, intake schema, quality
- `src/hospital_extractor/export/` — master CSV/XLSX intake outputs + quality/provenance report
- `src/hospital_extractor/comparison/` — multi-hospital procedure/department comparison
- `src/hospital_extractor/api/` — FastAPI application + web frontend
- `src/hospital_extractor/models/` — pydantic records, dataset container, intake model
- `src/hospital_extractor/sources/` — source adapters + P21 authorization
  boundary (website/mock_api/hospital_api/json_feed, fail-closed API policy)
- `src/hospital_extractor/search/` — P22 query models, field filters, search + stored field comparison
- `src/hospital_extractor/api/search.py` — P22 search / field-compare router and UI
- `src/hospital_extractor/quality/` — P23 typed quality reports, metrics service, JSON/Markdown formatters
- `src/hospital_extractor/api/quality.py` — P23 quality JSON/Markdown router, `/quality` UI, profile panel helper
- `src/hospital_extractor/auth/` — P24 principals, RBAC map, scrypt hashing, tokens, audit store, dependencies
- `src/hospital_extractor/api/auth.py` — P24 login/logout/me, user management, audit router
- `src/hospital_extractor/ops/` — P25 startup verification + worker signal handling
- `src/hospital_extractor/api/ops.py` — P25 `/health` liveness + `/ready` readiness
- `Dockerfile` / `.dockerignore` — P25 non-root Python 3.13 image (API default; worker override)
- `docs/deployment.md` — P25 operator guide (env vars, Docker, SQLite, IMPLEMENTED vs NOT CLAIMED)
- `src/hospital_extractor/storage/` — raw cache, JSON store, SQLite hospital database
- `src/hospital_extractor/verification/` — verification jobs, scheduler, worker, evaluation
- `src/hospital_extractor/reconciliation/` — reviewable cases, evidence, state machine
- `src/hospital_extractor/exporters/` — government portal exporter (simulated)
- `src/hospital_extractor/mockapi/` — simulated demo hospital API

See `docs/architecture.md` for the source-agnostic design, provenance contract,
and conflict handling.

## Known limitations

- Not every hospital website is crawlable; sites that block automated access
  return HTTP 403 and are skipped, never bypassed.
- Extraction quality depends on the markup the site exposes; these fields are
  inherently heuristic and are marked UNKNOWN when unsupported or absent.
- The tool targets hospital websites; generic marketing/cloud vendors may not
  populate all categories.
- **Real hospital API/feed integrations require an authorized source
  configuration and credentials and are not included by default.**  Without
  `authorized = true` + allowlisted host (+ credential env when required),
  API/remote-feed connectors fail closed with no outbound request.  The mock
  authorized connector is SIMULATED / MOCK only.

## Development

```bash
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check src tests
.venv\Scripts\python -m mypy src
```