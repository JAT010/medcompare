"""Milestone 3 closure tests: rollback, partial-usability, snapshot selection audit."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from hospital_extractor.api import create_app
from hospital_extractor.api.deps import get_db_session
from hospital_extractor.api.routers.crawl import CrawlOutcome, get_crawl_runner
from hospital_extractor.comparison.schemas import CrawlRequest, EvidenceItem
from hospital_extractor.models.intake import IntakeEvidence, MasterIntake
from hospital_extractor.persistence.base import Base
from hospital_extractor.persistence.crawl import service as crawl_service_module
from hospital_extractor.persistence.crawl.service import CrawlPersistenceService
from hospital_extractor.persistence.models_crawl import (
    CrawlRunStatus,
    HospitalCrawlEvidence,
    HospitalCrawlRun,
)
from hospital_extractor.persistence.registry.service import (
    FacilityCreate,
    OrganizationCreate,
    RegistryService,
)

URL_A = "https://alpha.example/"
URL_B = "https://beta.example/"
URL_C = "https://gamma.example/"
URL_D = "https://delta.example/"
PUBLIC_TEST_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def fake_public_dns(monkeypatch) -> None:
    from hospital_extractor.crawler import ssrf

    monkeypatch.setattr(ssrf, "default_resolver", lambda host: [PUBLIC_TEST_IP])


def _intake(name: str, locality: str, marker: str) -> MasterIntake:
    intake = MasterIntake(
        hospital_name=name,
        area_locality=f"{locality} {marker}",
        city="Testville",
        pin_code="400001",
    )
    intake.booleans["cardiac_sciences_enabled"] = "yes"
    intake.evidence["cardiac_sciences_enabled"] = [
        IntakeEvidence.yes(
            "cardiac_sciences_enabled",
            marker,
            f"{URL_A}{marker}",
            "pattern-match",
            1.0,
            "cardiac_sciences_enabled",
        )
    ]
    return intake


class SequencedRunner:
    def __init__(self) -> None:
        self.calls: list[CrawlRequest] = []
        self.outcomes: list[CrawlOutcome | Exception] = []

    def push(self, outcome: CrawlOutcome | Exception) -> None:
        self.outcomes.append(outcome)

    async def __call__(self, request: CrawlRequest) -> CrawlOutcome:
        self.calls.append(request)
        if not self.outcomes:
            raise AssertionError("no scripted crawl outcome")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest.fixture()
def runner() -> SequencedRunner:
    return SequencedRunner()


@pytest.fixture()
def make_client(session_factory, runner):
    clients: list[TestClient] = []

    def _make() -> TestClient:
        app = create_app()

        def _session() -> Iterator[Session]:
            session = session_factory()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()

        app.dependency_overrides[get_db_session] = _session
        app.dependency_overrides[get_crawl_runner] = lambda: runner
        client = TestClient(app)
        clients.append(client)
        return client

    yield _make
    for c in clients:
        c.close()


def _seed_facilities(session_factory) -> dict[str, str]:
    session = session_factory()
    try:
        service = RegistryService(session)
        org = service.create_organization(OrganizationCreate(name="Snapshot Group"))
        a = service.register_facility(
            FacilityCreate(
                name="Alpha Hospital",
                organization_id=org.id,
                canonical_url="https://shared.example/alpha",
                city="Alphaville",
            )
        )
        b = service.register_facility(
            FacilityCreate(
                name="Beta Hospital",
                organization_id=org.id,
                canonical_url="https://shared.example/beta",
                city="Betaville",
            )
        )
        session.commit()
        return {"a": str(a.id), "b": str(b.id)}
    finally:
        session.close()


def _compare(client: TestClient, hospitals: list[dict], source: str = "website_crawl"):
    return client.post(
        "/api/v1/comparison",
        json={"scope": "standard_12", "source": source, "hospitals": hospitals},
    )


def _location_value(body: dict, column_id: str) -> str:
    cols = {col["column_id"]: idx for idx, col in enumerate(body["columns"])}
    loc = next(r for r in body["rows"] if r["criterion"]["key"] == "location")
    return str(loc["cells"][cols[column_id]].get("value") or "")


def test_persistence_rollback_leaves_no_false_success_and_preserves_earlier(
    session_factory, monkeypatch
):
    ids = _seed_facilities(session_factory)

    # Earlier successful crawl in committed transaction scope
    earlier_run_id: uuid.UUID
    session = session_factory()
    try:
        service = CrawlPersistenceService(session)
        run = service.start_run(
            facility_id=uuid.UUID(ids["a"]),
            comparison_subject_id=None,
            subject_name="Alpha Hospital",
            subject_location="Alphaville",
            subject_website_url=URL_A,
            target_url=URL_A,
            requested_url=URL_A,
            requested_max_pages=10,
        )
        earlier_run_id = run.id
        evidence = [
            EvidenceItem(
                criterion_key="location",
                value="Alpha Earlier Snapshot",
                source="website_crawl",
                source_name="site",
                status="available",
            )
        ]
        service.finalize_run(
            run,
            status=CrawlRunStatus.COMPLETED,
            evidence=evidence,
            warnings=[],
            criteria_without_evidence=[],
            pages_fetched=1,
            hospital_name="Alpha Hospital",
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    # Failed attempt: start run, attempt to persist evidence, force failure, rollback
    session2 = session_factory()
    original = crawl_service_module.evidence_to_model_items

    def boom(*args, **kwargs):
        raise RuntimeError("forced persistence failure")

    try:
        service2 = CrawlPersistenceService(session2)
        run2 = service2.start_run(
            facility_id=uuid.UUID(ids["a"]),
            comparison_subject_id=None,
            subject_name="Alpha Hospital",
            subject_location="Alphaville",
            subject_website_url=URL_A,
            target_url=URL_A,
            requested_url=URL_A,
            requested_max_pages=10,
        )
        monkeypatch.setattr(crawl_service_module, "evidence_to_model_items", boom)
        with pytest.raises(RuntimeError, match="forced persistence failure"):
            service2.finalize_run(
                run2,
                status=CrawlRunStatus.COMPLETED_WITH_WARNINGS,
                evidence=[
                    EvidenceItem(
                        criterion_key="location",
                        value="SHOULD_NOT_PERSIST",
                        source="website_crawl",
                        source_name="site",
                        status="available",
                    )
                ],
                warnings=["partial"],
                criteria_without_evidence=["affordability"],
                pages_fetched=1,
                hospital_name="Alpha Hospital",
            )
        session2.rollback()
    finally:
        monkeypatch.setattr(crawl_service_module, "evidence_to_model_items", original)
        session2.close()

    # Verify no falsely successful run or partial evidence remains; earlier preserved
    session3 = session_factory()
    try:
        runs = list(session3.execute(select(HospitalCrawlRun)).scalars().all())
        assert [r.id for r in runs] == [earlier_run_id]
        assert runs[0].status == CrawlRunStatus.COMPLETED.value
        assert runs[0].usable_for_comparison == 1
        evidence_rows = list(session3.execute(select(HospitalCrawlEvidence)).scalars().all())
        assert len(evidence_rows) == 1
        assert evidence_rows[0].value == "Alpha Earlier Snapshot"
        assert "SHOULD_NOT_PERSIST" not in str([r.value for r in evidence_rows])
        latest = CrawlPersistenceService(session3).latest_usable_for_facility(uuid.UUID(ids["a"]))
        assert latest is not None and latest.id == earlier_run_id
    finally:
        session3.close()


def test_partial_crawl_completed_with_warnings_usable_and_empty_unusable(
    make_client, session_factory
):
    ids = _seed_facilities(session_factory)
    facility_id = uuid.UUID(ids["a"])

    # Create partial crawl directly via persistence service (valid evidence + warnings)
    s = session_factory()
    try:
        svc = CrawlPersistenceService(s)
        partial = svc.start_run(
            facility_id=facility_id,
            comparison_subject_id=None,
            subject_name="Alpha Hospital",
            subject_location="Alphaville",
            subject_website_url=URL_A,
            target_url=URL_A,
            requested_url=URL_A,
            requested_max_pages=10,
        )
        partial_id = partial.id
        svc.finalize_run(
            partial,
            status=CrawlRunStatus.COMPLETED_WITH_WARNINGS,
            evidence=[
                EvidenceItem(
                    criterion_key="location",
                    value="Partial Alpha EVIDENCE_PARTIAL",
                    source="website_crawl",
                    source_name="site",
                    source_url=URL_A,
                    status="available",
                    note="partial provenance",
                )
            ],
            warnings=["Some pages could not be fetched."],
            criteria_without_evidence=["affordability"],
            pages_fetched=2,
            hospital_name="Alpha Hospital",
        )
        s.commit()
    finally:
        s.close()

    c = make_client()
    # Retrievable with warnings/provenance
    detail = c.get(f"/api/v1/crawl/runs/{partial_id}", params={"facility_id": ids["a"]})
    assert detail.status_code == 200
    d = detail.json()
    assert d["status"] == "completed_with_warnings"
    assert d["usable_for_comparison"] is True
    assert d["warnings"] == ["Some pages could not be fetched."]
    assert d["evidence"][0]["value"] == "Partial Alpha EVIDENCE_PARTIAL"
    assert d["evidence"][0]["note"] == "partial provenance"
    assert d["evidence"][0]["source_url"] == URL_A

    # Selected by default for comparison
    cmp1 = _compare(c, [
        {"type": "registered", "facility_id": ids["a"]},
        {"type": "registered", "facility_id": ids["b"]},
    ])
    assert cmp1.status_code == 200
    assert "EVIDENCE_PARTIAL" in _location_value(cmp1.json(), ids["a"])

    # Later failed crawl does not replace it
    s2 = session_factory()
    try:
        svc2 = CrawlPersistenceService(s2)
        failed = svc2.start_run(
            facility_id=facility_id,
            comparison_subject_id=None,
            subject_name="Alpha Hospital",
            subject_location="Alphaville",
            subject_website_url=URL_A,
            target_url=URL_A,
            requested_url=URL_A,
            requested_max_pages=10,
        )
        svc2.finalize_run(
            failed,
            status=CrawlRunStatus.FAILED,
            evidence=[],
            warnings=["failed"],
            criteria_without_evidence=["location"],
            pages_fetched=0,
            hospital_name=None,
            failure_code="crawl_failed",
            failure_message="failed",
        )
        s2.commit()
    finally:
        s2.close()

    c.close()
    c2 = make_client()
    cmp2 = _compare(c2, [
        {"type": "registered", "facility_id": ids["a"]},
        {"type": "registered", "facility_id": ids["b"]},
    ])
    assert cmp2.status_code == 200
    assert "EVIDENCE_PARTIAL" in _location_value(cmp2.json(), ids["a"])

    # Empty crawl unusable
    s3 = session_factory()
    try:
        svc3 = CrawlPersistenceService(s3)
        empty = svc3.start_run(
            facility_id=facility_id,
            comparison_subject_id=None,
            subject_name="Alpha Hospital",
            subject_location="Alphaville",
            subject_website_url=URL_A,
            target_url=URL_A,
            requested_url=URL_A,
            requested_max_pages=10,
        )
        empty_id = empty.id
        svc3.finalize_run(
            empty,
            status=CrawlRunStatus.EMPTY,
            evidence=[],
            warnings=["no content"],
            criteria_without_evidence=["location"],
            pages_fetched=0,
            hospital_name=None,
        )
        s3.commit()
    finally:
        s3.close()

    detail_empty = c2.get(
        f"/api/v1/crawl/runs/{empty_id}", params={"facility_id": ids["a"]}
    ).json()
    assert detail_empty["usable_for_comparison"] is False
    latest = _compare(c2, [
        {"type": "registered", "facility_id": ids["a"], "crawl_run_id": str(empty_id)},
        {"type": "registered", "facility_id": ids["b"]},
    ])
    assert latest.status_code == 409
    assert latest.json()["detail"]["error"] == "crawl_run_not_usable"
