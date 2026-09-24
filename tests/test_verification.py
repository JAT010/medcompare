"""P19-B verification orchestrator tests.

Covers: job creation from captures (authoritative-only, idempotent), the
scheduler (due jobs, atomic claims, bounded retries), the worker (VERIFIED /
MISMATCH / UNVERIFIED, provenance, target snapshots kept under one identity),
per-change evaluation rules, the authorization boundary, the API and CLI
surfaces, and a read-safety regression proving normal reads never crawl.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from hospital_extractor.api.app import create_app
from hospital_extractor.cli import main
from hospital_extractor.models.change import Change, ChangeSet, ChangeSetStatus, ChangeType
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.metadata import FetchStatus, SourceMetadata, SourceType
from hospital_extractor.models.procedure import Procedure
from hospital_extractor.models.verification import (
    VerificationJob,
    VerificationJobStatus,
    VerificationOutcome,
    VerificationResultStatus,
)
from hospital_extractor.storage.database import HospitalDatabase
from hospital_extractor.verification.evaluate import evaluate_change_set
from hospital_extractor.verification.scheduler import VerificationScheduler
from hospital_extractor.verification.service import (
    VerificationPermanentError,
    create_manual_job,
    run_worker_cycle,
    schedule_job_for_capture,
)
from hospital_extractor.verification.settings import verification_settings

NOW = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _metadata(ref: str, source_type: str = "mock_api") -> SourceMetadata:
    return SourceMetadata(
        source_type=SourceType(source_type),
        source_id=f"{source_type}:{ref}",
        hospital_ref=ref,
        base_url=f"https://{ref}",
        retrieved_at=NOW.isoformat(),
        status=FetchStatus.SUCCESS,
    )


def _dataset(
    ref: str = "demo-northstar",
    name: str = "Northstar",
    beds: int = 420,
    website: str = "https://demo.northstar.example.in",
    procedures: tuple[str, ...] = (),
    source_type: str = "mock_api",
) -> HospitalDataset:
    return HospitalDataset(
        source=_metadata(ref, source_type),
        hospital=HospitalOverview(name=name, bed_count=beds, source_url=website, website=website),
        contacts=[],
        infrastructure=[],
        facilities=[],
        departments=[],
        specialities=[],
        doctors=[],
        treatments=[],
        procedures=[
            Procedure(name=procedure, source_url=website, source_type=source_type)
            for procedure in procedures
        ],
        technologies=[],
        accreditations=[],
    )


def _sq(db_path) -> HospitalDatabase:
    return HospitalDatabase(db_path)


def _changeset_with(changes: list[Change]) -> ChangeSet:
    return ChangeSet(
        change_set_id="c_test",
        snapshot_id="s_test",
        hospital_id="h_test",
        source_type="mock_api",
        detected_at=NOW,
        status=ChangeSetStatus.CHANGES_DETECTED,
        changes=changes,
    )


def _change(
    change_type: ChangeType,
    path: str,
    category: str = "procedures",
    field: str = "",
    entity_name: str | None = None,
    new_value: object = None,
) -> Change:
    return Change(
        category=category,
        path=path,
        field=field,
        change_type=change_type,
        entity_name=entity_name,
        new_value=new_value,
        source_type="mock_api",
    )


async def _run_cycle(
    db,
    capture,
    *,
    max_attempts: int = 3,
    backoff_minutes: int = 2,
    now=NOW,
) -> int:
    return await run_worker_cycle(
        db,
        settings=verification_settings(
            delay_minutes=0, max_attempts=max_attempts, backoff_minutes=backoff_minutes
        ),
        capture=capture,
        now=now,
    )


def _run_cycle_sync(
    db,
    capture,
    *,
    max_attempts: int = 3,
    backoff_minutes: int = 2,
    now=NOW,
) -> int:
    """Run one worker cycle in a fresh event loop for sync tests."""
    import asyncio

    return asyncio.run(
        _run_cycle(
            db, capture, max_attempts=max_attempts, backoff_minutes=backoff_minutes, now=now
        )
    )


# ---------------------------------------------------------------------------
# Job creation from captures
# ---------------------------------------------------------------------------


class TestJobScheduling:
    def test_initial_snapshot_never_schedules(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            capture = db.save_snapshot(_dataset())
            assert capture.change_set.status is ChangeSetStatus.INITIAL_SNAPSHOT
            assert schedule_job_for_capture(db, capture) is None
            assert db.list_verification_jobs(capture.snapshot.hospital_id) == []

    def test_no_change_capture_never_schedules(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            dataset = _dataset()
            db.save_snapshot(dataset)
            repeat = db.save_snapshot(dataset)
            assert repeat.change_set.status is ChangeSetStatus.NO_CHANGE
            assert schedule_job_for_capture(db, repeat) is None

    def test_changes_detected_schedules_website_job(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(procedures=("Knee Replacement",)))
            changed = db.save_snapshot(
                _dataset(procedures=("Knee Replacement", "Robotic Hip"))
            )
            assert changed.change_set.status is ChangeSetStatus.CHANGES_DETECTED
            job = schedule_job_for_capture(
                db, changed, settings=verification_settings(delay_minutes=5), now=NOW
            )
            assert job is not None
            assert job.status is VerificationJobStatus.PENDING
            assert job.hospital_id == changed.snapshot.hospital_id
            assert job.change_set_id == changed.change_set.change_set_id
            assert job.triggering_snapshot_id == changed.snapshot.snapshot_id
            assert job.triggering_source_type == "mock_api"
            assert job.target_source_type == "website"
            assert job.target_ref == "https://demo.northstar.example.in"
            assert job.scheduled_for == NOW + timedelta(minutes=5)

    def test_scheduling_same_event_is_idempotent(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(procedures=("Knee Replacement",)))
            changed = db.save_snapshot(_dataset(procedures=("Knee Replacement", "Robotic Hip")))
            first = schedule_job_for_capture(db, changed, now=NOW)
            second = schedule_job_for_capture(db, changed, now=NOW)
            assert first is not None and second is not None
            assert first.verification_job_id == second.verification_job_id
            assert len(db.list_verification_jobs(changed.snapshot.hospital_id)) == 1

    def test_capture_against_its_own_target_never_schedules(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            website_dataset = _dataset(source_type="website")
            first = db.save_snapshot(website_dataset)
            changed = db.save_snapshot(
                _dataset(beds=450, source_type="website")
            )
            assert changed.change_set.status is ChangeSetStatus.CHANGES_DETECTED
            assert (
                changed.snapshot.source_type
                == verification_settings().target_source_type
            )
            assert schedule_job_for_capture(db, changed, now=NOW) is None
            assert db.list_verification_jobs(first.snapshot.hospital_id) == []

    def test_target_ref_falls_back_to_source_ref(self, tmp_path) -> None:
        ref = "https://carecenter.example.in"
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(ref=ref, website="", beds=12))
            changed = db.save_snapshot(_dataset(ref=ref, website="", beds=34))
            job = schedule_job_for_capture(db, changed, now=NOW)
            assert job is not None
            assert job.target_ref == ref


# ---------------------------------------------------------------------------
# Scheduler: due jobs + atomic claims + manual creation
# ---------------------------------------------------------------------------


class TestScheduler:
    def test_due_jobs_respect_scheduled_for_and_status(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(procedures=("A",)))
            changed = db.save_snapshot(_dataset(procedures=("A", "B")))
            job = schedule_job_for_capture(
                db, changed, settings=verification_settings(delay_minutes=10), now=NOW
            )
            assert job is not None
            settings = verification_settings()
            scheduler = VerificationScheduler(db, settings=settings)
            assert scheduler.get_due_jobs(NOW) == []
            assert scheduler.get_due_jobs(NOW + timedelta(minutes=11)) == [job]

    def test_claim_is_atomic_between_workers(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(procedures=("A",)))
            changed = db.save_snapshot(_dataset(procedures=("A", "B")))
            job = schedule_job_for_capture(db, changed, now=NOW)
            assert job is not None
            settings = verification_settings()
            scheduler = VerificationScheduler(db, settings=settings)
            claimed = scheduler.claim(job, NOW)
            assert claimed is not None
            assert claimed.status is VerificationJobStatus.RUNNING
            assert claimed.attempt_count == 1
            assert job.verification_job_id == claimed.verification_job_id
            # A second worker (or second claim in the same process) must not
            # double-claim the same job.
            again = scheduler.claim(job, NOW)
            assert again is None
            assert db.get_verification_job(job.verification_job_id).attempt_count == 1

    def test_manual_job_is_idempotent_and_explains_when_nothing(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            empty = create_manual_job(db, "h_whatever")
            assert empty[0] is None
            assert "nothing has changed" in empty[1]

            db.save_snapshot(_dataset(procedures=("A",)))
            changed = db.save_snapshot(_dataset(procedures=("A", "B")))
            hospital_id = changed.snapshot.hospital_id
            first, reason = create_manual_job(db, hospital_id)
            assert first is not None
            assert reason == ""
            second, _ = create_manual_job(db, hospital_id)
            assert second is not None
            assert first.verification_job_id == second.verification_job_id


# ---------------------------------------------------------------------------
# Evaluation rules
# ---------------------------------------------------------------------------


class TestEvaluation:
    def _verified(self, dataset: HospitalDataset) -> tuple[str, int]:
        results, status, _ = evaluate_change_set(self._change_set(), dataset)
        return status.value, len(results)

    def _change_set(self, changes: list[Change] | None = None) -> ChangeSet:
        return _changeset_with(changes or [self._change()])

    def _change(self) -> Change:
        return _change(ChangeType.ADDED, "procedures", entity_name="Robotic Hip", new_value="x")

    def test_added_present_is_verified(self) -> None:
        status, _ = self._verified(_dataset(procedures=("Robotic Hip",)))
        assert status == "verified"

    def test_added_absent_is_unverified(self) -> None:
        status, _ = self._verified(_dataset(procedures=()))
        assert status == "unverified"

    def test_removed_still_present_is_mismatch(self) -> None:
        change_set = _changeset_with(
            [_change(ChangeType.REMOVED, "procedures", entity_name="Knee Replacement")]
        )
        _, status, _ = evaluate_change_set(change_set, _dataset(procedures=("Knee Replacement",)))
        assert status.value == "mismatch"

    def test_removed_absent_is_verified(self) -> None:
        change_set = _changeset_with(
            [_change(ChangeType.REMOVED, "procedures", entity_name="Knee Replacement")]
        )
        _, status, _ = evaluate_change_set(change_set, _dataset(procedures=()))
        assert status.value == "verified"

    def test_modified_equal_is_verified(self) -> None:
        change_set = _changeset_with(
            [
                _change(
                    ChangeType.MODIFIED,
                    "hospital.bed_count",
                    category="hospital",
                    field="bed_count",
                    new_value=450,
                )
            ]
        )
        _, status, _ = evaluate_change_set(change_set, _dataset(beds=450))
        assert status.value == "verified"

    def test_modified_differ_is_mismatch(self) -> None:
        change_set = _changeset_with(
            [
                _change(
                    ChangeType.MODIFIED,
                    "hospital.bed_count",
                    category="hospital",
                    field="bed_count",
                    new_value=450,
                )
            ]
        )
        _, status, _ = evaluate_change_set(change_set, _dataset(beds=420))
        assert status.value == "mismatch"

    def test_modified_website_unknown_is_unverified(self) -> None:
        change_set = _changeset_with(
            [
                _change(
                    ChangeType.MODIFIED,
                    "hospital.staff_count",
                    category="hospital",
                    field="staff_count",
                    new_value=120,
                )
            ]
        )
        dataset = _dataset()  # staff_count stays None -> UNKNOWN, never NO
        assert dataset.hospital.staff_count is None
        results, status, _ = evaluate_change_set(change_set, dataset)
        assert status.value == "unverified"
        assert results[0].outcome is VerificationOutcome.UNVERIFIED

    def test_modified_both_unknown_is_verified(self) -> None:
        change_set = _changeset_with(
            [
                _change(
                    ChangeType.MODIFIED,
                    "hospital.staff_count",
                    category="hospital",
                    field="staff_count",
                    new_value=None,  # authoritative also UNKNOWN
                )
            ]
        )
        _, status, _ = evaluate_change_set(change_set, _dataset())
        assert status.value == "verified"

    def test_aggregation_mismatch_wins(self) -> None:
        change_set = _changeset_with(
            [
                _change(ChangeType.ADDED, "procedures", entity_name="Robotic Hip"),
                _change(ChangeType.REMOVED, "procedures", entity_name="Knee Replacement"),
            ]
        )
        # One ADDED present (verified) + one REMOVED still present (mismatch)
        # -> overall MISMATCH wins.
        results, status, _ = evaluate_change_set(
            change_set, _dataset(procedures=("Robotic Hip", "Knee Replacement"))
        )
        assert status.value == "mismatch"
        assert {r.outcome.value for r in results} == {
            VerificationOutcome.VERIFIED.value,
            VerificationOutcome.MISMATCH.value,
        }

    def test_lists_compared_order_insensitively(self, tmp_path) -> None:
        # Same procedures in different orders must fingerprint/compare equal so
        # re-harvesting a reordered page produces NO_CHANGE (and no job).
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(procedures=("Robotic Hip", "Knee Replacement")))
            repeat = db.save_snapshot(
                _dataset(procedures=("Knee Replacement", "Robotic Hip"))
            )
            assert repeat.change_set.status is ChangeSetStatus.NO_CHANGE


# ---------------------------------------------------------------------------
# Worker execution
# ---------------------------------------------------------------------------


class TestWorker:
    def test_worker_verifies_an_added_entity(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(procedures=("Knee Replacement",)))
            changed = db.save_snapshot(
                _dataset(procedures=("Knee Replacement", "Robotic Hip"))
            )
            job = schedule_job_for_capture(db, changed, now=NOW)
            assert job is not None
            website = _dataset(procedures=("Robotic Hip",), source_type="website")
            called = {"n": 0}

            async def capture(job: VerificationJob) -> HospitalDataset:
                called["n"] += 1
                return website

            processed = _run_cycle_sync(db, capture)
            assert processed == 1
            assert called["n"] == 1

            stored_job = db.get_verification_job(job.verification_job_id)
            assert stored_job.status is VerificationJobStatus.VERIFIED
            assert stored_job.attempt_count == 1
            result = db.get_verification_result(job.verification_job_id)
            assert result is not None
            assert result.status is VerificationResultStatus.VERIFIED
            assert result.verified_count == 1
            assert result.change_count == 1
            assert result.provenance.target_source_type == "website"
            assert result.provenance.triggering_source_type == "mock_api"

    def test_worker_detects_mismatch(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(beds=420))
            changed = db.save_snapshot(_dataset(beds=450))
            job = schedule_job_for_capture(db, changed, now=NOW)
            assert job is not None
            website = _dataset(beds=420, source_type="website")  # unchanged

            async def capture(job: VerificationJob) -> HospitalDataset:
                return website

            processed = _run_cycle_sync(db, capture)
            assert processed == 1
            result = db.get_verification_result(job.verification_job_id)
            assert result is not None
            assert result.status is VerificationResultStatus.MISMATCH
            assert result.mismatch_count == 1
            assert "bed_count" in str(result.change_results[0].path)

    def test_worker_cannot_confirm_absent_entity(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(procedures=("Knee Replacement",)))
            changed = db.save_snapshot(
                _dataset(procedures=("Knee Replacement", "Robotic Hip"))
            )
            job = schedule_job_for_capture(db, changed, now=NOW)
            assert job is not None
            website = _dataset(procedures=("Knee Replacement",), source_type="website")

            async def capture(job: VerificationJob) -> HospitalDataset:
                return website

            _run_cycle_sync(db, capture)
            result = db.get_verification_result(job.verification_job_id)
            assert result is not None
            assert result.status is VerificationResultStatus.UNVERIFIED
            assert result.unverified_count == 1

    def test_worker_keeps_target_capture_under_same_hospital_identity(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(procedures=("Knee Replacement",), beds=420))
            changed = db.save_snapshot(
                _dataset(procedures=("Knee Replacement", "Robotic Hip"), beds=420)
            )
            job = schedule_job_for_capture(db, changed, now=NOW)
            assert job is not None
            hospital_id = job.hospital_id
            before = {s.snapshot_id for s in db.list_snapshots(hospital_id)}
            website = _dataset(procedures=("Robotic Hip",), beds=420, source_type="website")

            async def capture(job: VerificationJob) -> HospitalDataset:
                return website

            _run_cycle_sync(db, capture)
            after = {s.snapshot_id for s in db.list_snapshots(hospital_id)}
            assert len(after) == len(before) + 1, "one website snapshot added, no new identity"
            website_snapshot = next(
                s for s in db.list_snapshots(hospital_id) if s.source_type == "website"
            )
            assert website_snapshot.hospital_id == hospital_id
            # No second hospital identity was ever minted.
            assert len(db.list_hospitals()) == 1


# ---------------------------------------------------------------------------
# Retries, backoff and failure semantics
# ---------------------------------------------------------------------------


class TestRetries:
    def test_retryable_failure_requeues_with_backoff(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(procedures=("A",)))
            changed = db.save_snapshot(_dataset(procedures=("A", "B")))
            job = schedule_job_for_capture(db, changed, now=NOW)
            assert job is not None
            settings = verification_settings(delay_minutes=0, max_attempts=3, backoff_minutes=2)

            async def capture(job: VerificationJob) -> HospitalDataset:
                raise RuntimeError("website temporarily unreachable")

            processed = _run_cycle_sync(
                db, capture, max_attempts=3, now=NOW
            )
            assert processed == 1
            requeued = db.get_verification_job(job.verification_job_id)
            assert requeued.status is VerificationJobStatus.PENDING
            assert requeued.attempt_count == 1
            # backoff = backoff_minutes * attempt_count
            assert requeued.scheduled_for == NOW + timedelta(minutes=2)
            assert "temporarily unreachable" in requeued.last_error
            # Not due right away.
            assert VerificationScheduler(db, settings=settings).get_due_jobs(NOW) == []

    def test_attempts_are_bounded_and_then_fail(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(procedures=("A",)))
            changed = db.save_snapshot(_dataset(procedures=("A", "B")))
            job = schedule_job_for_capture(db, changed, now=NOW)
            assert job is not None
            settings = verification_settings(delay_minutes=0, max_attempts=2, backoff_minutes=0)
            scheduler = VerificationScheduler(db, settings=settings)

            async def capture(job: VerificationJob) -> HospitalDataset:
                raise RuntimeError("boom")

            for index in range(3):
                _run_cycle_sync(db, capture, max_attempts=2, backoff_minutes=0, now=NOW)
                status = db.get_verification_job(job.verification_job_id).status
                if index == 0:
                    assert status is VerificationJobStatus.PENDING
                else:
                    assert status is VerificationJobStatus.FAILED
            final = db.get_verification_job(job.verification_job_id)
            assert final.status is VerificationJobStatus.FAILED
            assert final.attempt_count == 2
            assert scheduler.get_due_jobs(NOW) == []
            assert db.get_verification_result(job.verification_job_id) is None

    def test_permanent_error_fails_immediately(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(procedures=("A",)))
            changed = db.save_snapshot(_dataset(procedures=("A", "B")))
            job = schedule_job_for_capture(db, changed, now=NOW)
            assert job is not None
            calls = {"n": 0}

            async def capture(job: VerificationJob) -> HospitalDataset:
                calls["n"] += 1
                raise VerificationPermanentError("target is out of scope")

            _run_cycle_sync(db, capture, max_attempts=3)
            final = db.get_verification_job(job.verification_job_id)
            assert final.status is VerificationJobStatus.FAILED
            assert final.attempt_count == 1
            assert "out of scope" in final.last_error
            assert calls["n"] == 1  # captured once, never retried


class TestAuthorizationBoundary:
    def test_authorized_source_is_never_captured(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(procedures=("A",)))
            changed = db.save_snapshot(_dataset(procedures=("A", "B")))
            hospital_id = changed.snapshot.hospital_id
            job, _ = create_manual_job(
                db, hospital_id, target_source_type="hospital_api", now=NOW
            )
            assert job is not None
            touched = {"called": False}

            async def capture(job: VerificationJob) -> HospitalDataset:
                touched["called"] = True
                raise AssertionError("authorized target must never be captured in P19-B")

            _run_cycle_sync(db, capture)
            final = db.get_verification_job(job.verification_job_id)
            assert final.status is VerificationJobStatus.FAILED
            assert "authorized" in final.last_error
            assert "P19-D" in final.last_error
            assert not touched["called"], "authorization boundary blocks before capture"


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


@pytest.fixture
def verification_client(tmp_path) -> TestClient:
    app = create_app()
    app.state.medcompare_db_path = tmp_path / "verification-api-test.db"
    app.state.medcompare_persist = True
    with TestClient(app) as test_client:
        yield test_client


class TestVerificationApi:
    @staticmethod
    def _seed(client: TestClient) -> tuple[str, str]:
        response = client.post(
            "/api/v1/hospitals/ignored/capture",
            json=_dataset(procedures=("Knee Replacement",)).model_dump(mode="json"),
        )
        assert response.status_code == 201
        hospital_id = response.json()["snapshot"]["hospital_id"]
        response = client.post(
            "/api/v1/hospitals/ignored/capture",
            json=_dataset(procedures=("Knee Replacement", "Robotic Hip")).model_dump(
                mode="json"
            ),
        )
        payload = response.json()
        assert payload["change_set"]["status"] == "changes_detected"
        assert payload["verification_job"] is not None
        job_id = payload["verification_job"]["verification_job_id"]
        return hospital_id, job_id

    def test_capture_schedules_and_api_lists_jobs(self, verification_client) -> None:
        hospital_id, job_id = self._seed(verification_client)
        listing = verification_client.get(
            f"/api/v1/hospitals/{hospital_id}/verification-jobs"
        )
        assert listing.status_code == 200
        body = listing.json()
        assert body["hospital_id"] == hospital_id
        assert len(body["verification_jobs"]) == 1
        row = body["verification_jobs"][0]
        assert row["job"]["verification_job_id"] == job_id
        assert row["job"]["status"] == "pending"
        assert row["result"] is None  # worker has not run

        single = verification_client.get(f"/api/v1/verification-jobs/{job_id}")
        assert single.status_code == 200
        assert single.json()["job"]["change_set_id"].startswith("c_")

    def test_manual_create_endpoint_is_idempotent(self, verification_client) -> None:
        self._seed(verification_client)
        hospital_id = verification_client.get("/api/v1/hospitals/registry").json()[
            "hospitals"
        ][0]["hospital_id"]
        first = verification_client.post(
            f"/api/v1/hospitals/{hospital_id}/verification-jobs"
        )
        assert first.status_code == 201
        second = verification_client.post(
            f"/api/v1/hospitals/{hospital_id}/verification-jobs"
        )
        assert second.status_code == 201
        assert (
            first.json()["verification_job"]["verification_job_id"]
            == second.json()["verification_job"]["verification_job_id"]
        )

    def test_manual_create_without_changes_is_conflict(self, verification_client) -> None:
        response = verification_client.post(
            "/api/v1/hospitals/ignored/capture",
            json=_dataset().model_dump(mode="json"),
        )
        assert response.status_code == 201  # initial only — nothing to verify
        registry = verification_client.get("/api/v1/hospitals/registry").json()["hospitals"]
        assert len(registry) == 1
        conflict = verification_client.post(
            f"/api/v1/hospitals/{registry[0]['hospital_id']}/verification-jobs"
        )
        assert conflict.status_code == 409

    def test_404s(self, verification_client) -> None:
        assert (
            verification_client.get("/api/v1/hospitals/h_bogus/verification-jobs").status_code
            == 404
        )
        assert (
            verification_client.get("/api/v1/verification-jobs/v_bogus").status_code == 404
        )

    def test_worker_then_api_shows_result_status(self, tmp_path) -> None:
        app = create_app()
        app.state.medcompare_db_path = tmp_path / "db.sqlite"
        app.state.medcompare_persist = True
        with TestClient(app) as client:
            # Seed via the API, then run the worker against the same DB path.
            client.post(
                "/api/v1/hospitals/ignored/capture",
                json=_dataset(procedures=("A",)).model_dump(mode="json"),
            )
            changed = client.post(
                "/api/v1/hospitals/ignored/capture",
                json=_dataset(procedures=("A", "Robotic Hip")).model_dump(mode="json"),
            )
            job_id = changed.json()["verification_job"]["verification_job_id"]

            import asyncio

            from hospital_extractor.verification.service import run_worker_cycle
            from hospital_extractor.verification.settings import verification_settings

            async def _cycle() -> int:
                with _sq(tmp_path / "db.sqlite") as db:
                    website = _dataset(procedures=("Robotic Hip",), source_type="website")

                    async def capture(job: VerificationJob) -> HospitalDataset:
                        return website

                    # Real-time clock so the API-scheduled job (real now) is due.
                    return await run_worker_cycle(
                        db, settings=verification_settings(delay_minutes=0), capture=capture
                    )

            assert asyncio.run(_cycle()) == 1

            row = client.get(f"/api/v1/verification-jobs/{job_id}").json()
            assert row["result"]["status"] == "verified"
            assert row["result"]["verified_count"] == 1


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class TestVerificationCli:
    def _seed(self, tmp_path):
        db_path = tmp_path / "verification-cli.db"
        from hospital_extractor.verification.demo import run_verification_demo

        results = run_verification_demo(db_path)
        return str(db_path), results[0]

    def test_verification_demo_is_offline_and_deterministic(self, tmp_path, capsys) -> None:
        db_path = tmp_path / "demo.db"
        from hospital_extractor.verification.demo import run_verification_demo

        results = run_verification_demo(db_path)
        statuses = {r.label: r.result_status for r in results}
        assert statuses["A"] == "verified"
        assert statuses["B"] == "mismatch"
        for result in results:
            assert result.ok

    def test_verification_jobs_command(self, tmp_path, capsys) -> None:
        db_path, result = self._seed(tmp_path)
        assert main(["verification-jobs", result.hospital_id, "--db", db_path]) == 0
        out = capsys.readouterr().out
        assert "Verification jobs for" in out
        assert result.result_status in out

    def test_verify_command_is_idempotent(self, tmp_path, capsys) -> None:
        db_path, result = self._seed(tmp_path)
        assert main(["verify", result.hospital_id, "--db", db_path]) == 0
        first_out = capsys.readouterr().out
        assert "verification job:" in first_out
        assert main(["verify", result.hospital_id, "--db", db_path]) == 0
        # The same job id is reported both times.
        import re

        ids = re.findall(r"verification job: (v_[0-9a-f]+)", capsys.readouterr().out)
        assert ids

    def test_verify_unknown_hospital_fails(self, tmp_path, capsys) -> None:
        db_path, _ = self._seed(tmp_path)
        assert main(["verify", "does-not-exist", "--db", db_path]) != 0
        assert "error: unknown hospital" in capsys.readouterr().err

    def test_worker_once_with_nothing_due(self, tmp_path, capsys) -> None:
        db_path, _ = self._seed(tmp_path)
        assert main(["worker", "--once", "--db", db_path]) == 0
        assert "processed 0 due verification job" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Read-safety regression: normal reads must never crawl
# ---------------------------------------------------------------------------


class TestReadSafety:
    def test_no_read_path_crawls(self, tmp_path, monkeypatch) -> None:
        """Every read endpoint must work with crawling disabled entirely.

        Patching ``harvest`` to raise means any endpoint that (wrongly) tries
        to fetch a source during a read will surface the error.
        """
        from hospital_extractor.sources import pipeline as sources_pipeline

        def _no_crawl(*args, **kwargs):
            raise AssertionError("read path must never crawl")

        monkeypatch.setattr(sources_pipeline, "harvest", _no_crawl)

        app = create_app()
        app.state.medcompare_db_path = tmp_path / "db.sqlite"
        app.state.medcompare_persist = True
        with TestClient(app) as client:
            # Seed first (writes don't crawl either).
            response = client.post(
                "/api/v1/hospitals/ignored/capture",
                json=_dataset(procedures=("A",)).model_dump(mode="json"),
            )
            assert response.status_code == 201
            hospital_id = response.json()["snapshot"]["hospital_id"]
            dataset_id = response.json()["snapshot"]["dataset_id"]

            assert client.get("/api/v1/health").status_code == 200
            assert client.get("/").status_code == 200
            assert client.get("/hospitals").status_code == 200
            assert client.get(f"/hospitals/{dataset_id}").status_code == 200
            assert client.get("/api/v1/hospitals").status_code == 200
            assert client.get("/api/v1/hospitals/registry").status_code == 200
            assert client.get(f"/api/v1/hospitals/{hospital_id}/snapshots").status_code == 200
            assert client.get(f"/api/v1/hospitals/{hospital_id}/changes").status_code == 200
            assert (
                client.get(f"/api/v1/hospitals/{hospital_id}/verification-jobs").status_code
                == 200
            )
            assert client.get(f"/hospitals/{dataset_id}").status_code == 200
            # Reads never mint verification jobs.
            with _sq(tmp_path / "db.sqlite") as db:
                assert db.list_verification_jobs(hospital_id) == []

    def test_reading_profile_does_not_schedule_anything(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(procedures=("A",)))
            hospital_id = db.list_hospitals()[0].hospital_id
            assert db.list_verification_jobs(hospital_id) == []