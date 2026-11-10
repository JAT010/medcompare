# Domain Boundaries

This document defines module boundaries for MedCompare so new application scaffolding does not contaminate the existing extraction and recommendation domain.

## 1. Domain layer (framework-free, preserved)

**Packages/modules:** `src/hospital_extractor/crawler`, `extraction`, `processing`, `schema`, `models`, `export`, `storage/raw.py`, `storage/json_store.py`, `utils`, `recommendation`, `config`, `cli.py` (CLI remains a thin orchestrator over domain behavior).

**Responsibilities**
- Discover/crawl hospital pages with existing safety limits and relevance controls.
- Extract and normalize structured entities into `HospitalDataset` with provenance, quality, conflicts, and source records.
- Project the 25-field `MasterIntake` and resolve booleans with weighted evidence; preserve explicit tri-state semantics (`yes/no/unknown`, missing => `unknown`).
- Export JSON/CSV/XLSX/quality outputs with `UNKNOWN_KEEP` default and explicit legacy `UNKNOWN_FALSE` compatibility.
- Provide deterministic recommendation validation, authorization boundary protocol, eligibility, scoring, ranking, coverage, confidence, and explanations.

**Rules**
- Must not import FastAPI, Starlette, SQLAlchemy, Alembic, React/frontend tooling, or worker queue frameworks.
- Must not depend on PostgreSQL, HTTP sessions, cookies, or web request objects.
- Must not change scoring formulas, extraction behavior, crawler safety limits, schema/provenance semantics, or tri-state/export behavior for scaffolding.
- Existing CLI must remain usable without PostgreSQL, FastAPI, or frontend runtime dependencies.

## 2. Application layer (future orchestration)

**Planned location:** `src/hospital_extractor/app/`

**Responsibilities**
- Use cases: facility registration, extraction job submission, dataset/version activation, profile/search/comparison projections, recommendation execution.
- Load authorized candidates/active datasets and call domain services (`run_crawl`, `build_dataset`, `project_intake`, exporters, `RecommendationService`).
- Enforce transaction boundaries and map persistence records to domain inputs/outputs.

**Rules**
- May depend on domain and persistence abstractions; must not contain HTTP/framework-specific request parsing.
- Must preserve domain semantics (especially unknown vs false, provenance/conflicts, recommendation support/evidence states).

## 3. API layer (FastAPI)

**Location:** `src/hospital_extractor/api/` (Milestone 0 scaffold only).

**Responsibilities**
- Application factory and router composition.
- Environment-aware API settings.
- Request validation, authentication/session dependencies, CSRF enforcement for state-changing cookie-authenticated requests (future), serialization, and HTTP error mapping.
- Milestone 0 endpoint: `GET /api/v1/health` only; no database dependency.

**Rules**
- May import application/domain interfaces as needed for thin orchestration, but domain packages must never import API packages.
- Must not implement fake auth, registry, or recommendation endpoints in Milestone 0.
- Must not embed extraction or recommendation business logic; domain packages remain authoritative.

## 4. Persistence layer (future)

**Planned location:** `src/hospital_extractor/persistence/`

**Responsibilities**
- PostgreSQL engine/session management, SQLAlchemy ORM models, repositories, Alembic migrations.
- Tables/records for users, sessions/grants, organizations/facilities, extraction runs/jobs, dataset/intake/quality/provenance versions, active version pointers, and audit records as required.
- DB-backed implementation of `HospitalAuthorizer` (`scope_for`) with default-deny behavior.

**Rules**
- Owns DB concerns only; must not reimplement crawler/extraction/scoring logic.
- Facility UUID is the immutable join key for facility-owned extraction/recommendation artifacts.
- Must preserve version immutability and tri-state/provenance fidelity when storing snapshots.

## 5. Worker layer (future)

**Planned location:** dedicated worker module/process entrypoint.

**Responsibilities**
- Poll/claim PostgreSQL-backed extraction job records in a separate process.
- Execute existing pipeline (`run_crawl` -> `build_dataset` -> `project_intake` -> exports) and persist artifacts/versions/status.
- Handle retries/failure recording without corrupting prior active dataset versions.

**Rules**
- No FastAPI request handling in worker process.
- Reuse domain pipeline; do not fork extraction logic.

## 6. Frontend layer

**Location:** `frontend/` (Milestone 0 scaffold only).

**Responsibilities**
- React + TypeScript + Vite internal UI shell, environment-based API base URL configuration, production build.
- Future: authenticated views for registry, profiles/search/comparison, extraction runs, and recommendation workflows.

**Rules**
- No fabricated hospital data or fake recommendation results.
- No client-side authorization or scoring authority; backend/domain remain authoritative.
- Frontend build/runtime is isolated from Python domain package imports.

## 7. Authentication and authorization boundary (intended)

- **Authentication (future):** email/password, Argon2id password hashing, server-side sessions, secure HttpOnly/SameSite cookies, CSRF protection for cookie-authenticated state-changing requests, admin-controlled user creation/invitation.
- **Authorization (future):** backend-enforced, default-deny facility access; grants scoped to facilities (UUID).
- **Recommendation integration:** existing `HospitalAuthorizer` interface remains the boundary; production DB authorizer will provide `AuthorizationScope` without changing recommendation scoring/ranking/exclusion behavior.

## Dependency direction (target)

```text
frontend -> API (HTTP only)
API -> application -> domain
API -> persistence
worker -> application/domain + persistence + artifact storage
domain -> domain only (stdlib + existing domain deps)
```

No dependency from domain packages to API, persistence/ORM, worker framework, or frontend.
