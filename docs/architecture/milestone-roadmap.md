# Milestone Roadmap

Roadmap for evolving this repository into the complete MedCompare internal application. Milestone 0 is scaffolding/documentation/baseline only.

## Milestone 0 — Foundation stabilization and application scaffolding (current)

**Scope**
- Inspect/preserve existing work; verify recommendation and tri-state changes; record baseline validation.
- Document agreed target architecture, domain boundaries, and roadmap.
- Add minimal FastAPI scaffold: app factory, health router, `GET /api/v1/health`, env-aware settings, TestClient tests; no DB or fake domain endpoints.
- Add minimal React + TypeScript + Vite frontend scaffold: strict TS, app shell, env-based API base URL, production build; no fake hospital/recommendation data.
- Preserve existing CLI, extraction, intake/tri-state, export, and recommendation behavior.

**Acceptance criteria**
- Baseline and post-change `uv run --frozen pytest -q`, `ruff check .`, `mypy src`, `git diff --check` pass.
- FastAPI health endpoint returns healthy response without database dependency.
- Frontend installs and builds for production.
- CLI remains runnable; `hospital_extractor.recommendation` imports successfully.
- No circular imports introduced into domain packages.
- No PostgreSQL tables/migrations, registry/auth/recommendation APIs, jobs, dataset persistence, or full UI implemented.

## Milestone 1 — Persistence foundation and hospital registry

**Scope**
- PostgreSQL integration, SQLAlchemy models/repositories, Alembic migrations.
- `HospitalOrganization` and `HospitalFacility` registry (facility immutable UUID, organization ownership, canonical URL/domain/slug hints, location metadata, status).
- Idempotent/conflict-safe facility registration rules; no merging distinct facilities by shared brand/domain.

**Acceptance criteria**
- Migrations create registry schema; facility UUIDs are immutable.
- Registry service/repository tests cover create/read/list uniqueness and organization-facility relationships.
- Existing extraction/recommendation/CLI behavior remains unchanged without requiring DB for current CLI flows.

## Milestone 2 — Authentication and authorization

**Scope**
- Email/password users, Argon2id password hashing, server-side sessions, secure HttpOnly/SameSite cookies, CSRF protection for cookie-authenticated state-changing requests.
- Admin-controlled user creation/invitation; facility access grants with default deny.
- DB-backed implementation of `hospital_extractor.recommendation.HospitalAuthorizer` (`scope_for(user_id)`).
- API auth dependencies and protected route patterns.

**Acceptance criteria**
- Login/session lifecycle and CSRF protections covered by tests.
- `scope_for` returns only granted facility IDs; no grants => deny.
- Recommendation integration preserves existing exclusion semantics for unauthorized candidates.
- Domain recommendation package remains framework/ORM independent.

## Milestone 3 — Dataset persistence and extraction jobs

**Scope**
- Extraction run/job records in PostgreSQL; separate worker process claiming jobs.
- Persist immutable dataset versions, intake projections, quality reports, provenance/sources, crawl/run metadata, and active version pointer per facility.
- Local artifact storage abstraction for raw/export artifacts.

**Acceptance criteria**
- Job lifecycle (queued/running/succeeded/failed) is durable and auditable.
- Stored versions round-trip without losing provenance, conflicts, evidence, tri-state values, or export-policy semantics.
- Worker reuses existing crawl/extract/intake/export pipeline; failed runs do not corrupt prior active versions.
- API enqueues jobs; extraction does not block request threads/processes.

## Milestone 4 — Recommendation API integration

**Scope**
- FastAPI recommendation endpoint(s) aligned with existing `PreferenceRequest`/`RecommendationResponse` contracts.
- Load authorized facility candidates and active dataset versions from persistence; build `HospitalCandidate` inputs.
- Inject DB-backed `HospitalAuthorizer`; server derives authenticated user identity.

**Acceptance criteria**
- API responses are deterministic and semantically equivalent to direct `RecommendationService` calls for equivalent inputs.
- `selected`/`discover` modes respect authorization and facility boundaries.
- Unsupported/clarification/unknown handling, mandatory `unknown` never satisfied, ranking, coverage, confidence, and `scoring_available` semantics unchanged.

## Milestone 5 — Search, profiles, and comparison APIs

**Scope**
- Facility search/filter projections, profile/detail endpoints, version history access, and comparison endpoint.
- Expose intake, quality, conflicts, provenance, and category summaries without hiding unknown/missing states.

**Acceptance criteria**
- Search returns only authorized facilities with pagination and documented filters.
- Profiles include registry metadata, active dataset/intake/quality/source summaries, and explicit version access.
- Comparison is side-by-side and preserves tri-state/provenance/conflict visibility.
- Query performance acceptable for internal scale (indexes/projections as needed).

## Milestone 6 — Frontend application

**Scope**
- React UI: login/session handling, facility registry views, extraction run management, profiles, search, comparison, and recommendation workflow.
- Faithful rendering of support states, clarification prompts, evidence coverage/confidence, exclusions, warnings, and empty/insufficient states.

**Acceptance criteria**
- End-to-end user flows function against API for authorized internal users.
- No fabricated data/results; no client-side scoring or authorization decisions.
- Accessibility/usability baseline for internal operations.

## Milestone 7 — Hardening and operations

**Scope**
- Deployment configuration, health/readiness, structured logging/metrics, backup/restore, retention policies, security review, rate/concurrency controls, and worker reliability improvements.
- Optional artifact-store backend replacement behind abstraction.

**Acceptance criteria**
- Reproducible deployment of API/worker/frontend/PostgreSQL.
- Backup/restore verified for registry and versioned datasets.
- Operational runbooks and monitoring for extraction jobs and API health.
- Security checks for sessions, CSRF, authorization boundaries, and secret/config handling.
