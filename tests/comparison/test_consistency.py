"""Tests for the database-to-website consistency audit engine and endpoint."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from hospital_extractor.api import create_app
from hospital_extractor.api.deps import get_db_session
from hospital_extractor.comparison.schemas import EvidenceItem
from hospital_extractor.consistency.service import (
    INTERNAL_SOURCE_NAME,
    build_audit,
)
from hospital_extractor.persistence.base import Base
from hospital_extractor.persistence.crawl.service import CrawlPersistenceService
from hospital_extractor.persistence.models_crawl import CrawlRunStatus
from hospital_extractor.persistence.registry.service import (
    FacilityCreate,
    OrganizationCreate,
    RegistryService,
)

NOW = datetime(2026, 11, 15, tzinfo=UTC)

FACILITY_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()


def _facility(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": FACILITY_ID,
        "name": "Northgate General Hospital",
        "organization": SimpleNamespace(name="Northgate Health Group"),
        "canonical_url": "https://northgate.example/branch-main",
        "address": "12 Sector 12",
        "city": "Northbridge",
        "state": "Northbridge State",
        "postal_code": "411001",
        "country": "Testland",
        "updated_at": NOW,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _run(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": RUN_ID,
        "status": "completed",
        "target_url": "https://northgate.example/branch-main",
        "requested_url": "https://northgate.example/branch-main",
        "canonical_url": "https://northgate.example/branch-main",
        "hospital_name": "Northgate General Hospital",
        "pages_fetched": 4,
        "usable_for_comparison": 1,
        "requested_at": NOW - timedelta(hours=2),
        "completed_at": NOW - timedelta(hours=1),
        "retrieved_at": NOW - timedelta(hours=1),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _location_evidence(value: str = "Northbridge 411001") -> EvidenceItem:
    return EvidenceItem(
        criterion_key="location",
        value=value,
        source="website_crawl",
        source_name="Hospital website (crawled)",
        source_url="https://northgate.example/branch-main",
        retrieved_at=NOW - timedelta(hours=1),
        status="available",
        confidence=0.8,
        note="Extracted from crawled pages.",
    )


def _finding(response, key: str):
    return next(finding for finding in response.findings if finding.field_key == key)


def test_identical_identity_is_consistent_with_provenance() -> None:
    response = build_audit(_facility(), _run(), [_location_evidence()])

    name = _finding(response, "facility_name")
    url = _finding(response, "website_url")
    location = _finding(response, "location")

    assert name.status == "consistent"
    assert url.status == "consistent"
    assert location.status == "consistent"
    assert name.internal is not None and name.website is not None
    assert name.internal.source_name == INTERNAL_SOURCE_NAME
    assert name.internal.source_url == "https://northgate.example/branch-main"
    assert name.internal.retrieved_at == NOW
    assert name.internal.confidence == 1.0
    assert name.website.source_url == "https://northgate.example/branch-main"
    assert name.website.retrieved_at == NOW - timedelta(hours=1)
    assert location.website is not None and location.website.confidence == 0.8
    assert response.summary.consistent == 3
    assert response.summary.differs == 0


def test_differing_values_are_flagged_without_choosing_a_winner() -> None:
    response = build_audit(
        _facility(updated_at=NOW - timedelta(days=1)),
        _run(
            hospital_name="Southwind Medical Centre",
            canonical_url="https://southwind.example/",
            target_url="https://southwind.example/",
        ),
        [_location_evidence("Southmere 411002")],
    )

    name = _finding(response, "facility_name")
    url = _finding(response, "website_url")
    location = _finding(response, "location")

    assert name.status == "differs"
    assert url.status == "differs"
    assert location.status == "differs"
    assert "neither value is treated as automatically correct" in name.explanation
    assert response.summary.differs == 3


def test_website_snapshot_older_than_internal_update_is_potentially_outdated() -> None:
    response = build_audit(
        _facility(updated_at=NOW),
        _run(retrieved_at=NOW - timedelta(days=3), hospital_name="Southwind Medical Centre"),
    )

    name = _finding(response, "facility_name")

    assert name.status == "potentially_outdated"
    assert "website may be outdated" in name.explanation
    assert response.summary.potentially_outdated == 1


def test_internal_value_missing_from_website_is_not_reported_as_absence() -> None:
    response = build_audit(_facility(), _run(hospital_name=None), [])

    name = _finding(response, "facility_name")
    location = _finding(response, "location")

    assert name.status == "missing_from_website"
    assert location.status == "missing_from_website"
    assert "not evidence that the website omits the field" in name.explanation
    assert response.summary.missing_from_website == 2


def test_website_only_service_field_is_missing_from_internal_record() -> None:
    evidence = EvidenceItem(
        criterion_key="infrastructure",
        value="ICU available; Total beds: 650",
        source="website_crawl",
        source_name="Hospital website (crawled)",
        source_url="https://northgate.example/branch-main",
        retrieved_at=NOW,
        status="available",
        confidence=0.7,
    )

    response = build_audit(_facility(), _run(), [evidence])
    finding = _finding(response, "infrastructure")

    assert finding.status == "missing_from_internal_record"
    assert finding.internal is None
    assert finding.website is not None and finding.website.confidence == 0.7
    assert finding.requires_authorized_integration is True
    assert response.summary.missing_from_internal_record == 1
    assert any("authorized hospital-server integration" in item for item in response.warnings)


def test_no_website_snapshot_is_unverifiable_not_missing() -> None:
    response = build_audit(_facility(), None, [])

    assert all(finding.status == "unverifiable" for finding in response.findings)
    assert response.website_snapshot is None
    assert any("No usable persisted website extraction" in item for item in response.warnings)
    assert response.summary.unverifiable == len(response.findings)


def test_audit_preserves_branch_level_identity() -> None:
    response = build_audit(_facility(), _run(), [_location_evidence()])

    assert response.facility_id == FACILITY_ID
    assert response.facility_name == "Northgate General Hospital"
    assert "physical-facility level" in response.branch_identity_note
    assert "parent organisation is never substituted" in response.branch_identity_note
    assert "No automatic correction" in response.disclaimer


@pytest.fixture()
def audit_session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def audit_client(audit_session: Session) -> TestClient:
    application = create_app()
    application.dependency_overrides[get_db_session] = lambda: audit_session
    with TestClient(application) as client:
        yield client
    application.dependency_overrides.clear()


def _seed(audit_session: Session) -> tuple[object, object]:
    service = RegistryService(audit_session)
    organization = service.create_organization(
        OrganizationCreate(name="Northgate Health Group")
    )
    facility = service.register_facility(
        FacilityCreate(
            name="Northgate General Hospital",
            organization_id=organization.id,
            canonical_url="https://northgate.example/branch-main",
            address="12 Sector 12",
            city="Northbridge",
            state="Northbridge State",
            postal_code="411001",
            country="Testland",
        )
    )
    return facility, organization


def _persist_run(
    audit_session: Session,
    facility_id: uuid.UUID,
    *,
    status: CrawlRunStatus = CrawlRunStatus.COMPLETED,
    evidence: list[EvidenceItem] | None = None,
) -> uuid.UUID:
    persistence = CrawlPersistenceService(audit_session)
    items = evidence if evidence is not None else [_location_evidence()]
    run = persistence.start_run(
        facility_id=facility_id,
        comparison_subject_id=None,
        subject_name=None,
        subject_location=None,
        subject_website_url="https://northgate.example/branch-main",
        target_url="https://northgate.example/branch-main",
        requested_url="https://northgate.example/branch-main",
        requested_max_pages=5,
    )
    usable_status = status in {
        CrawlRunStatus.COMPLETED,
        CrawlRunStatus.COMPLETED_WITH_WARNINGS,
    }
    persistence.finalize_run(
        run,
        status=status,
        evidence=items if usable_status else [],
        warnings=[],
        criteria_without_evidence=[],
        pages_fetched=3,
        hospital_name="Northgate General Hospital",
        canonical_url="https://northgate.example/branch-main",
    )
    audit_session.commit()
    return run.id


def test_api_audits_facility_against_latest_snapshot(
    audit_client: TestClient, audit_session: Session
) -> None:
    facility, _ = _seed(audit_session)
    run_id = _persist_run(audit_session, facility.id)

    response = audit_client.post(
        "/api/v1/consistency/audit", json={"facility_id": str(facility.id)}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["facility_id"] == str(facility.id)
    assert body["website_snapshot"]["crawl_run_id"] == str(run_id)
    statuses = {finding["field_key"]: finding["status"] for finding in body["findings"]}
    assert statuses["facility_name"] == "consistent"
    assert statuses["website_url"] == "consistent"
    assert statuses["location"] == "consistent"
    assert body["summary"]["consistent"] >= 3

    read_response = audit_client.get(f"/api/v1/consistency/audit/{facility.id}")
    assert read_response.status_code == 200
    assert read_response.json()["summary"] == body["summary"]


def test_api_without_snapshot_reports_unverifiable(
    audit_client: TestClient, audit_session: Session
) -> None:
    facility, _ = _seed(audit_session)

    response = audit_client.post(
        "/api/v1/consistency/audit", json={"facility_id": str(facility.id)}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["website_snapshot"] is None
    assert all(finding["status"] == "unverifiable" for finding in body["findings"])
    assert body["warnings"]


def test_api_rejects_unknown_facility(audit_client: TestClient) -> None:
    response = audit_client.post(
        "/api/v1/consistency/audit", json={"facility_id": str(uuid.uuid4())}
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "facility_not_found"


def test_api_rejects_unusable_explicit_run(
    audit_client: TestClient, audit_session: Session
) -> None:
    facility, _ = _seed(audit_session)
    run_id = _persist_run(
        audit_session, facility.id, status=CrawlRunStatus.FAILED, evidence=[]
    )

    response = audit_client.post(
        "/api/v1/consistency/audit",
        json={"facility_id": str(facility.id), "crawl_run_id": str(run_id)},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "crawl_run_not_usable"


def test_api_rejects_run_from_another_facility(
    audit_client: TestClient, audit_session: Session
) -> None:
    facility, _ = _seed(audit_session)
    other = RegistryService(audit_session).register_facility(
        FacilityCreate(name="Southwind Medical Centre", city="Southmere")
    )
    run_id = _persist_run(audit_session, other.id)

    response = audit_client.post(
        "/api/v1/consistency/audit",
        json={"facility_id": str(facility.id), "crawl_run_id": str(run_id)},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "crawl_run_ownership_mismatch"


def test_capabilities_advertise_the_consistency_audit(audit_client: TestClient) -> None:
    response = audit_client.get("/api/v1/comparison/capabilities")

    capabilities = {item["key"]: item for item in response.json()["capabilities"]}
    audit = capabilities["database_website_consistency_audit"]
    assert audit["available"] is True
    assert "/api/v1/consistency/audit" in audit["implementation"]

    integration = capabilities["authorized_hospital_system_integration"]
    assert integration["available"] is False
    assert integration["required_integration"]
