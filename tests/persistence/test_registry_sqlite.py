"""SQLite-backed registry tests (fast, isolated). Not proof of PostgreSQL compatibility."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from hospital_extractor.persistence.base import Base
from hospital_extractor.persistence.exceptions import (
    DuplicateCanonicalUrlError,
    FacilityNotFoundError,
    InvalidOrganizationReferenceError,
    OrganizationNotFoundError,
)
from hospital_extractor.persistence.models import FacilityStatus, OrganizationStatus
from hospital_extractor.persistence.registry.service import (
    FacilityCreate,
    FacilityUpdate,
    OrganizationCreate,
    OrganizationUpdate,
    RegistryService,
)


@pytest.fixture()
def sqlite_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def service(sqlite_session: Session) -> RegistryService:
    return RegistryService(sqlite_session)


def test_create_and_get_organization(service: RegistryService) -> None:
    org = service.create_organization(OrganizationCreate(name="Apollo Hospitals", website_url="https://apollohospitals.com"))
    assert isinstance(org.id, uuid.UUID)
    fetched = service.get_organization(org.id)
    assert fetched.name == "Apollo Hospitals"
    assert fetched.normalized_name == "apollo hospitals"
    assert fetched.status == OrganizationStatus.ACTIVE.value


def test_organization_uuid_unique_and_similar_names_allowed(service: RegistryService) -> None:
    a = service.create_organization(OrganizationCreate(name="City Care"))
    b = service.create_organization(OrganizationCreate(name="City Care"))
    assert a.id != b.id
    assert service.get_organization(a.id).name == "City Care"


def test_update_and_deactivate_organization(service: RegistryService) -> None:
    org = service.create_organization(OrganizationCreate(name="Old Name"))
    updated = service.update_organization(org.id, OrganizationUpdate(name="New Name", website_url="https://new.example"))
    assert updated.id == org.id
    assert updated.name == "New Name"
    deactivated = service.deactivate_organization(org.id)
    assert deactivated.status == OrganizationStatus.INACTIVE.value


def test_register_facility_auto_uuid_and_without_org(service: RegistryService) -> None:
    f = service.register_facility(FacilityCreate(name="Standalone Clinic", city="Delhi"))
    assert isinstance(f.id, uuid.UUID)
    assert f.organization_id is None
    assert service.get_facility(f.id).name == "Standalone Clinic"


def test_uuid_immutability_on_update(service: RegistryService) -> None:
    org = service.create_organization(OrganizationCreate(name="Org"))
    f = service.register_facility(FacilityCreate(name="F1", organization_id=org.id, city="Delhi", canonical_url="https://example.com/f1"))
    original = f.id
    updated = service.update_facility(
        f.id,
        FacilityUpdate(
            name="F1 Renamed",
            city="New Delhi",
            set_city=True,
            canonical_url="https://example.com/f1-new",
            set_canonical_url=True,
        ),
    )
    assert updated.id == original
    assert updated.name == "F1 Renamed"
    assert updated.city == "New Delhi"
    assert updated.normalized_canonical_url == "https://example.com/f1-new"


def test_multiple_facilities_under_one_organization(service: RegistryService) -> None:
    org = service.create_organization(OrganizationCreate(name="Apollo Hospitals"))
    d = service.register_facility(
        FacilityCreate(name="Apollo Hospital Delhi", organization_id=org.id, city="Delhi")
    )
    c = service.register_facility(
        FacilityCreate(name="Apollo Hospital Chennai", organization_id=org.id, city="Chennai")
    )
    assert d.id != c.id
    assert d.organization_id == org.id == c.organization_id
    facilities = service.list_facilities(organization_id=org.id)
    assert {f.id for f in facilities} == {d.id, c.id}


def test_same_facility_name_different_cities(service: RegistryService) -> None:
    a = service.register_facility(FacilityCreate(name="City Hospital", city="Delhi"))
    b = service.register_facility(FacilityCreate(name="City Hospital", city="Mumbai"))
    assert a.id != b.id


def test_different_facilities_can_share_domain(service: RegistryService) -> None:
    org = service.create_organization(OrganizationCreate(name="Apollo Hospitals"))
    a = service.register_facility(
        FacilityCreate(name="Apollo Delhi", organization_id=org.id, canonical_url="https://apollohospitals.com/delhi")
    )
    b = service.register_facility(
        FacilityCreate(name="Apollo Chennai", organization_id=org.id, canonical_url="https://apollohospitals.com/chennai")
    )
    assert a.website_domain == b.website_domain == "apollohospitals.com"
    assert a.normalized_canonical_url != b.normalized_canonical_url
    assert a.id != b.id


def test_multiple_facilities_with_null_canonical_url(service: RegistryService) -> None:
    a = service.register_facility(FacilityCreate(name="Branch A", canonical_url=None))
    b = service.register_facility(FacilityCreate(name="Branch B", canonical_url=None))
    assert a.id != b.id
    assert a.normalized_canonical_url is None and b.normalized_canonical_url is None


def test_duplicate_normalized_canonical_url_rejected(service: RegistryService) -> None:
    first = service.register_facility(
        FacilityCreate(name="Delhi", canonical_url="https://Example.com/Delhi/")
    )
    with pytest.raises(DuplicateCanonicalUrlError) as excinfo:
        service.register_facility(
            FacilityCreate(name="Duplicate", canonical_url="https://example.com/Delhi")
        )
    assert excinfo.value.existing_facility_id == first.id
    assert excinfo.value.normalized_canonical_url == "https://example.com/Delhi"


def test_same_domain_different_facility_specific_url_succeeds(service: RegistryService) -> None:
    a = service.register_facility(FacilityCreate(name="A", canonical_url="https://chain.com/branch-a"))
    b = service.register_facility(FacilityCreate(name="B", canonical_url="https://chain.com/branch-b"))
    assert a.id != b.id
    assert a.website_domain == b.website_domain == "chain.com"


def test_assign_and_change_organization(service: RegistryService) -> None:
    org1 = service.create_organization(OrganizationCreate(name="Org1"))
    org2 = service.create_organization(OrganizationCreate(name="Org2"))
    f = service.register_facility(FacilityCreate(name="Facility"))
    updated = service.update_facility(
        f.id, FacilityUpdate(organization_id=org1.id, set_organization_id=True)
    )
    assert updated.organization_id == org1.id
    updated = service.update_facility(
        f.id, FacilityUpdate(organization_id=org2.id, set_organization_id=True)
    )
    assert updated.organization_id == org2.id
    cleared = service.update_facility(
        f.id, FacilityUpdate(organization_id=None, set_organization_id=True)
    )
    assert cleared.organization_id is None


def test_invalid_organization_reference_rejected(service: RegistryService) -> None:
    with pytest.raises(InvalidOrganizationReferenceError):
        service.register_facility(FacilityCreate(name="Bad", organization_id=uuid.uuid4()))
    f = service.register_facility(FacilityCreate(name="Ok"))
    with pytest.raises(InvalidOrganizationReferenceError):
        service.update_facility(
            f.id,
            FacilityUpdate(organization_id=uuid.uuid4(), set_organization_id=True),
        )


def test_soft_deactivation(service: RegistryService) -> None:
    f = service.register_facility(FacilityCreate(name="To Deactivate"))
    deactivated = service.deactivate_facility(f.id)
    assert deactivated.id == f.id
    assert deactivated.status == FacilityStatus.INACTIVE.value
    assert service.get_facility(f.id).status == FacilityStatus.INACTIVE.value


def test_not_found_errors(service: RegistryService) -> None:
    with pytest.raises(OrganizationNotFoundError):
        service.get_organization(uuid.uuid4())
    with pytest.raises(FacilityNotFoundError):
        service.get_facility(uuid.uuid4())


def test_get_facility_by_canonical_url(service: RegistryService) -> None:
    f = service.register_facility(FacilityCreate(name="X", canonical_url="https://ex.com/x/"))
    found = service.get_facility_by_canonical_url("HTTPS://EX.COM/x")
    assert found is not None and found.id == f.id


def test_update_clears_canonical_url(service: RegistryService) -> None:
    f = service.register_facility(FacilityCreate(name="X", canonical_url="https://ex.com/x"))
    updated = service.update_facility(
        f.id, FacilityUpdate(canonical_url=None, set_canonical_url=True)
    )
    assert updated.canonical_url is None
    assert updated.normalized_canonical_url is None
    assert updated.website_domain is None
    # can register same URL again after clear
    other = service.register_facility(FacilityCreate(name="Y", canonical_url="https://ex.com/x"))
    assert other.id != f.id


def test_acceptance_apollo_two_facilities(service: RegistryService) -> None:
    org = service.create_organization(OrganizationCreate(name="Apollo Hospitals"))
    delhi = service.register_facility(
        FacilityCreate(name="Apollo Hospital Delhi", organization_id=org.id, city="Delhi", canonical_url="https://apollohospitals.com/delhi")
    )
    chennai = service.register_facility(
        FacilityCreate(name="Apollo Hospital Chennai", organization_id=org.id, city="Chennai", canonical_url="https://apollohospitals.com/chennai")
    )
    assert delhi.id != chennai.id
    assert delhi.organization_id == org.id and chennai.organization_id == org.id
    assert service.get_facility(delhi.id).city == "Delhi"
    assert service.get_facility(chennai.id).city == "Chennai"

    # independent metadata updates
    service.update_facility(delhi.id, FacilityUpdate(address="Delhi Address", set_address=True))
    service.update_facility(chennai.id, FacilityUpdate(address="Chennai Address", set_address=True))
    assert service.get_facility(delhi.id).address == "Delhi Address"
    assert service.get_facility(chennai.id).address == "Chennai Address"

    # same domain, different URLs - separate
    assert delhi.website_domain == chennai.website_domain == "apollohospitals.com"

    # duplicate normalized URL rejected
    with pytest.raises(DuplicateCanonicalUrlError):
        service.register_facility(FacilityCreate(name="Dup", canonical_url="HTTPS://apollohospitals.com/delhi/"))

    # second facility same domain different URL succeeds (chennai already proves it)
    mumbai = service.register_facility(
        FacilityCreate(name="Apollo Hospital Mumbai", organization_id=org.id, canonical_url="https://apollohospitals.com/mumbai")
    )
    assert mumbai.id not in {delhi.id, chennai.id}

    # facility without canonical URL succeeds
    no_url = service.register_facility(
        FacilityCreate(name="Apollo Hospital Unknown", organization_id=org.id)
    )
    assert no_url.canonical_url is None
    assert no_url.id != delhi.id
