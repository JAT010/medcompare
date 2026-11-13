"""Snapshot selection audit: A(success), B(success), C(failed), D(empty)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from hospital_extractor.api import create_app
from hospital_extractor.api.deps import get_db_session
from hospital_extractor.comparison.schemas import EvidenceItem
from hospital_extractor.persistence.base import Base
from hospital_extractor.persistence.crawl.service import CrawlPersistenceService
from hospital_extractor.persistence.models_crawl import CrawlRunStatus
from hospital_extractor.persistence.registry.service import (
    FacilityCreate,
    OrganizationCreate,
    RegistryService,
)


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
def make_client(session_factory):
    clients: list[TestClient] = []

    def _make() -> TestClient:
        app = create_app()

        def _session() -> Iterator:
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
        client = TestClient(app)
        clients.append(client)
        return client

    yield _make
    for c in clients:
        c.close()


def _seed(session_factory) -> dict[str, str]:
    s = session_factory()
    try:
        svc = RegistryService(s)
        org = svc.create_organization(OrganizationCreate(name="Org"))
        a = svc.register_facility(
            FacilityCreate(
                name="Alpha",
                organization_id=org.id,
                canonical_url="https://shared.example/alpha",
                city="A",
            )
        )
        b = svc.register_facility(
            FacilityCreate(
                name="Beta",
                organization_id=org.id,
                canonical_url="https://shared.example/beta",
                city="B",
            )
        )
        s.commit()
        return {"a": str(a.id), "b": str(b.id)}
    finally:
        s.close()


def _create_run(svc, fid, marker, status, usable_evidence=True, warnings=None):
    run = svc.start_run(
        facility_id=fid,
        comparison_subject_id=None,
        subject_name="Alpha",
        subject_location="A",
        subject_website_url="https://alpha.example/",
        target_url="https://alpha.example/",
        requested_url="https://alpha.example/",
        requested_max_pages=10,
    )
    evidence = []
    if usable_evidence and marker is not None:
        evidence = [
            EvidenceItem(
                criterion_key="location",
                value=f"Loc {marker}",
                source="website_crawl",
                source_name="site",
                status="available",
            )
        ]
    svc.finalize_run(
        run,
        status=status,
        evidence=evidence,
        warnings=warnings or ([] if status != CrawlRunStatus.EMPTY else ["empty"]),
        criteria_without_evidence=["affordability"] if evidence else ["location"],
        pages_fetched=1 if evidence else 0,
        hospital_name="Alpha" if evidence else None,
        failure_code="x" if status in {CrawlRunStatus.FAILED, CrawlRunStatus.TIMED_OUT} else None,
        failure_message="x" if status == CrawlRunStatus.FAILED else None,
    )
    return run.id


def _compare(client, hospitals):
    return client.post(
        "/api/v1/comparison",
        json={"scope": "standard_12", "source": "website_crawl", "hospitals": hospitals},
    )


def _loc(body, column_id):
    cols = {col["column_id"]: i for i, col in enumerate(body["columns"])}
    row = next(r for r in body["rows"] if r["criterion"]["key"] == "location")
    return str(row["cells"][cols[column_id]].get("value") or "")


def test_snapshot_selection_sequence_a_b_failed_empty(session_factory, make_client):
    ids = _seed(session_factory)
    fid_a = uuid.UUID(ids["a"])
    fid_b = uuid.UUID(ids["b"])
    s = session_factory()
    try:
        svc = CrawlPersistenceService(s)
        run_a = _create_run(svc, fid_a, "SNAP_A", CrawlRunStatus.COMPLETED)
        run_b = _create_run(svc, fid_a, "SNAP_B", CrawlRunStatus.COMPLETED)
        run_c = _create_run(svc, fid_a, None, CrawlRunStatus.FAILED, False, ["failed"])
        run_d = _create_run(svc, fid_a, None, CrawlRunStatus.EMPTY, False, ["empty"])
        # run on other facility for cross-facility rejection
        run_other = _create_run(svc, fid_b, "OTHER", CrawlRunStatus.COMPLETED)
        s.commit()
        # unregistered subject runs for cross-subject rejection
        subj1 = uuid.uuid4()
        subj2 = uuid.uuid4()
        u1 = svc.start_run(
            facility_id=None,
            comparison_subject_id=subj1,
            subject_name="U1",
            subject_location=None,
            subject_website_url="https://u1.example/",
            target_url="https://u1.example/",
            requested_url="https://u1.example/",
            requested_max_pages=5,
        )
        svc.finalize_run(
            u1,
            status=CrawlRunStatus.COMPLETED,
            evidence=[
                EvidenceItem(
                    criterion_key="location",
                    value="U1LOC",
                    source="website_crawl",
                    source_name="s",
                    status="available",
                )
            ],
            warnings=[],
            criteria_without_evidence=[],
            pages_fetched=1,
            hospital_name="U1",
        )
        u2 = svc.start_run(
            facility_id=None,
            comparison_subject_id=subj2,
            subject_name="U2",
            subject_location=None,
            subject_website_url="https://u2.example/",
            target_url="https://u2.example/",
            requested_url="https://u2.example/",
            requested_max_pages=5,
        )
        svc.finalize_run(
            u2,
            status=CrawlRunStatus.COMPLETED,
            evidence=[
                EvidenceItem(
                    criterion_key="location",
                    value="U2LOC",
                    source="website_crawl",
                    source_name="s",
                    status="available",
                )
            ],
            warnings=[],
            criteria_without_evidence=[],
            pages_fetched=1,
            hospital_name="U2",
        )
        s.commit()
    finally:
        s.close()

    c = make_client()

    # All historical runs retrievable
    for rid in [run_a, run_b, run_c, run_d]:
        resp = c.get(f"/api/v1/crawl/runs/{rid}", params={"facility_id": ids["a"]})
        assert resp.status_code == 200, resp.text

    # Default snapshot is B
    cmp_default = _compare(c, [
        {"type": "registered", "facility_id": ids["a"]},
        {"type": "registered", "facility_id": ids["b"]},
    ])
    assert cmp_default.status_code == 200
    assert "SNAP_B" in _loc(cmp_default.json(), ids["a"])
    assert "SNAP_A" not in _loc(cmp_default.json(), ids["a"])

    # Explicit selection of A works
    cmp_a = _compare(c, [
        {"type": "registered", "facility_id": ids["a"], "crawl_run_id": str(run_a)},
        {"type": "registered", "facility_id": ids["b"]},
    ])
    assert cmp_a.status_code == 200
    assert "SNAP_A" in _loc(cmp_a.json(), ids["a"])

    # Explicit selection of C or D rejected as unusable
    for rid, label in [(run_c, "failed"), (run_d, "empty")]:
        bad = _compare(c, [
            {"type": "registered", "facility_id": ids["a"], "crawl_run_id": str(rid)},
            {"type": "registered", "facility_id": ids["b"]},
        ])
        assert bad.status_code == 409, (label, bad.text)
        assert bad.json()["detail"]["error"] == "crawl_run_not_usable"

    # Cross-facility selection rejected
    cross_fac = _compare(c, [
        {"type": "registered", "facility_id": ids["a"], "crawl_run_id": str(run_other)},
        {"type": "registered", "facility_id": ids["b"]},
    ])
    assert cross_fac.status_code == 403
    assert cross_fac.json()["detail"]["error"] == "crawl_run_ownership_mismatch"

    # Cross-subject selection rejected
    cross_subj = _compare(c, [
        {
            "type": "unregistered",
            "comparison_subject_id": str(subj1),
            "crawl_run_id": str(u2.id),
            "name": "U1",
        },
        {
            "type": "unregistered",
            "comparison_subject_id": str(subj2),
            "crawl_run_id": str(u2.id),
            "name": "U2",
        },
    ])
    assert cross_subj.status_code == 403
    assert cross_subj.json()["detail"]["error"] == "crawl_run_ownership_mismatch"
