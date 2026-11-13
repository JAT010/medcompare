"""Shared fixtures for API tests: isolated SQLite registry, no PostgreSQL."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from hospital_extractor.api import create_app
from hospital_extractor.api.deps import get_db_session
from hospital_extractor.persistence.base import Base
from hospital_extractor.persistence.registry.service import (
    FacilityCreate,
    OrganizationCreate,
    RegistryService,
)

NORTHGATE = "Northgate General Hospital"
SOUTHWIND = "Southwind Medical Centre"

__all__ = ["NORTHGATE", "SOUTHWIND"]


@pytest.fixture()
def db_session() -> Iterator[Session]:
    """A schema-complete, in-memory registry session shared across threads."""
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
def app(db_session: Session):
    application = create_app()
    application.dependency_overrides[get_db_session] = lambda: db_session
    return application


@pytest.fixture()
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def seeded(db_session: Session) -> dict[str, object]:
    """Two clearly fictional facilities used only as controlled test fixtures."""
    service = RegistryService(db_session)
    organization = service.create_organization(
        OrganizationCreate(name="Northgate Health Group")
    )
    northgate = service.register_facility(
        FacilityCreate(
            name=NORTHGATE,
            organization_id=organization.id,
            canonical_url="https://northgate.example/",
            address="12 Sector 12",
            city="Northbridge",
            state="Northbridge State",
            postal_code="411001",
            country="Testland",
        )
    )
    southwind = service.register_facility(
        FacilityCreate(
            name=SOUTHWIND,
            city="Southmere",
            state="Southmere State",
        )
    )
    return {"northgate": northgate, "southwind": southwind}
