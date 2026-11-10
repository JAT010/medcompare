# Target Architecture (Milestone 0)

This repository (`hospital-website-extractor`) is the entire MedCompare project. Target architecture evolves the existing extractor into an internal multi-user application while preserving the current domain behavior.

## Agreed decisions

- **Deployment:** internal multi-user application.
- **Hospital identity:** each physical hospital facility receives its own immutable UUID.
- **Organization model:** `HospitalOrganization` can own multiple `HospitalFacility` records.
- **Authentication:** email/password with secure server-side sessions.
- **Database:** PostgreSQL.
- **Backend:** FastAPI.
- **Frontend:** React + TypeScript + Vite.
- **ORM/migrations:** SQLAlchemy + Alembic.
- **Background extraction:** separate worker process, initially using PostgreSQL-backed job records.
- **Artifact storage:** local filesystem initially, behind a replaceable storage abstraction.
- **Recommendation engine:** reuse existing deterministic `hospital_extractor.recommendation` package without rewriting scoring logic.

## Hospital identity model

- **`HospitalOrganization`**: parent brand/organization entity. May own multiple facilities. Organization identity is not facility identity.
- **`HospitalFacility`**: individual physical hospital location. Every facility has an immutable UUID primary key.
- **No branch merging by brand/domain:** facilities under the same brand or domain remain separate records unless explicitly modeled as the same physical facility by a future, auditable registry workflow.
- **Facility-owned artifacts:** extraction datasets (`HospitalDataset` snapshots), extraction runs, crawl/raw artifact references, intake projections, quality reports, provenance/source records, active dataset version pointers, and recommendation records are associated with a **facility** UUID.
- **Current code mapping:** existing crawl/extraction operates on one target URL and produces one dataset/intake/export set. Future registry/orchestration will bind each extraction run to exactly one `HospitalFacility` and persist versioned outputs.

## Layered application architecture

| Layer | Responsibility | Current/target modules |
|---|---|---|
| Domain | Crawl, extraction, normalization, dedupe/validation, schema/intake/boolean resolution, exports, recommendation scoring/eligibility. Framework-free. | Existing: `crawler`, `extraction`, `processing`, `schema`, `models`, `export`, `storage/raw.py`, `storage/json_store.py`, `utils`, `recommendation`, `config`. |
| Application | Use cases/orchestration: register facilities, trigger extraction jobs, load active datasets, build comparison/profile/search projections, run recommendation service. | Target: `src/hospital_extractor/app/` (future). |
| API | FastAPI app factory, routers, request/response schemas, auth/session dependencies, error mapping, serialization. No domain scoring logic. | Target: `src/hospital_extractor/api/` (Milestone 0 scaffold: health only). |
| Persistence | PostgreSQL models, repositories, transactions, Alembic migrations, DB-backed jobs/sessions/grants. | Target: `src/hospital_extractor/persistence/` (future). |
| Worker | Background extraction execution consuming PostgreSQL-backed job records; calls existing crawl/extract/intake/export pipeline; stores artifacts and dataset versions. | Target: worker entrypoint/process (future). |
| Frontend | React SPA for internal users: auth UI, registry, profiles, search, comparison, extraction runs, recommendations. | Target: `frontend/` (Milestone 0 scaffold only). |

## Runtime topology (target)

- **API process:** FastAPI/Uvicorn, handles authenticated HTTP requests and enqueues extraction jobs.
- **Worker process:** separate process, polls/claims PostgreSQL job records, runs existing async extraction pipeline, writes artifacts and dataset versions.
- **Frontend:** Vite-built React app served separately (dev server/build artifact) calling API.
- **PostgreSQL:** system of record for users/sessions/grants, organizations/facilities, extraction runs/jobs, dataset version metadata/pointers, and recommendation/audit records as needed.
- **Artifact store:** local filesystem initially (`data/` and configured artifact root) behind an abstraction so object storage can replace it later without changing domain extraction logic.

## Integration boundaries

- **Recommendation boundary:** `hospital_extractor.recommendation.HospitalAuthorizer.scope_for(user_id)` remains the integration contract. Production implementation will be DB-backed and fail closed; `DenyAllAuthorizer` remains default/test-safe behavior.
- **Extraction boundary:** existing `run_crawl`, `build_dataset`, `analyze_pages`, `project_intake`, exporters, and raw/json stores are reused as-is. API/worker orchestrate; they do not reimplement extraction.
- **Storage boundary:** JSON dataset snapshots and export formats remain compatible; DB stores version metadata and may store structured snapshots/refs, but must preserve provenance, quality, conflicts, evidence, and tri-state intake semantics.
- **CLI boundary:** existing Python CLI remains usable without requiring PostgreSQL, FastAPI, or frontend runtime dependencies.

## Preserved invariants

- Existing crawler behavior and safety limits.
- `HospitalDataset` schema and provenance fields.
- Extraction/normalization/dedup/validation/conflict pipeline behavior.
- Intake projection and tri-state boolean behavior (`boolean_state`, missing => `unknown`).
- `UNKNOWN_KEEP` default and legacy `UNKNOWN_FALSE` compatibility/export reporting.
- CSV/XLSX export semantics.
- Recommendation scoring, ranking, mandatory eligibility, support/evidence states, coverage, confidence, and authorization protocol.
