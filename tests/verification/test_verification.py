"""Verification and reconciliation tests — offline, simulated evidence."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from hospital_extractor.comparison.schemas import EvidenceItem
from hospital_extractor.persistence import models  # noqa: F401
from hospital_extractor.persistence.base import Base
from hospital_extractor.persistence.models_integration import (
    HospitalReconciliationCase,
    HospitalSourceIntegration,
    HospitalVerificationJob,
    IntegrationAuthMode,
    IntegrationStatus,
    ReconciliationCaseStatus,
    VerificationJobStatus,
    VerificationResultStatus,
)
from hospital_extractor.persistence.registry.service import FacilityCreate, RegistryService
from hospital_extractor.reconciliation.service import ReconciliationError, ReconciliationService
from hospital_extractor.snapshots.service import SnapshotService
from hospital_extractor.sources.authorization import AuthorizationPolicy
from hospital_extractor.sources.mock_hospital_api import (
    MOCK_CONNECTOR_TYPE,
    MockHospitalApiConnector,
)
from hospital_extractor.sources.registry import source_kind_for
from hospital_extractor.verification.evidence import WebsiteEvidence
from hospital_extractor.verification.service import VerificationService


@pytest.fixture()
def session() -> Session:
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
def facility_id(session: Session) -> uuid.UUID:
    facility = RegistryService(session).register_facility(
        FacilityCreate(
            name="Mock Authorized Hospital", canonical_url="https://mock-authorized.example.test/"
        )
    )
    return facility.id


def _policy() -> AuthorizationPolicy:
    return AuthorizationPolicy.from_config(
        authorized=True,
        allowed_hosts=["mock-authorized.example.test"],
    )


def _integration(facility_id: uuid.UUID) -> HospitalSourceIntegration:
    return HospitalSourceIntegration(
        facility_id=facility_id,
        name="Simulated integration",
        connector_type=MOCK_CONNECTOR_TYPE,
        auth_mode=IntegrationAuthMode.NONE.value,
        config_json={"payload_variant": "default"},
        status=IntegrationStatus.CONFIGURED.value,
        enabled=True,
    )


def _capture(
    session: Session,
    integration: HospitalSourceIntegration,
    variant: str,
):
    return asyncio.run(
        SnapshotService(session).capture(
            integration,
            connector=MockHospitalApiConnector(
                "mock-authorized-1",
                variant=variant,
                authorization=_policy(),
            ),
            source_kind=source_kind_for(integration),
        )
    )


def _make_job(
    session: Session,
    facility_id: uuid.UUID,
    change_set_id: uuid.UUID | None,
    snapshot_id: uuid.UUID,
    *,
    status: str = VerificationJobStatus.RUNNING.value,
    key: str | None = None,
) -> HospitalVerificationJob:
    job = HospitalVerificationJob(
        facility_id=facility_id,
        change_set_id=change_set_id,
        snapshot_id=snapshot_id,
        target_ref="https://mock-authorized.example.test/",
        trigger_type="change_detected",
        status=status,
        idempotency_key=key or f"test:{uuid.uuid4()}",
        attempt_count=1,
        max_attempts=3,
        scheduled_for=datetime.now(UTC),
    )
    session.add(job)
    session.flush()
    return job


def _website_evidence(
    *,
    beds: int | None,
    hospital_name: str | None = "Mock Authorized Hospital",
    target_url: str = "https://mock-authorized.example.test/",
    status: str = "completed",
) -> WebsiteEvidence:
    items: list[EvidenceItem] = []
    if beds is not None:
        items.append(
            EvidenceItem(
                criterion_key="infrastructure",
                value=f"Total beds: {beds}; ICU beds: 12",
                source="website_crawl",
                status="available",
            )
        )
    items.append(
        EvidenceItem(
            criterion_key="location",
            value="1 Mock Way, Testville, TS 000000",
            source="website_crawl",
            status="available",
        )
    )
    return WebsiteEvidence(
        status=status,
        target_url=target_url,
        crawl_run_id=None,
        hospital_name=hospital_name,
        pages_fetched=6,
        retrieved_at=datetime.now(UTC),
        evidence=items,
    )


def test_mismatch_when_website_disagrees(session: Session, facility_id: uuid.UUID) -> None:
    integration = _integration(facility_id)
    session.add(integration)
    session.commit()
    _capture(session, integration, "default")
    changed = _capture(session, integration, "changed")
    job = _make_job(session, facility_id, changed.change_set.id, changed.snapshot.id)
    website = _website_evidence(beds=100)  # hospital API now claims 120
    outcome = VerificationService(session).run(job, website=website)
    assert outcome.result is not None
    assert outcome.result.status == VerificationResultStatus.MISMATCH.value
    assert job.status == VerificationJobStatus.MISMATCH.value
    assert outcome.reconciliation_case is not None
    assert outcome.reconciliation_case.verification_result_id == outcome.result.id
    session.commit()


def test_match_requires_every_change_to_be_verified(
    session: Session, facility_id: uuid.UUID
) -> None:
    integration = _integration(facility_id)
    session.add(integration)
    session.commit()
    _capture(session, integration, "default")
    changed = _capture(session, integration, "changed_beds")
    job = _make_job(session, facility_id, changed.change_set.id, changed.snapshot.id)
    website = _website_evidence(beds=120)
    outcome = VerificationService(session).run(job, website=website)
    assert outcome.result is not None
    assert outcome.result.status == VerificationResultStatus.MATCH.value
    assert job.status == VerificationJobStatus.VERIFIED.value
    checks = outcome.result.evidence_json["checks"]
    assert all(c["status"] == "match" for c in checks)
    assert outcome.result.provenance_json["hospital_snapshot"]["is_simulation"] is True
    session.commit()


def test_partial_evidence_never_yields_a_verified_match(
    session: Session, facility_id: uuid.UUID
) -> None:
    """'changed' touches bed counts AND procedures; procedures cannot be
    evaluated from website evidence, so the aggregate must stay insufficient
    even though every verifiable check matches."""
    integration = _integration(facility_id)
    session.add(integration)
    session.commit()
    _capture(session, integration, "default")
    changed = _capture(session, integration, "changed")
    job = _make_job(session, facility_id, changed.change_set.id, changed.snapshot.id)
    website = _website_evidence(beds=120)
    outcome = VerificationService(session).run(job, website=website)
    assert outcome.result is not None
    assert outcome.result.status == VerificationResultStatus.INSUFFICIENT_EVIDENCE.value
    assert job.status == VerificationJobStatus.INSUFFICIENT_EVIDENCE.value
    checks = outcome.result.evidence_json["checks"]
    bed_check = next(c for c in checks if c["path"] == "hospital.bed_count")
    assert bed_check["status"] == "match"
    procedure_checks = [c for c in checks if c["path"] == "procedures"]
    assert procedure_checks
    assert all(c["status"] == "insufficient_evidence" for c in procedure_checks)
    session.commit()


def test_insufficient_evidence_when_website_has_nothing_comparable(
    session: Session, facility_id: uuid.UUID
) -> None:
    integration = _integration(facility_id)
    session.add(integration)
    session.commit()
    _capture(session, integration, "default")
    changed = _capture(session, integration, "changed")
    job = _make_job(session, facility_id, changed.change_set.id, changed.snapshot.id)
    website = _website_evidence(beds=None)
    website = WebsiteEvidence(
        status=website.status,
        target_url=website.target_url,
        crawl_run_id=None,
        hospital_name=None,
        pages_fetched=0,
        retrieved_at=None,
        evidence=[],
    )
    outcome = VerificationService(session).run(job, website=website)
    assert outcome.result is not None
    assert outcome.result.status == VerificationResultStatus.INSUFFICIENT_EVIDENCE.value
    assert job.status == VerificationJobStatus.INSUFFICIENT_EVIDENCE.value
    assert outcome.reconciliation_case is not None
    session.commit()


def test_rerun_is_idempotent(session: Session, facility_id: uuid.UUID) -> None:
    integration = _integration(facility_id)
    session.add(integration)
    session.commit()
    _capture(session, integration, "default")
    changed = _capture(session, integration, "changed")
    job = _make_job(session, facility_id, changed.change_set.id, changed.snapshot.id)
    website = _website_evidence(beds=120)
    first = VerificationService(session).run(job, website=website)
    second = VerificationService(session).run(job, website=website)
    assert first.result is not None and second.result is not None
    assert first.result.id == second.result.id
    from sqlalchemy import func, select

    from hospital_extractor.persistence.models_integration import HospitalVerificationResult

    count = session.scalar(
        select(func.count(HospitalVerificationResult.id)).where(
            HospitalVerificationResult.job_id == job.id
        )
    )
    assert count == 1
    session.commit()


def test_reconciliation_transition_audit_trail(session: Session, facility_id: uuid.UUID) -> None:
    service = ReconciliationService(session)
    case = HospitalReconciliationCase(
        facility_id=facility_id,
        verification_result_id=None,
        status=ReconciliationCaseStatus.OPEN.value,
        summary="test",
        provenance_json={},
    )
    session.add(case)
    session.commit()

    transition = service.transition(
        case,
        to_status=ReconciliationCaseStatus.UNDER_REVIEW.value,
        actor_label="operator",
        note="picked up",
    )
    assert transition.case.status == ReconciliationCaseStatus.UNDER_REVIEW.value
    assert transition.event.from_status == ReconciliationCaseStatus.OPEN.value

    with pytest.raises(ReconciliationError):
        service.transition(
            case,
            to_status=ReconciliationCaseStatus.RESOLVED.value,
            actor_label="operator",
            note="no resolution given",
        )

    resolved = service.transition(
        case,
        to_status=ReconciliationCaseStatus.RESOLVED.value,
        actor_label="operator",
        resolution="hospital_source_confirmed",
        note="confirmed",
    )
    assert resolved.case.resolution == "hospital_source_confirmed"
    assert resolved.case.resolved_by_label == "operator"

    reopened = service.transition(
        case,
        to_status=ReconciliationCaseStatus.OPEN.value,
        actor_label="operator",
        note="reopen",
    )
    assert reopened.case.resolution is None

    events = list(case.events)
    assert [event.to_status for event in events] == [
        ReconciliationCaseStatus.UNDER_REVIEW.value,
        ReconciliationCaseStatus.RESOLVED.value,
        ReconciliationCaseStatus.OPEN.value,
    ]
    session.commit()
