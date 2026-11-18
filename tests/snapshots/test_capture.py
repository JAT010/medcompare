"""Snapshot service tests — SQLite in-memory registry + simulated connector."""

from __future__ import annotations

import asyncio
import copy
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from hospital_extractor.persistence import models  # noqa: F401  register all tables
from hospital_extractor.persistence.base import Base
from hospital_extractor.persistence.models_integration import (
    ChangeSetStatus,
    HospitalSourceIntegration,
    IntegrationAuthMode,
    IntegrationStatus,
)
from hospital_extractor.persistence.registry.service import FacilityCreate, RegistryService
from hospital_extractor.snapshots.service import SnapshotService
from hospital_extractor.sources.authorization import AuthorizationPolicy
from hospital_extractor.sources.base import (
    AcquisitionResult,
    ConnectorErrorCode,
    HospitalConnectorError,
)
from hospital_extractor.sources.mock_hospital_api import (
    MOCK_CONNECTOR_TYPE,
    MockHospitalApiConnector,
)
from hospital_extractor.sources.registry import source_kind_for


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
    service = RegistryService(session)
    facility = service.register_facility(FacilityCreate(name="Simulated General Hospital"))
    return facility.id


def _integration(
    facility_id: uuid.UUID, payload_variant: str = "default"
) -> HospitalSourceIntegration:
    return HospitalSourceIntegration(
        facility_id=facility_id,
        name="Simulated integration",
        connector_type=MOCK_CONNECTOR_TYPE,
        auth_mode=IntegrationAuthMode.NONE.value,
        config_json={"payload_variant": payload_variant},
        status=IntegrationStatus.CONFIGURED.value,
        enabled=True,
    )


def _connector(variant: str = "default") -> MockHospitalApiConnector:
    policy = AuthorizationPolicy.from_config(
        authorized=True,
        allowed_hosts=["mock-authorized.example.test"],
    )
    return MockHospitalApiConnector(
        "mock-authorized-1",
        variant=variant,
        authorization=policy,
    )


def _capture(
    session: Session,
    integration: HospitalSourceIntegration,
    variant: str = "default",
):
    return asyncio.run(
        SnapshotService(session).capture(
            integration,
            connector=_connector(variant),
            source_kind="mock_hospital_api_simulated",
        )
    )


def test_first_capture_is_initial_snapshot(session: Session, facility_id: uuid.UUID) -> None:
    integration = _integration(facility_id)
    session.add(integration)
    session.commit()
    result = _capture(session, integration)
    assert result.change_set.status == ChangeSetStatus.INITIAL_SNAPSHOT.value
    assert result.snapshot.version_number == 1
    assert result.changes == ()
    assert result.snapshot.is_simulation is True
    session.commit()


def test_second_identical_capture_is_no_change_and_versions_increment(
    session: Session, facility_id: uuid.UUID
) -> None:
    integration = _integration(facility_id)
    session.add(integration)
    session.commit()
    first = _capture(session, integration)
    second = _capture(session, integration)
    assert first.snapshot.version_number == 1
    assert second.snapshot.version_number == 2
    assert second.change_set.status == ChangeSetStatus.NO_CHANGE.value
    assert second.changes == ()
    session.commit()


def test_changed_capture_detects_field_level_changes(
    session: Session, facility_id: uuid.UUID
) -> None:
    baseline = _integration(facility_id, "default")
    session.add(baseline)
    session.commit()
    _capture(session, baseline)

    changed = _integration(facility_id, "changed")
    changed.name = "Simulated integration (changed)"
    session.add(changed)
    session.commit()
    result = _capture(session, changed, variant="changed")

    assert result.change_set.status == ChangeSetStatus.CHANGES_DETECTED.value
    assert result.snapshot.version_number == 2
    paths = {change.path for change in result.changes}
    assert "hospital.bed_count" in paths
    assert "hospital.doctor_count" in paths
    added_entities = {
        change.entity_name for change in result.changes if change.change_type == "added"
    }
    assert "Robot-assisted surgery" in added_entities
    counts = {c.change_type for c in result.changes}
    assert counts <= {"added", "removed", "modified"}
    assert result.change_set.added_count == sum(
        1 for c in result.changes if c.change_type == "added"
    )
    session.commit()


def test_acquisition_failure_persists_nothing(session: Session, facility_id: uuid.UUID) -> None:
    class FailingConnector:
        connector_type = MOCK_CONNECTOR_TYPE

        async def acquire(self) -> AcquisitionResult:
            raise HospitalConnectorError(
                "boom", code=ConnectorErrorCode.NETWORK_ERROR, retryable=True
            )

    integration = _integration(facility_id)
    session.add(integration)
    session.commit()
    with pytest.raises(HospitalConnectorError):
        asyncio.run(
            SnapshotService(session).capture(
                integration,
                connector=FailingConnector(),  # type: ignore[arg-type]
                source_kind="mock_hospital_api_simulated",
            )
        )
    service = SnapshotService(session)
    assert service.list_for_facility(facility_id) == []
    assert service.list_change_sets(facility_id) == []


def test_capture_preserves_duplicate_named_collection_members(
    session: Session, facility_id: uuid.UUID
) -> None:
    payload_v1 = {
        "hospital": {"name": "Mock", "bed_count": 100},
        "departments": [
            {"name": "Cardiology", "beds": 10},
            {"name": "Cardiology", "beds": 12},
        ],
    }
    payload_v2 = {
        "hospital": {"name": "Mock", "bed_count": 100},
        "departments": [
            {"name": "Cardiology", "beds": 11},
            {"name": "Cardiology", "beds": 12},
        ],
    }

    class StubConnector:
        connector_type = MOCK_CONNECTOR_TYPE

        def __init__(self, payload):
            self._payload = payload

        async def acquire(self) -> AcquisitionResult:
            return AcquisitionResult.from_bytes(
                connector_type=self.connector_type,
                provider="STUB (SIMULATED)",
                source_ref="stub",
                requested_url="https://stub.example/",
                body=b"{}",
                payload=self._payload,
                simulated=True,
            )

    integration = _integration(facility_id)
    session.add(integration)
    session.commit()

    first = asyncio.run(
        SnapshotService(session).capture(
            integration,
            connector=StubConnector(payload_v1),
            source_kind=source_kind_for(integration),
        )
    )
    second = asyncio.run(
        SnapshotService(session).capture(
            integration,
            connector=StubConnector(copy.deepcopy(payload_v2)),
            source_kind=source_kind_for(integration),
        )
    )
    assert first.snapshot.version_number == 1
    assert second.snapshot.version_number == 2
    assert second.change_set.status == ChangeSetStatus.CHANGES_DETECTED.value
    added = [c for c in second.changes if c.change_type == "added"]
    removed = [c for c in second.changes if c.change_type == "removed"]
    assert added == [] and removed == []
    modified_paths = {c.path for c in second.changes if c.change_type == "modified"}
    assert any(path.startswith("departments") for path in modified_paths)
    session.commit()


def test_change_set_lists_changes(session: Session, facility_id: uuid.UUID) -> None:
    integration = _integration(facility_id)
    session.add(integration)
    session.commit()
    baseline = _capture(session, integration)
    changed = _capture(session, integration, variant="changed")
    service = SnapshotService(session)
    changes = service.list_changes(changed.change_set.id)
    assert len(changes) == len(changed.changes)
    assert {c.change_set_id for c in changes} == {changed.change_set.id}
    change_sets = service.list_change_sets(facility_id)
    assert {cs.id for cs in change_sets} >= {baseline.change_set.id, changed.change_set.id}
    session.commit()
