"""Worker end-to-end: monitoring -> capture -> change -> claim -> verify.

The website crawl is monkeypatched with a deterministic fixture; no network is
used. The hospital side is the provided simulated (mock) connector.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select

import hospital_extractor.worker.cli as worker_cli
from hospital_extractor.comparison.schemas import EvidenceItem
from hospital_extractor.monitoring.service import MonitoringService
from hospital_extractor.persistence import models  # noqa: F401
from hospital_extractor.persistence.base import Base
from hospital_extractor.persistence.models_integration import (
    HospitalReconciliationCase,
    HospitalSourceIntegration,
    HospitalVerificationJob,
    HospitalVerificationResult,
    IntegrationAuthMode,
    IntegrationStatus,
    VerificationJobStatus,
    VerificationResultStatus,
)
from hospital_extractor.persistence.registry.service import FacilityCreate, RegistryService
from hospital_extractor.persistence.session import reset_engine_cache, session_scope
from hospital_extractor.snapshots.service import SnapshotService
from hospital_extractor.sources.authorization import AuthorizationPolicy
from hospital_extractor.sources.mock_hospital_api import (
    MOCK_CONNECTOR_TYPE,
    MockHospitalApiConnector,
)
from hospital_extractor.sources.registry import source_kind_for
from hospital_extractor.verification.evidence import WebsiteEvidence


@pytest.fixture()
def temp_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "medcompare_worker_test.sqlite"
    url = f"sqlite+pysqlite:///{db_path}"
    monkeypatch.setenv("MEDCOMPARE_DATABASE_URL", url)
    reset_engine_cache()
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    engine.dispose()
    yield db_path
    reset_engine_cache()


def _policy() -> AuthorizationPolicy:
    return AuthorizationPolicy.from_config(
        authorized=True,
        allowed_hosts=["mock-authorized.example.test"],
    )


def _integration(facility_id: uuid.UUID, variant: str) -> HospitalSourceIntegration:
    return HospitalSourceIntegration(
        facility_id=facility_id,
        name="Simulated integration",
        connector_type=MOCK_CONNECTOR_TYPE,
        auth_mode=IntegrationAuthMode.NONE.value,
        config_json={"payload_variant": variant},
        status=IntegrationStatus.CONFIGURED.value,
        enabled=True,
    )


def _seed(facility_id: uuid.UUID, variant: str) -> uuid.UUID:
    with session_scope() as session:
        integration = _integration(facility_id, variant)
        session.add(integration)
        session.flush()
        integration_id = integration.id
    connector = MockHospitalApiConnector(
        "mock-authorized-1", variant=variant, authorization=_policy()
    )
    with session_scope() as session:
        row = session.get(HospitalSourceIntegration, integration_id)
        assert row is not None
        asyncio.run(
            SnapshotService(session).capture(
                row,
                connector=connector,
                source_kind=source_kind_for(row),
                captured_by="test",
            )
        )
    return integration_id


def _set_variant(integration_id: uuid.UUID, variant: str) -> None:
    with session_scope() as session:
        row = session.get(HospitalSourceIntegration, integration_id)
        assert row is not None
        row.config_json = {"payload_variant": variant}


def _monitoring_cycle(integration_id: uuid.UUID, facility_id: uuid.UUID) -> uuid.UUID:
    with session_scope() as session:
        service = MonitoringService(session)
        existing = service.list_schedules(facility_id)
        if not existing:
            service.create_schedule(
                facility_id=facility_id,
                integration_id=integration_id,
                name="drift monitor",
                interval_seconds=60,
                enabled=True,
                config_json={"target_ref": "https://mock-authorized.example.test/"},
            )
        else:
            for schedule in existing:
                schedule.next_run_at = datetime.now(UTC) - timedelta(minutes=5)

    with session_scope() as session:
        service = MonitoringService(session)
        runs = asyncio.run(service.process_due())
        job_ids = [r.execution.job_id for r in runs if r.execution.job_id]
        assert job_ids, f"expected a verification job to be enqueued, got {runs}"
        return job_ids[0]


def _website(beds: int) -> WebsiteEvidence:
    return WebsiteEvidence(
        status="completed",
        target_url="https://mock-authorized.example.test/",
        crawl_run_id=None,
        hospital_name="Mock Authorized Hospital",
        pages_fetched=5,
        retrieved_at=datetime.now(UTC),
        evidence=[
            EvidenceItem(
                criterion_key="infrastructure",
                value=f"Total beds: {beds}; ICU beds: 12",
                source="website_crawl",
                status="available",
            ),
            EvidenceItem(
                criterion_key="location",
                value="1 Mock Way, Testville, TS 000000",
                source="website_crawl",
                status="available",
            ),
        ],
    )


def _facility() -> uuid.UUID:
    with session_scope() as session:
        facility = RegistryService(session).register_facility(
            FacilityCreate(
                name="Mock Authorized Hospital",
                canonical_url="https://mock-authorized.example.test/",
            )
        )
        return facility.id


def test_monitoring_capture_to_verified_end_to_end(
    temp_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    facility_id = _facility()
    integration_id = _seed(facility_id, "default")
    _set_variant(integration_id, "changed_beds")

    job_id = _monitoring_cycle(integration_id, facility_id)

    async def fake_crawl(*args, **kwargs) -> WebsiteEvidence:
        return _website(120)

    monkeypatch.setattr(worker_cli, "crawl_website_for_verification", fake_crawl)
    assert worker_cli.main(["--once", "--limit", "1"]) == 0

    with session_scope() as session:
        job = session.get(HospitalVerificationJob, job_id)
        assert job is not None
        assert job.status == VerificationJobStatus.VERIFIED.value
        result = session.scalars(
            select(HospitalVerificationResult).where(HospitalVerificationResult.job_id == job_id)
        ).first()
        assert result is not None
        assert result.status == VerificationResultStatus.MATCH.value
        assert result.provenance_json["hospital_snapshot"]["is_simulation"] is True
        assert session.scalars(select(HospitalReconciliationCase)).all() == []


def test_worker_detects_mismatch_and_opens_case(temp_db, monkeypatch: pytest.MonkeyPatch) -> None:
    facility_id = _facility()
    integration_id = _seed(facility_id, "default")
    _set_variant(integration_id, "changed")

    job_id = _monitoring_cycle(integration_id, facility_id)

    async def fake_crawl(*args, **kwargs) -> WebsiteEvidence:
        return _website(100)

    monkeypatch.setattr(worker_cli, "crawl_website_for_verification", fake_crawl)
    assert worker_cli.main(["--once", "--limit", "1"]) == 0

    with session_scope() as session:
        job = session.get(HospitalVerificationJob, job_id)
        assert job is not None
        assert job.status == VerificationJobStatus.MISMATCH.value
        result = session.scalars(
            select(HospitalVerificationResult).where(HospitalVerificationResult.job_id == job_id)
        ).first()
        assert result is not None
        assert result.status == VerificationResultStatus.MISMATCH.value
        cases = session.scalars(select(HospitalReconciliationCase)).all()
        assert len(cases) == 1
        assert cases[0].verification_result_id == result.id
        assert cases[0].status == "open"
        assert cases[0].facility_id == facility_id
