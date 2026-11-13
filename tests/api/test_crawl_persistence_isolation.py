"""API-level persistence and isolation tests for Milestone 3.

These tests prove server-side persistence across fresh app/session instances
against the same SQLite database file (not the same in-memory session object),
and enforce facility/subject isolation and latest-usable snapshot semantics.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from hospital_extractor.api import create_app
from hospital_extractor.api.deps import get_db_session
from hospital_extractor.api.routers.crawl import CrawlOutcome, get_crawl_runner
from hospital_extractor.comparison.schemas import CrawlRequest
from hospital_extractor.models.intake import IntakeEvidence, MasterIntake
from hospital_extractor.persistence.base import Base
from hospital_extractor.persistence.registry.service import (
    FacilityCreate,
    OrganizationCreate,
    RegistryService,
)

URL_A = "https://alpha.example/"
URL_B = "https://beta.example/"
URL_U1 = "https://unreg-one.example/"
URL_U2 = "https://unreg-two.example/"
PUBLIC_TEST_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def fake_public_dns(monkeypatch) -> None:
    from hospital_extractor.crawler import ssrf

    monkeypatch.setattr(ssrf, "default_resolver", lambda host: [PUBLIC_TEST_IP])


def _intake(name: str, locality: str, marker: str) -> MasterIntake:
    # Put marker in area_locality so crawl-derived location evidence value
    # contains a unique, comparable token per run (evidence_from_intake does not
    # copy boolean evidence text into values).
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


def _seed_two(session_factory) -> dict[str, str]:
    session = session_factory()
    try:
        service = RegistryService(session)
        org = service.create_organization(OrganizationCreate(name="Shared Domain Group"))
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
        return {"a": str(a.id), "b": str(b.id)}
    finally:
        session.close()


def _compare(client: TestClient, hospitals: list[dict], source: str = "website_crawl"):
    return client.post(
        "/api/v1/comparison",
        json={"scope": "standard_12", "source": source, "hospitals": hospitals},
    )


def test_registered_persistence_isolation_mismatch_and_failed_recrawl_preservation(
    make_client, session_factory, runner
):
    ids = _seed_two(session_factory)
    a_id, b_id = ids["a"], ids["b"]

    # 1-5) crawl A and B with different evidence, persist
    runner.push(
        CrawlOutcome(
            URL_A, 3, "Alpha Hospital", _intake("Alpha Hospital", "AlphaLoc", "EVIDENCE_A")
        )
    )
    runner.push(
        CrawlOutcome(
            URL_B, 2, "Beta Hospital", _intake("Beta Hospital", "BetaLoc", "EVIDENCE_B")
        )
    )
    c1 = make_client()
    ra = c1.post("/api/v1/crawl/extract", json={"url": URL_A, "facility_id": a_id})
    rb = c1.post("/api/v1/crawl/extract", json={"url": URL_B, "facility_id": b_id})
    assert ra.status_code == 200 and rb.status_code == 200
    run_a = ra.json()["crawl_run_id"]
    run_b = rb.json()["crawl_run_id"]
    assert ra.json()["facility_id"] == a_id
    assert rb.json()["facility_id"] == b_id
    assert run_a != run_b

    # 6-8) fresh app/session instances retrieve persisted runs
    c1.close()
    c2 = make_client()
    ga = c2.get(f"/api/v1/crawl/runs/{run_a}", params={"facility_id": a_id})
    gb = c2.get(f"/api/v1/crawl/runs/{run_b}", params={"facility_id": b_id})
    assert ga.status_code == 200 and gb.status_code == 200
    assert "EVIDENCE_A" in str(ga.json()["evidence"])
    assert "EVIDENCE_B" in str(gb.json()["evidence"])

    # 9-11) compare uses persisted latest usable snapshots and isolates columns
    cmp1 = _compare(
        c2,
        [
            {"type": "registered", "facility_id": a_id},
            {"type": "registered", "facility_id": b_id},
        ],
    )
    assert cmp1.status_code == 200
    body = cmp1.json()
    cols = {col["column_id"]: idx for idx, col in enumerate(body["columns"])}
    loc = next(r for r in body["rows"] if r["criterion"]["key"] == "location")
    a_text = str(loc["cells"][cols[a_id]].get("value") or "")
    b_text = str(loc["cells"][cols[b_id]].get("value") or "")
    assert "EVIDENCE_A" in a_text and "EVIDENCE_B" not in a_text
    assert "EVIDENCE_B" in b_text and "EVIDENCE_A" not in b_text

    # 12-13) mismatched explicit run rejected
    bad = _compare(c2, [
        {"type": "registered", "facility_id": a_id, "crawl_run_id": run_b},
        {"type": "registered", "facility_id": b_id},
    ])
    assert bad.status_code == 403
    assert bad.json()["detail"]["error"] == "crawl_run_ownership_mismatch"

    # 14-15) failed new crawl for A must not replace previous usable snapshot
    runner.push(
        CrawlOutcome(
            URL_A,
            0,
            None,
            None,
            warnings=[
                "No usable page content was retrieved for this URL, so no evidence "
                "could be extracted."
            ],
        )
    )
    rf = c2.post("/api/v1/crawl/extract", json={"url": URL_A, "facility_id": a_id})
    assert rf.status_code == 200
    assert rf.json()["crawl_status"] == "empty"
    assert rf.json()["crawl_run_id"] != run_a

    c2.close()
    c3 = make_client()
    cmp2 = _compare(
        c3,
        [
            {"type": "registered", "facility_id": a_id},
            {"type": "registered", "facility_id": b_id},
        ],
    )
    assert cmp2.status_code == 200
    body2 = cmp2.json()
    cols2 = {col["column_id"]: idx for idx, col in enumerate(body2["columns"])}
    loc2 = next(r for r in body2["rows"] if r["criterion"]["key"] == "location")
    a_text2 = str(loc2["cells"][cols2[a_id]].get("value") or "")
    assert "EVIDENCE_A" in a_text2  # previous usable snapshot still selected


def test_two_unregistered_subjects_isolated_and_mismatch_rejected(
    make_client, session_factory, runner
):
    runner.push(
        CrawlOutcome(URL_U1, 1, "Unreg One", _intake("Unreg One", "U1Loc", "UNREG_ONE"))
    )
    runner.push(
        CrawlOutcome(URL_U2, 1, "Unreg Two", _intake("Unreg Two", "U2Loc", "UNREG_TWO"))
    )
    c = make_client()
    r1 = c.post("/api/v1/crawl/extract", json={"url": URL_U1, "name": "Unreg One"})
    r2 = c.post("/api/v1/crawl/extract", json={"url": URL_U2, "name": "Unreg Two"})
    assert r1.status_code == 200 and r2.status_code == 200
    run1, subj1 = r1.json()["crawl_run_id"], r1.json()["comparison_subject_id"]
    run2, subj2 = r2.json()["crawl_run_id"], r2.json()["comparison_subject_id"]
    assert subj1 and subj2 and subj1 != subj2
    assert r1.json()["facility_id"] is None and r2.json()["facility_id"] is None

    c.close()
    c2 = make_client()
    cmp_ok = _compare(
        c2,
        [
            {
                "type": "unregistered",
                "comparison_subject_id": subj1,
                "crawl_run_id": run1,
                "name": "Unreg One",
            },
            {
                "type": "unregistered",
                "comparison_subject_id": subj2,
                "crawl_run_id": run2,
                "name": "Unreg Two",
            },
        ],
    )
    assert cmp_ok.status_code == 200
    body = cmp_ok.json()
    cols = {
        col.get("comparison_subject_id") or col["column_id"]: idx
        for idx, col in enumerate(body["columns"])
    }
    loc = next(r for r in body["rows"] if r["criterion"]["key"] == "location")
    t1 = str(loc["cells"][cols[subj1]].get("value") or "")
    t2 = str(loc["cells"][cols[subj2]].get("value") or "")
    assert "UNREG_ONE" in t1 and "UNREG_TWO" not in t1
    assert "UNREG_TWO" in t2 and "UNREG_ONE" not in t2

    bad = _compare(
        c2,
        [
            {
                "type": "unregistered",
                "comparison_subject_id": subj1,
                "crawl_run_id": run2,
                "name": "Unreg One",
            },
            {
                "type": "unregistered",
                "comparison_subject_id": subj2,
                "crawl_run_id": run2,
                "name": "Unreg Two",
            },
        ],
    )
    assert bad.status_code == 403
    assert bad.json()["detail"]["error"] == "crawl_run_ownership_mismatch"

    # unknown run
    missing = _compare(
        c2,
        [
            {
                "type": "unregistered",
                "comparison_subject_id": subj1,
                "crawl_run_id": str(uuid.uuid4()),
                "name": "Unreg One",
            },
            {
                "type": "unregistered",
                "comparison_subject_id": subj2,
                "crawl_run_id": run2,
                "name": "Unreg Two",
            },
        ],
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"] == "crawl_run_not_found"
