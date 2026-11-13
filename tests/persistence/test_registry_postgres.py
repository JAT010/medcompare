"""PostgreSQL integration tests for hospital registry.

Run against an actual PostgreSQL instance via TEST_DATABASE_URL.
Skipped (and reported as unverified) when PostgreSQL is unavailable.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from hospital_extractor.persistence.base import Base
from hospital_extractor.persistence.exceptions import DuplicateCanonicalUrlError
from hospital_extractor.persistence.models import HospitalFacility, HospitalOrganization
from hospital_extractor.persistence.registry.service import (
    FacilityCreate,
    OrganizationCreate,
    RegistryService,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("MEDCOMPARE_TEST_DATABASE_URL")


def _pg_available(url: str | None) -> bool:
    if not url:
        return False
    if not (url.startswith("postgresql") or url.startswith("postgres")):
        return False
    try:
        engine = create_engine(url, pool_pre_ping=True, future=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


PG_AVAILABLE = _pg_available(TEST_DATABASE_URL)

pytestmark = pytest.mark.skipif(
    not PG_AVAILABLE,
    reason=(
        "PostgreSQL integration tests not run: set TEST_DATABASE_URL to a reachable "
        "PostgreSQL instance (not verified against PostgreSQL)."
    ),
)


@pytest.fixture(scope="module")
def pg_engine():
    assert TEST_DATABASE_URL
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def pg_session(pg_engine) -> Session:
    Base.metadata.drop_all(pg_engine)
    Base.metadata.create_all(pg_engine)
    SessionLocal = sessionmaker(bind=pg_engine, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(pg_engine)


def test_tables_and_constraints_exist(pg_engine) -> None:
    Base.metadata.drop_all(pg_engine)
    Base.metadata.create_all(pg_engine)
    insp = inspect(pg_engine)
    tables = set(insp.get_table_names())
    assert "hospital_organizations" in tables
    assert "hospital_facilities" in tables
    uq = {c["name"] for c in insp.get_unique_constraints("hospital_facilities")}
    assert "uq_hospital_facilities_normalized_canonical_url" in uq
    fks = insp.get_foreign_keys("hospital_facilities")
    assert any(fk["referred_table"] == "hospital_organizations" for fk in fks)
    Base.metadata.drop_all(pg_engine)


def test_uuid_and_foreign_key_integrity(pg_session: Session) -> None:
    service = RegistryService(pg_session)
    org = service.create_organization(OrganizationCreate(name="Org"))
    assert isinstance(org.id, uuid.UUID)
    f = service.register_facility(FacilityCreate(name="F", organization_id=org.id))
    assert isinstance(f.id, uuid.UUID)
    assert f.organization_id == org.id


def test_unique_normalized_canonical_url_enforced(pg_session: Session) -> None:
    service = RegistryService(pg_session)
    service.register_facility(FacilityCreate(name="A", canonical_url="https://ex.com/a"))
    with pytest.raises(DuplicateCanonicalUrlError):
        service.register_facility(FacilityCreate(name="B", canonical_url="https://ex.com/a/"))


def test_transaction_rollback_on_failure(pg_session: Session) -> None:
    # valid org exists; invalid facility in nested tx must not partially commit
    org = HospitalOrganization(name="Org", normalized_name="org", status="active")
    pg_session.add(org)
    pg_session.commit()
    oid = org.id

    # manual transaction rollback behavior
    session = pg_session
    session.begin_nested() if session.is_active else None
    try:
        bad = HospitalFacility(
            name="Bad",
            normalized_name="bad",
            organization_id=uuid.uuid4(),
            status="active",
        )
        session.add(bad)
        session.flush()
        committed = True
    except IntegrityError:
        session.rollback()
        committed = False
    assert committed is False
    assert session.get(HospitalOrganization, oid) is not None


def test_acceptance_apollo_postgres(pg_session: Session) -> None:
    service = RegistryService(pg_session)
    org = service.create_organization(OrganizationCreate(name="Apollo Hospitals"))
    delhi = service.register_facility(
        FacilityCreate(name="Apollo Hospital Delhi", organization_id=org.id, canonical_url="https://apollohospitals.com/delhi")
    )
    chennai = service.register_facility(
        FacilityCreate(name="Apollo Hospital Chennai", organization_id=org.id, canonical_url="https://apollohospitals.com/chennai")
    )
    assert delhi.id != chennai.id
    assert delhi.organization_id == org.id == chennai.organization_id
    no_url = service.register_facility(
        FacilityCreate(name="Apollo Hospital Unknown", organization_id=org.id)
    )
    assert no_url.canonical_url is None
    with pytest.raises(DuplicateCanonicalUrlError):
        service.register_facility(FacilityCreate(name="Dup", canonical_url="https://apollohospitals.com/delhi"))
    # same domain different URL already succeeded
    assert service.get_facility(delhi.id).id == delhi.id
    assert service.get_facility(chennai.id).id == chennai.id
