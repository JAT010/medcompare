"""Deterministic, fully-offline verification + reconciliation demo (P19-B/C).

Three SIMULATED scenarios traverse the whole chain without waiting real time
(``delay_minutes=0``):

A) Authoritative (demo API) gains a procedure -> ChangeSet -> VerificationJob
   -> the "website" dataset already contains the same procedure -> VERIFIED
   -> NO reconciliation case (verified changes need no review).

B) Authoritative (demo API) changes ``bed_count`` -> ChangeSet ->
   VerificationJob -> the "website" dataset is unchanged -> MISMATCH ->
   a reconciliation case opens automatically, gets reviewed and resolved
   (recorded decision, full audit trail).

C) Authoritative (demo API) gains a procedure but the "website" dataset does
   not list it -> UNVERIFIED -> opt-in policy (-v flag) opens a reviewable
   case ready for a human.

Authoritative data comes from the real in-process mock API adapter; the
"website" side is a constructed canonical dataset carrying website provenance —
no real crawling, no internet.  Everything is labelled SIMULATED.  No hospital
data is ever written back to any source.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from hospital_extractor.mockapi.app import create_mock_app
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.procedure import Procedure
from hospital_extractor.models.verification import VerificationJob
from hospital_extractor.reconciliation.models import ResolutionType
from hospital_extractor.reconciliation.service import (
    resolve_case,
    start_review,
)
from hospital_extractor.sources.base import apply_source_provenance
from hospital_extractor.sources.mock_api import MockHospitalAPIDataSource
from hospital_extractor.sources.models import SourceMetadata, SourceType
from hospital_extractor.storage.database import HospitalDatabase
from hospital_extractor.verification.service import (
    CaptureFn,
    run_worker_cycle,
    schedule_job_for_capture,
)
from hospital_extractor.verification.settings import verification_settings

__all__ = ["DemoScenarioResult", "run_verification_demo"]

DEMO_NORTHSTAR = "demo-northstar"
DEMO_SOLSTICE = "demo-solstice"

SCENARIO_A_WEBSITE = "https://demo.northstar.example.in"
SCENARIO_B_WEBSITE = "https://demo.solstice.example.in"
SCENARIO_C_WEBSITE = "https://demo.northstar.example.in"
ADDED_PROCEDURE = "Robotic-Assisted Hip Replacement"
MODIFIED_BEDS = 450  # authoritative now says 450; the website still says 420
_SCENARIO_DELTA = timedelta(days=1)  # separate each scenario's clock to avoid ties

_ENV_UNVERIFIED = "RECONCILIATION_CREATE_UNVERIFIED_CASES"


@dataclass(frozen=True)
class DemoScenarioResult:
    """Outcome summary for one demo scenario."""

    label: str
    hospital_id: str
    change_set_id: str
    job_id: str
    job_status: str
    result_status: str | None
    checked_changes: list[str]
    ok: bool
    reconciliation_case_id: str | None = None
    reconciliation_status: str | None = None
    reconciliation_trigger: str | None = None
    reconciliation_resolution: str | None = None
    reconciliation_events: int = 0


def _simulated_website_metadata(ref: str) -> SourceMetadata:
    return SourceMetadata(
        source_type=SourceType.WEBSITE,
        source_id=f"website:{ref.split('://', 1)[-1]}",
        provider=ref.split("://", 1)[-1],
        base_url=ref,
        endpoint=ref,
        hospital_ref=ref,
        retrieved_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        status="success",
        notes="SIMULATED website dataset used by the verification demo",
    )


def _as_website_dataset(dataset: HospitalDataset, ref: str) -> HospitalDataset:
    """Rebuild a dataset from *dataset* with website provenance (simulated)."""
    cloned = HospitalDataset.model_validate(dataset.model_dump(mode="json"))
    return apply_source_provenance(cloned, _simulated_website_metadata(ref), rebuild=True)


def _with_added_procedure(dataset: HospitalDataset, name: str) -> HospitalDataset:
    """The authoritative dataset with one new procedure (SIMULATED change)."""
    cloned = HospitalDataset.model_validate(dataset.model_dump(mode="json"))
    now = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
    homepage = cloned.hospital.website if cloned.hospital is not None else ""
    source_type = cloned.source.source_type.value if cloned.source is not None else "mock_api"
    procedure = Procedure(
        name=name,
        speciality="Orthopaedics",
        description="Robot-assisted total hip arthroplasty (demo change).",
        source_url=homepage,
        page_url=homepage,
        extracted_at=now,
        source_type=source_type,
        extraction_method="structured_payload",
        source_strength="high",
        confidence=1.0,
    )
    cloned.procedures = [procedure, *cloned.procedures]
    return cloned


def _with_bed_count(dataset: HospitalDataset, beds: int) -> HospitalDataset:
    """The authoritative dataset with one changed scalar (SIMULATED change)."""
    cloned = HospitalDataset.model_validate(dataset.model_dump(mode="json"))
    hospital = cloned.hospital
    if hospital is None:
        raise ValueError("dataset has no hospital overview")
    cloned.hospital = hospital.model_copy(update={"bed_count": beds})
    return cloned


async def _fetch_mock(hospital_id: str) -> HospitalDataset:
    transport = httpx.ASGITransport(app=create_mock_app())
    client = httpx.AsyncClient(transport=transport, base_url="http://demo")
    try:
        return await MockHospitalAPIDataSource(hospital_id=hospital_id, client=client).fetch()
    finally:
        await client.aclose()


def _target_ref(dataset: HospitalDataset) -> str:
    return dataset.source.hospital_ref if dataset.source is not None else ""


def _stub_capture(website_ref_to_dataset: dict[str, HospitalDataset]) -> CaptureFn:
    """Capture stub returning the canned SIMULATED website dataset per ref."""

    async def capture(job: VerificationJob) -> HospitalDataset:
        if job.target_ref not in website_ref_to_dataset:
            raise ValueError(f"demo has no website dataset for {job.target_ref!r}")
        return website_ref_to_dataset[job.target_ref]

    return capture


async def _run_scenario(
    db: HospitalDatabase,
    *,
    label: str,
    hospital_id: str,
    base: datetime,
    authoritative_v1: HospitalDataset,
    authoritative_v2: HospitalDataset,
    website_dataset: HospitalDataset,
    unverified_opt_in: bool = False,
) -> DemoScenarioResult:
    now = base
    settings = verification_settings(delay_minutes=0, max_attempts=1)

    first = db.save_snapshot(authoritative_v1, captured_at=now)
    second = db.save_snapshot(authoritative_v2, captured_at=now + timedelta(hours=1))
    print(f"  [{label}] authoritative: {first.change_set.status.value} -> "
          f"{second.change_set.status.value} ({second.change_set.total} change(s))")

    job = schedule_job_for_capture(db, second, settings=settings, now=now)
    if job is None:
        raise AssertionError(f"{label}: expected a verification job from a changed capture")
    print(
        f"  [{label}] verification job: {job.verification_job_id} -> "
        f"target {job.target_source_type} ({job.target_ref})"
    )

    if unverified_opt_in:
        os.environ[_ENV_UNVERIFIED] = "1"
    try:
        processed = await run_worker_cycle(
            db,
            settings=settings,
            capture=_stub_capture({_target_ref(website_dataset): website_dataset}),
            now=now + timedelta(hours=2),
        )
    finally:
        if unverified_opt_in:
            os.environ.pop(_ENV_UNVERIFIED, None)
    assert processed == 1, f"{label}: worker processed {processed} jobs"

    result = db.get_verification_result(job.verification_job_id)
    assert result is not None, f"{label}: expected a verification result"

    case = db.get_reconciliation_case_for_result(result.verification_result_id)
    rec_case_id = case.reconciliation_case_id if case is not None else None
    rec_status = case.status.value if case is not None else None
    rec_trigger = case.trigger.value if case is not None else None
    rec_resolution = (
        case.resolution.value if case is not None and case.resolution is not None else None
    )
    rec_events = (
        len(db.list_reconciliation_events(case.reconciliation_case_id))
        if case is not None
        else 0
    )

    # Scenario B: a MISMATCH case surfaced automatically — walk it to resolution.
    if (
        result.status.value == "mismatch"
        and case is not None
    ):
        print(
            f"  [{label}] reconciliation case: {case.reconciliation_case_id} "
            f"({case.trigger.value}) -> reviewing"
        )
        start_review(
            db,
            case.reconciliation_case_id,
            actor="manual",
            assigned_to="demo-reviewer",
            reason="demo: reviewer picks up the open mismatch case",
        )
        resolved = resolve_case(
            db,
            case.reconciliation_case_id,
            resolution=ResolutionType.AUTHORITATIVE_SOURCE_CONFIRMED,
            reason=(
                "demo: authoritative capture was confirmed against hospital records; "
                "the simulated website simply lags behind"
            ),
            actor="demo-reviewer",
        )
        resolution = resolved.resolution
        rec_status = resolved.status.value
        rec_resolution = resolution.value if resolution is not None else None
        rec_events = len(db.list_reconciliation_events(case.reconciliation_case_id))
        print(
            f"  [{label}] reconciliation resolved: {rec_resolution} "
            f"({rec_events} audit event(s))"
        )
    elif case is not None:
        print(
            f"  [{label}] reconciliation case: {case.reconciliation_case_id} "
            f"({case.trigger.value}) -> {case.status.value}, ready for human review"
        )

    return DemoScenarioResult(
        label=label,
        hospital_id=job.hospital_id,
        change_set_id=job.change_set_id,
        job_id=job.verification_job_id,
        job_status=result.status.value,
        result_status=result.status.value,
        checked_changes=[change.path for change in result.change_results],
        ok=True,
        reconciliation_case_id=rec_case_id,
        reconciliation_status=rec_status,
        reconciliation_trigger=rec_trigger,
        reconciliation_resolution=rec_resolution,
        reconciliation_events=rec_events,
    )


def run_verification_demo(
    db_path: Path | str,
    *,
    hospital_ids: list[str] | None = None,
) -> list[DemoScenarioResult]:
    """Run the SIMULATED verification scenarios against ``db_path``.

    Returns per-scenario results.  Authoritative data is pulled through the
    real demo-API adapter (in-process); the website side is canned canonical
    data with website provenance.  ``delay_minutes=0`` — nothing sleeps.
    """
    hospitals = hospital_ids or [DEMO_NORTHSTAR, DEMO_SOLSTICE]
    db_path = Path(db_path)

    async def run() -> list[DemoScenarioResult]:
        results: list[DemoScenarioResult] = []
        base = datetime(2026, 9, 2, 9, 30, tzinfo=UTC)
        # Scenario A: website contains the same update -> VERIFIED (no case).
        if DEMO_NORTHSTAR in hospitals:
            authoritative_v1 = await _fetch_mock(DEMO_NORTHSTAR)
            authoritative_v2 = _with_added_procedure(authoritative_v1, ADDED_PROCEDURE)
            website_dataset = _as_website_dataset(authoritative_v2, SCENARIO_A_WEBSITE)
            with HospitalDatabase(db_path) as db:
                results.append(
                    await _run_scenario(
                        db,
                        label="A",
                        hospital_id=DEMO_NORTHSTAR,
                        base=base,
                        authoritative_v1=authoritative_v1,
                        authoritative_v2=authoritative_v2,
                        website_dataset=website_dataset,
                    )
                )
        # Scenario B: website unchanged -> MISMATCH -> case reviewed + resolved.
        if DEMO_SOLSTICE in hospitals:
            authoritative_v1 = await _fetch_mock(DEMO_SOLSTICE)
            authoritative_v2 = _with_bed_count(authoritative_v1, MODIFIED_BEDS)
            website_dataset = _as_website_dataset(authoritative_v1, SCENARIO_B_WEBSITE)
            with HospitalDatabase(db_path) as db:
                results.append(
                    await _run_scenario(
                        db,
                        label="B",
                        hospital_id=DEMO_SOLSTICE,
                        base=base + _SCENARIO_DELTA,
                        authoritative_v1=authoritative_v1,
                        authoritative_v2=authoritative_v2,
                        website_dataset=website_dataset,
                    )
                )
        # Scenario C: website cannot confirm -> UNVERIFIED -> reviewable case.
        if DEMO_SOLSTICE in hospitals:
            authoritative_v1 = await _fetch_mock(DEMO_SOLSTICE)
            authoritative_v2 = _with_added_procedure(authoritative_v1, ADDED_PROCEDURE)
            website_dataset = _as_website_dataset(authoritative_v1, SCENARIO_B_WEBSITE)
            with HospitalDatabase(db_path) as db:
                results.append(
                    await _run_scenario(
                        db,
                        label="C",
                        hospital_id=DEMO_SOLSTICE,
                        base=base + 2 * _SCENARIO_DELTA,
                        authoritative_v1=authoritative_v1,
                        authoritative_v2=authoritative_v2,
                        website_dataset=website_dataset,
                        unverified_opt_in=True,
                    )
                )
        return results

    return asyncio.run(run())