"""Monitoring scheduler and worker claim tests — offline, simulated."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from hospital_extractor.jobs.queue import claim_due_jobs, enqueue_job, fail_job, sweep_stale_leases
from hospital_extractor.monitoring.service import MonitoringService
from hospital_extractor.persistence import models  # noqa: F401
from hospital_extractor.persistence.base import Base
from hospital_extractor.persistence.models_integration import (
    HospitalSourceIntegration,
    HospitalVerificationJob,
    IntegrationAuthMode,
    IntegrationStatus,
    MonitoringExecutionStatus,
    VerificationJobStatus,
)
from hospital_extractor.persistence.registry.service import FacilityCreate, RegistryService
from hospital_extractor.sources.authorization import AuthorizationPolicy
from hospital_extractor.sources.mock_hospital_api import (
    MOCK_CONNECTOR_TYPE,
    MockHospitalApiConnector,
)


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
            name="Simulated General Hospital",
            canonical_url="https://mock-authorized.example.test/",
        )
    )
    return facility.id


def _integration(facility_id: uuid.UUID, variant: str = "default") -> HospitalSourceIntegration:
    return HospitalSourceIntegration(
        facility_id=facility_id,
        name=f"Simulated integration {variant}",
        connector_type=MOCK_CONNECTOR_TYPE,
        auth_mode=IntegrationAuthMode.NONE.value,
        config_json={"payload_variant": variant},
        status=IntegrationStatus.CONFIGURED.value,
        enabled=True,
    )


def _policy() -> AuthorizationPolicy:
    return AuthorizationPolicy.from_config(
        authorized=True,
        allowed_hosts=["mock-authorized.example.test"],
    )


def _connector(variant: str) -> MockHospitalApiConnector:
    return MockHospitalApiConnector(
        "mock-authorized-1",
        variant=variant,
        authorization=_policy(),
    )


def test_enqueue_is_idempotent(session: Session, facility_id: uuid.UUID) -> None:
    first = enqueue_job(
        session,
        facility_id=facility_id,
        target_ref="https://mock-authorized.example.test/",
        trigger_type="change_detected",
        idempotency_key="demo:1",
    )
    second = enqueue_job(
        session,
        facility_id=facility_id,
        target_ref="https://mock-authorized.example.test/",
        trigger_type="change_detected",
        idempotency_key="demo:1",
    )
    assert first.created is True
    assert second.created is False
    assert first.job.id == second.job.id
    session.commit()


def test_claim_marks_running_and_attempt_incremented(
    session: Session, facility_id: uuid.UUID
) -> None:
    job = enqueue_job(
        session,
        facility_id=facility_id,
        target_ref="https://mock-authorized.example.test/",
        trigger_type="manual",
        idempotency_key="claim:1",
    ).job
    claimed = claim_due_jobs(session, worker_id="w1", limit=1, lease_seconds=300)
    assert [c.id for c in claimed] == [job.id]
    assert job.status == VerificationJobStatus.RUNNING.value
    assert job.attempt_count == 1
    assert job.lease_owner == "w1"
    # A second claim of the same job must fail (not pending anymore).
    assert claim_due_jobs(session, worker_id="w2", limit=1) == []
    session.commit()


def test_stale_lease_swept_back_to_pending(session: Session, facility_id: uuid.UUID) -> None:
    job = enqueue_job(
        session,
        facility_id=facility_id,
        target_ref="https://mock-authorized.example.test/",
        trigger_type="manual",
        idempotency_key="sweep:1",
    ).job
    job.status = VerificationJobStatus.RUNNING.value
    job.started_at = datetime.now(UTC) - timedelta(hours=1)
    job.lease_expires_at = datetime.now(UTC) - timedelta(minutes=5)
    job.attempt_count = 1
    session.commit()

    swept = sweep_stale_leases(session)
    assert [s.id for s in swept] == [job.id]
    assert job.status == VerificationJobStatus.PENDING.value
    assert job.last_error_code == "lease_expired"
    session.commit()


def test_fail_job_requeues_then_terminates(session: Session, facility_id: uuid.UUID) -> None:
    job = enqueue_job(
        session,
        facility_id=facility_id,
        target_ref="https://mock-authorized.example.test/",
        trigger_type="manual",
        idempotency_key="fail:1",
        max_attempts=2,
    ).job
    claimed = claim_due_jobs(session, worker_id="w1", limit=1)
    assert claimed
    fail_job(
        session,
        job,
        error_code="boom",
        error_message="first",
        retryable=True,
        backoff_seconds=1,
    )
    assert job.status == VerificationJobStatus.PENDING.value

    claimed_again = claim_due_jobs(
        session,
        worker_id="w1",
        limit=1,
        now=datetime.now(UTC) + timedelta(seconds=5),
    )
    assert claimed_again
    fail_job(
        session,
        job,
        error_code="boom",
        error_message="second",
        retryable=True,
        backoff_seconds=1,
    )
    assert job.status == VerificationJobStatus.FAILED.value
    session.commit()


def test_cancelled_job_cannot_be_claimed(session: Session, facility_id: uuid.UUID) -> None:
    job = enqueue_job(
        session,
        facility_id=facility_id,
        target_ref="https://mock-authorized.example.test/",
        trigger_type="manual",
        idempotency_key="cancel:1",
    ).job
    job.status = VerificationJobStatus.CANCELLED.value
    session.commit()

    assert claim_due_jobs(session, worker_id="w1", limit=1) == []
    session.commit()


def test_claim_respects_max_attempts(session: Session, facility_id: uuid.UUID) -> None:
    job = enqueue_job(
        session,
        facility_id=facility_id,
        target_ref="https://mock-authorized.example.test/",
        trigger_type="manual",
        idempotency_key="max:1",
        max_attempts=1,
    ).job
    job.attempt_count = 1
    session.commit()

    assert claim_due_jobs(session, worker_id="w1", limit=1) == []
    session.commit()


def test_monitoring_enqueues_job_on_change(session: Session, facility_id: uuid.UUID) -> None:
    integration = _integration(facility_id, "default")
    session.add(integration)
    session.commit()
    service = MonitoringService(session)
    schedule = service.create_schedule(
        facility_id=facility_id,
        integration_id=integration.id,
        name="daily drift",
        interval_seconds=60,
        enabled=True,
        config_json={"target_ref": "https://mock-authorized.example.test/"},
    )

    first_run = asyncio.run(service.process_due())
    assert first_run and first_run[0].execution.status == MonitoringExecutionStatus.SKIPPED.value
    assert first_run[0].execution.reason == "initial_snapshot"

    integration.config_json = {"payload_variant": "changed"}
    session.commit()

    second_run = asyncio.run(service.process_due(now=datetime.now(UTC) + timedelta(minutes=2)))
    assert second_run and second_run[0].execution.status == MonitoringExecutionStatus.ENQUEUED.value
    job_id = second_run[0].execution.job_id
    assert job_id is not None
    jobs = session.query(HospitalVerificationJob).filter_by(id=job_id).all()
    assert jobs and jobs[0].trigger_type == "monitoring"
    assert schedule.next_run_at is not None and schedule.next_run_at > datetime.now(UTC)
    session.commit()
