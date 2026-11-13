# Database Design (Milestone 1)

## Overview

Milestone 1 introduces the PostgreSQL persistence foundation for the MedCompare hospital registry. The registry is the authoritative source of hospital identity for future extraction runs, datasets, permissions, and recommendations.

- **Database:** PostgreSQL 16
- **ORM:** SQLAlchemy 2.x (synchronous)
- **Migrations:** Alembic (authoritative schema history; no automatic `create_all()` at runtime)
- **Driver:** Psycopg 3 (`psycopg[binary]`)
- **Identity:** Python-generated UUIDs (`uuid.uuid4()`), immutable for facilities

Persistence code lives in `src/hospital_extractor/persistence/` and is opt-in. Existing crawler/extraction/processing/recommendation domain packages and the CLI do not import SQLAlchemy and do not require database configuration.

## Schema

### `hospital_organizations`

Parent brand/organization (e.g. Apollo Hospitals). Not a physical facility.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | Python-generated |
| `name` | varchar(255), not null | Display name |
| `normalized_name` | varchar(255), not null, indexed | Lowercased/collapsed for search |
| `website_url` | text, nullable | Organization website (not identity) |
| `status` | varchar(32), not null, indexed | `active` / `inactive` (soft state) |
| `created_at` | timestamptz, not null | server/default UTC-aware |
| `updated_at` | timestamptz, not null | updated in service layer / ORM |

Organization names are **not** globally unique.

### `hospital_facilities`

One physical hospital facility. This is the primary identity unit.

| Field | Type | Notes |
|---|---|---|
| `id` | UUID PK | Immutable, Python-generated |
| `organization_id` | UUID FK -> `hospital_organizations.id`, nullable, indexed, `ON DELETE RESTRICT` | Parent org; facility may exist without known org |
| `name` | varchar(255), not null | Official facility name |
| `normalized_name` | varchar(255), not null, indexed | Search key |
| `canonical_url` | text, nullable | Facility-specific canonical URL as provided |
| `normalized_canonical_url` | text, nullable, **unique when present** | Deterministically normalized; duplicate => conflict |
| `website_domain` | varchar(255), nullable, indexed | Extracted host; **not unique** |
| `address` | text, nullable |  |
| `city` | varchar(128), nullable | indexed with state |
| `state` | varchar(128), nullable |  |
| `country` | varchar(128), nullable |  |
| `postal_code` | varchar(32), nullable |  |
| `latitude` | numeric(10,7), nullable |  |
| `longitude` | numeric(10,7), nullable |  |
| `status` | varchar(32), not null, indexed | `active` / `inactive` (soft deactivation) |
| `created_at` | timestamptz, not null |  |
| `updated_at` | timestamptz, not null |  |

**Constraints/indexes:** unique `normalized_canonical_url` (`uq_hospital_facilities_normalized_canonical_url`), FK `organization_id`, indexes on org/name, city/state, website_domain, status, normalized_name. No unique constraint on name or `website_domain`.

## Relationships (ER diagram)

```mermaid
erDiagram
    HOSPITAL_ORGANIZATIONS ||--o{ HOSPITAL_FACILITIES : "owns (optional)"
    HOSPITAL_ORGANIZATIONS {
        uuid id PK
        string name
        string normalized_name
        text website_url
        string status
        timestamptz created_at
        timestamptz updated_at
    }
    HOSPITAL_FACILITIES {
        uuid id PK
        uuid organization_id FK "nullable"
        string name
        string normalized_name
        text canonical_url "nullable"
        text normalized_canonical_url "nullable, unique when present"
        string website_domain "nullable, not unique"
        text address
        string city
        string state
        string country
        string postal_code
        numeric latitude
        numeric longitude
        string status
        timestamptz created_at
        timestamptz updated_at
    }
```

## Database configuration

- **`MEDCOMPARE_DATABASE_URL`** (required for DB operations): e.g. `postgresql+psycopg://medcompare:medcompare_dev_only@localhost:5432/medcompare`
- Pooling defaults: `pool_pre_ping=True`, configurable pool size/overflow/timeout/recycle via persistence settings.
- Credentials must not be committed; `.env.example` documents variables.
- The existing CLI does not require DB configuration.

## Migrations

Alembic in `alembic/`, initial revision `0001_hospital_registry`.

```bash
# set MEDCOMPARE_DATABASE_URL, then (use $env:UV_LINK_MODE="copy" on Windows/OneDrive if needed):
uv run --frozen alembic upgrade head
uv run --frozen alembic downgrade base   # disposable test database only
uv run --frozen alembic upgrade head
```

## Local PostgreSQL (Docker, optional for registry dev)

```bash
docker compose up -d postgres
# stop without deleting data volume
docker compose down
```

`docker-compose.yml` provides PostgreSQL 16, persistent named volume `medcompare_postgres_data`, env-based credentials, health check. Docker is **not** required for existing extraction CLI usage.

## Known limitations (Milestone 1)

- No public API endpoints for registry; service/repository layer only.
- No users/auth/sessions/permissions or production `HospitalAuthorizer` binding.
- No dataset/extraction-run persistence, workers, or search/comparison/profile APIs.
- PostgreSQL integration/migration runtime verification depends on an available PostgreSQL instance (`TEST_DATABASE_URL`); SQLite tests do not prove PostgreSQL compatibility.
