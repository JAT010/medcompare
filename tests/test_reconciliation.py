"""P19-C reconciliation workflow tests.

Covers: automatic case creation rules (MISMATCH always, UNVERIFIED opt-in,
VERIFIED/FAILED never), idempotency per verification result, the state
machine (allowed/forbidden transitions, reopen-without-dup), resolutions,
field-level items, evidence packages, the immutability guarantee, the API and
CLI surfaces, and the worker auto-creation hook.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from hospital_extractor.api.app import create_app
from hospital_extractor.cli import main
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.metadata import FetchStatus, SourceMetadata, SourceType
from hospital_extractor.models.procedure import Procedure
from hospital_extractor.models.verification import VerificationJob, VerificationResultStatus
from hospital_extractor.reconciliation.models import (
    ReconciliationCase,
    ReconciliationCaseStatus,
    ReconciliationTrigger,
    ResolutionType,
)
from hospital_extractor.reconciliation.service import (
    ReconciliationStateError,
    build_evidence_package,
    create_cases_for_result,
    create_manual_case,
    dismiss_case,
    get_case,
    list_cases,
    list_events,
    reopen_case,
    resolve_case,
    start_review,
)
from hospital_extractor.storage.database import HospitalDatabase
from hospital_extractor.verification.service import run_worker_cycle, schedule_job_for_capture
from hospital_extractor.verification.settings import verification_settings

NOW = datetime(2026, 9, 3, 9, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# helpers (mirroring the verification test suite)
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
            Procedure(name=p, source_url=website, source_type=source_type) for p in procedures
        ],
        technologies=[],
        accreditations=[],
    )


def _sq(db_path) -> HospitalDatabase:
    return HospitalDatabase(db_path)


def _run_cycle_sync(
    db: HospitalDatabase,
    capture,
    *,
    max_attempts: int = 3,
    backoff_minutes: int = 2,
    now: datetime = NOW,
) -> int:
    import asyncio

    async def cycle() -> int:
        return await run_worker_cycle(
            db,
            settings=verification_settings(
                delay_minutes=0, max_attempts=max_attempts, backoff_minutes=backoff_minutes
            ),
            capture=capture,
            now=now,
        )

    return asyncio.run(cycle())


def _seed_mismatch(db: HospitalDatabase) -> tuple[VerificationJob, ReconciliationCase]:
    """Run the worker on a real change to produce a MISMATCH + its case."""
    db.save_snapshot(_dataset(beds=420))
    changed = db.save_snapshot(_dataset(beds=450))
    job = schedule_job_for_capture(db, changed, now=NOW)
    assert job is not None

    async def capture(job: VerificationJob) -> HospitalDataset:
        return _dataset(beds=420, source_type="website")

    _run_cycle_sync(db, capture)
    result = db.get_verification_result(job.verification_job_id)
    assert result is not None
    assert result.status is VerificationResultStatus.MISMATCH
    case = db.get_reconciliation_case_for_result(result.verification_result_id)
    assert case is not None
    return job, case


# ---------------------------------------------------------------------------
# Automatic case-creation policy
# ---------------------------------------------------------------------------

class TestCaseCreationPolicy:
    def test_mismatch_auto_opens_case(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            _job, case = _seed_mismatch(db)
            assert case.status is ReconciliationCaseStatus.OPEN
            assert case.trigger is ReconciliationTrigger.MISMATCH
            assert case.created_by == "system"
            assert case.item_count == 1
            item = case.items[0]
            assert item.path == "hospital.bed_count"
            assert item.previous_value == 420
            assert item.authoritative_value == 450
            assert item.verification_value == 420

    def test_verified_never_opens_as_auto(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(procedures=("Knee Replacement",)))
            changed = db.save_snapshot(_dataset(procedures=("Knee Replacement", "Robotic Hip")))
            job = schedule_job_for_capture(db, changed, now=NOW)
            assert job is not None

            async def capture(job: VerificationJob) -> HospitalDataset:
                return _dataset(
                    procedures=("Knee Replacement", "Robotic Hip"), source_type="website"
                )

            _run_cycle_sync(db, capture)
            assert db.list_reconciliation_cases() == []

    def test_unverified_no_case_by_default(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(procedures=("Knee Replacement",)))
            changed = db.save_snapshot(_dataset(procedures=("Knee Replacement", "Robotic Hip")))
            job = schedule_job_for_capture(db, changed, now=NOW)
            assert job is not None

            async def capture(job: VerificationJob) -> HospitalDataset:
                return _dataset(procedures=("Knee Replacement",), source_type="website")

            _run_cycle_sync(db, capture)
            assert db.list_reconciliation_cases() == []

    def test_unverified_opens_case_when_opted_in(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("RECONCILIATION_CREATE_UNVERIFIED_CASES", "1")
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(procedures=("Knee Replacement",)))
            changed = db.save_snapshot(_dataset(procedures=("Knee Replacement", "Robotic Hip")))
            job = schedule_job_for_capture(db, changed, now=NOW)
            assert job is not None

            async def capture(job: VerificationJob) -> HospitalDataset:
                return _dataset(procedures=("Knee Replacement",), source_type="website")

            _run_cycle_sync(db, capture)
            cases = db.list_reconciliation_cases()
            assert len(cases) == 1
            assert cases[0].trigger is ReconciliationTrigger.UNVERIFIED

    def test_failed_result_never_opens(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(procedures=("Knee Replacement",)))
            changed = db.save_snapshot(_dataset(procedures=("Knee Replacement", "Robotic Hip")))
            job = schedule_job_for_capture(db, changed, now=NOW)
            assert job is not None

            async def capture(job: VerificationJob) -> HospitalDataset:
                raise RuntimeError("boom")

            _run_cycle_sync(db, capture, max_attempts=1)
            assert db.list_reconciliation_cases() == []

    def test_processed_twice_is_single_case(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            _job, case = _seed_mismatch(db)
            result = db.get_verification_result(_job.verification_job_id)
            assert result is not None
            again = create_cases_for_result(db, result, actor="system", now=NOW)
            assert [c.reconciliation_case_id for c in again] == [case.reconciliation_case_id]
            assert len(db.list_reconciliation_cases()) == 1

    def test_manual_case_allows_verified_result(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(_dataset(procedures=("Knee Replacement",)))
            changed = db.save_snapshot(_dataset(procedures=("Knee Replacement", "Robotic Hip")))
            job = schedule_job_for_capture(db, changed, now=NOW)
            assert job is not None

            async def capture(job: VerificationJob) -> HospitalDataset:
                return _dataset(procedures=("Robotic Hip",), source_type="website")

            _run_cycle_sync(db, capture)
            result = db.get_verification_result(job.verification_job_id)
            assert result is not None
            assert result.status is VerificationResultStatus.VERIFIED
            case, reason = create_manual_case(db, result.hospital_id, actor="manual", now=NOW)
            assert case is not None
            assert reason == ""
            assert case.trigger is ReconciliationTrigger.MANUAL

    def test_manual_case_without_result_reports_reason(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            case, reason = create_manual_case(db, "h_unknown", actor="manual", now=NOW)
            assert case is None
            assert "no verification result" in reason

    def test_manual_case_is_idempotent(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            _job, case = _seed_mismatch(db)
            again, _ = create_manual_case(db, case.hospital_id, actor="manual", now=NOW)
            assert again is not None
            assert again.reconciliation_case_id == case.reconciliation_case_id
            assert len(db.list_reconciliation_cases()) == 1


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class TestStateMachine:
    def test_full_resolve_lifecycle(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            _job, case = _seed_mismatch(db)
            reviewed = start_review(db, case.reconciliation_case_id, actor="manual", now=NOW)
            assert reviewed.status is ReconciliationCaseStatus.UNDER_REVIEW
            assert reviewed.assigned_to is None
            resolved = resolve_case(
                db,
                case.reconciliation_case_id,
                resolution=ResolutionType.AUTHORITATIVE_SOURCE_CONFIRMED,
                reason="authoritative capture was confirmed against hospital records",
                actor="manual",
                now=NOW,
            )
            assert resolved.status is ReconciliationCaseStatus.RESOLVED
            assert resolved.resolution is ResolutionType.AUTHORITATIVE_SOURCE_CONFIRMED
            assert resolved.resolution_reason == (
                "authoritative capture was confirmed against hospital records"
            )
            assert resolved.resolved_at == NOW

    def test_direct_resolve_from_open(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            _job, case = _seed_mismatch(db)
            resolved = resolve_case(
                db,
                case.reconciliation_case_id,
                resolution=ResolutionType.FALSE_POSITIVE,
                reason="verification fixture seeded the wrong value",
                actor="manual",
                now=NOW,
            )
            assert resolved.status is ReconciliationCaseStatus.RESOLVED

    def test_reopen_then_review_then_dismiss(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            _job, case = _seed_mismatch(db)
            resolve_case(
                db,
                case.reconciliation_case_id,
                resolution=ResolutionType.SOURCE_DISAGREEMENT,
                reason="sources genuinely disagree",
                actor="manual",
                now=NOW,
            )
            reopened = reopen_case(
                db, case.reconciliation_case_id, reason="new evidence arrived", actor="manual",
                now=NOW,
            )
            assert reopened.status is ReconciliationCaseStatus.REOPENED
            assert reopened.resolution is None
            assert reopened.resolution_reason is None
            assert reopened.assigned_to is None
            reviewed = start_review(
                db,
                case.reconciliation_case_id,
                actor="manual",
                assigned_to="rebecca",
                reason="reassessment",
                now=NOW,
            )
            assert reviewed.assigned_to == "rebecca"
            dismissed = dismiss_case(
                db, case.reconciliation_case_id, reason="no longer an issue", actor="manual",
                now=NOW,
            )
            assert dismissed.status is ReconciliationCaseStatus.DISMISSED

    def test_dismissed_then_reopen_keeps_same_case(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            _job, case = _seed_mismatch(db)
            dismissed = dismiss_case(
                db, case.reconciliation_case_id, reason="not actionable", actor="manual", now=NOW
            )
            assert dismissed.status is ReconciliationCaseStatus.DISMISSED
            reopened = reopen_case(
                db, case.reconciliation_case_id, reason="was actionable", actor="manual", now=NOW
            )
            assert reopened.reconciliation_case_id == case.reconciliation_case_id
            assert reopened.status is ReconciliationCaseStatus.REOPENED
            assert len(db.list_reconciliation_cases()) == 1

    def test_forbidden_transitions_raise(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            _job, case = _seed_mismatch(db)
            case_id = case.reconciliation_case_id
            # OPEN -> REOPENED is illegal.
            with pytest.raises(ReconciliationStateError) as excinfo:
                reopen_case(db, case_id, reason="nope", actor="manual", now=NOW)
            assert excinfo.value.current is ReconciliationCaseStatus.OPEN
            assert excinfo.value.requested is ReconciliationCaseStatus.REOPENED
            assert "allowed" in str(excinfo.value)

            resolve_case(
                db,
                case_id,
                resolution=ResolutionType.WEBSITE_CONFIRMED,
                reason="website value matches reality",
                actor="manual",
                now=NOW,
            )
            # RESOLVED -> REVIEW is illegal.
            with pytest.raises(ReconciliationStateError):
                start_review(db, case_id, actor="manual", now=NOW)
            # RESOLVED -> RESOLVE is illegal.
            with pytest.raises(ReconciliationStateError):
                resolve_case(
                    db, case_id, resolution=ResolutionType.FALSE_POSITIVE, reason="x",
                    actor="manual", now=NOW,
                )

    def test_unknown_case_raises_value_error(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            with pytest.raises(ValueError):
                resolve_case(
                    db, "rec_nope", resolution=ResolutionType.FALSE_POSITIVE, reason="x",
                    actor="manual", now=NOW,
                )

    def test_resolution_requires_reason(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            _job, case = _seed_mismatch(db)
            with pytest.raises(ValueError):
                resolve_case(
                    db, case.reconciliation_case_id, resolution="website_confirmed", reason="  ",
                    actor="manual", now=NOW,
                )

    def test_invalid_resolution_string_rejected(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            _job, case = _seed_mismatch(db)
            with pytest.raises(ValueError):
                resolve_case(
                    db, case.reconciliation_case_id, resolution="bogus", reason="x",
                    actor="manual", now=NOW,
                )


# ---------------------------------------------------------------------------
# Items + evidence
# ---------------------------------------------------------------------------


class TestItemsAndEvidence:
    def test_multi_change_mismatch_items(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            db.save_snapshot(
                _dataset(procedures=("Knee Replacement", "Robotic Hip"), beds=420)
            )
            changed = db.save_snapshot(
                _dataset(procedures=("Knee Replacement",), beds=450)
            )
            job = schedule_job_for_capture(db, changed, now=NOW)
            assert job is not None

            async def capture(job: VerificationJob) -> HospitalDataset:
                # Website unchanged: beds still 420 (MODIFIED mismatch) and
                # still lists Robotic Hip (REMOVED mismatch).
                return _dataset(
                    procedures=("Knee Replacement", "Robotic Hip"), beds=420,
                    source_type="website",
                )

            _run_cycle_sync(db, capture)
            result = db.get_verification_result(job.verification_job_id)
            assert result is not None
            assert result.status is VerificationResultStatus.MISMATCH
            assert result.mismatch_count == 2
            case = db.get_reconciliation_case_for_result(result.verification_result_id)
            assert case is not None
            assert case.item_count == 2
            paths = {item.path for item in case.items}
            assert paths == {"hospital.bed_count", "procedures"}

    def test_evidence_package_references_snapshots(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            _job, case = _seed_mismatch(db)
            evidence = build_evidence_package(db, case)
            assert evidence["case"]["reconciliation_case_id"] == case.reconciliation_case_id
            assert evidence["result"] is not None
            assert evidence["result"]["status"] == "mismatch"
            assert evidence["job"] is not None
            assert evidence["change_set"] is not None
            triggering = evidence["triggering_snapshot"]
            assert triggering is not None
            assert "snapshot_id" in triggering
            assert "payload" not in triggering
            previous = evidence["previous_snapshot"]
            assert previous is not None and previous["snapshot_id"] != triggering["snapshot_id"]
            verification = evidence["verification_snapshot"]
            assert verification is not None
            assert verification["captured_at"]

    def test_resolution_does_not_mutate_snapshots(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            _job, case = _seed_mismatch(db)
            snapshots_before = db.list_snapshots(case.hospital_id)
            datasets_before = db.list_datasets()
            resolve_case(
                db,
                case.reconciliation_case_id,
                resolution=ResolutionType.INSUFFICIENT_EVIDENCE,
                reason="not enough evidence to act",
                actor="manual",
                now=NOW,
            )
            assert db.list_snapshots(case.hospital_id) == snapshots_before
            assert db.list_datasets() == datasets_before


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


class TestAuditTrail:
    def test_created_event_then_transition_events(self, tmp_path) -> None:
        with _sq(tmp_path / "db.sqlite") as db:
            _job, case = _seed_mismatch(db)
            events = list_events(db, case.reconciliation_case_id)
            assert [e.action for e in events] == ["created"]
            assert events[0].actor == "system"
            assert events[0].to_status is ReconciliationCaseStatus.OPEN
            assert events[0].from_status is None

            start_review(db, case.reconciliation_case_id, actor="manual", now=NOW)
            resolve_case(
                db, case.reconciliation_case_id, resolution="source_disagreement",
                reason="in dispute", actor="manual", now=NOW,
            )
            reopened = reopen_case(
                db, case.reconciliation_case_id, reason="re-check", actor="manual", now=NOW
            )
            events = list_events(db, case.reconciliation_case_id)
            expected = ["created", "review_started", "resolved", "reopened"]
            assert [e.action for e in events] == expected
            assert events[-1].from_status is ReconciliationCaseStatus.RESOLVED
            assert events[-1].to_status is ReconciliationCaseStatus.REOPENED
            assert reopened.updated_at == NOW


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


class TestReconciliationApi:
    def _client(self, db_path: str):
        app = create_app()
        app.state.medcompare_db_path = db_path
        app.state.medcompare_persist = True
        return TestClient(app)

    def test_list_cases_for_hospital(self, tmp_path) -> None:
        db_path = str(tmp_path / "x.db")
        with _sq(db_path) as db:
            _job, case = _seed_mismatch(db)
            hospital_id = case.hospital_id
        client = self._client(db_path)
        response = client.get(f"/api/v1/hospitals/{hospital_id}/reconciliation-cases")
        assert response.status_code == 200
        rows = response.json()["cases"]
        assert len(rows) == 1
        assert rows[0]["case"]["status"] == "open"
        assert rows[0]["case"]["item_count"] == 1

    def test_list_unknown_hospital_404(self, tmp_path) -> None:
        client = self._client(str(tmp_path / "y.db"))
        assert client.get("/api/v1/hospitals/h_nope/reconciliation-cases").status_code == 404

    def test_get_case_404(self, tmp_path) -> None:
        client = self._client(str(tmp_path / "z.db"))
        assert client.get("/api/v1/reconciliation-cases/rec_nope").status_code == 404

    def test_review_and_resolve_via_api(self, tmp_path) -> None:
        db_path = str(tmp_path / "w.db")
        with _sq(db_path) as db:
            _job, case = _seed_mismatch(db)
            case_id = case.reconciliation_case_id
        client = self._client(db_path)
        reviewed = client.post(
            f"/api/v1/reconciliation-cases/{case_id}/review",
            json={"assigned_to": "rebecca", "reason": "looks important"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["case"]["status"] == "under_review"
        resolved = client.post(
            f"/api/v1/reconciliation-cases/{case_id}/resolve",
            json={"resolution": "authoritative_source_confirmed", "reason": "confirmed"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["case"]["status"] == "resolved"
        assert resolved.json()["case"]["resolution"] == "authoritative_source_confirmed"

    def test_invalid_transition_409(self, tmp_path) -> None:
        db_path = str(tmp_path / "v.db")
        with _sq(db_path) as db:
            _job, case = _seed_mismatch(db)
            case_id = case.reconciliation_case_id
        client = self._client(db_path)
        response = client.post(
            f"/api/v1/reconciliation-cases/{case_id}/reopen", json={"reason": "nope"}
        )
        assert response.status_code == 409
        assert "case_id" in response.json()

    def test_resolve_without_reason_422(self, tmp_path) -> None:
        db_path = str(tmp_path / "u.db")
        with _sq(db_path) as db:
            _job, case = _seed_mismatch(db)
            case_id = case.reconciliation_case_id
        client = self._client(db_path)
        response = client.post(
            f"/api/v1/reconciliation-cases/{case_id}/resolve",
            json={"resolution": "false_positive"},
        )
        assert response.status_code == 422

    def test_reopen_after_resolve_via_api(self, tmp_path) -> None:
        db_path = str(tmp_path / "t.db")
        with _sq(db_path) as db:
            _job, case = _seed_mismatch(db)
            case_id = case.reconciliation_case_id
        client = self._client(db_path)
        client.post(
            f"/api/v1/reconciliation-cases/{case_id}/resolve",
            json={"resolution": "false_positive", "reason": "fixture noise"},
        )
        reopened = client.post(
            f"/api/v1/reconciliation-cases/{case_id}/reopen", json={"reason": "was real"}
        )
        assert reopened.status_code == 200
        assert reopened.json()["case"]["status"] == "reopened"

    def test_html_page_renders(self, tmp_path) -> None:
        db_path = str(tmp_path / "s.db")
        with _sq(db_path) as db:
            _job, case = _seed_mismatch(db)
            case_id = case.reconciliation_case_id
        client = self._client(db_path)
        page = client.get(f"/reconciliation-cases/{case_id}")
        assert page.status_code == 200
        assert "Reconciliation case" in page.text
        assert "action-form" in page.text

    def test_html_action_form_resolves_and_redirects(self, tmp_path) -> None:
        db_path = str(tmp_path / "r.db")
        with _sq(db_path) as db:
            _job, case = _seed_mismatch(db)
            case_id = case.reconciliation_case_id
        client = self._client(db_path)
        response = client.post(
            f"/reconciliation-cases/{case_id}/actions",
            data={
                "action": "resolve",
                "resolution": "website_confirmed",
                "reason": "website is right",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == f"/reconciliation-cases/{case_id}"
        case_json = client.get(f"/api/v1/reconciliation-cases/{case_id}").json()["case"]
        assert case_json["status"] == "resolved"

    def test_html_profile_shows_reconciliation(self, tmp_path) -> None:
        db_path = str(tmp_path / "q.db")
        with _sq(db_path) as db:
            _job, case = _seed_mismatch(db)
            dataset_id = db.list_datasets()[0].dataset_id
        client = self._client(db_path)
        page = client.get(f"/hospitals/{dataset_id}")
        assert page.status_code == 200
        assert "Reconciliation" in page.text


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class TestReconciliationCli:
    def _seed(self, tmp_path) -> str:
        db_path = tmp_path / "recon-cli.db"
        with _sq(db_path) as db:
            _job, case = _seed_mismatch(db)
        return str(db_path)

    def test_reconciliation_cases_command(self, tmp_path, capsys) -> None:
        db_path = self._seed(tmp_path)
        with _sq(db_path) as db:
            hospital_id = list_cases(db)[0].hospital_id
        assert main(["reconciliation-cases", hospital_id, "--db", db_path]) == 0
        out = capsys.readouterr().out
        assert "Reconciliation cases for" in out
        assert "trigger=mismatch" in out

    def test_reconciliation_show_command(self, tmp_path, capsys) -> None:
        db_path = self._seed(tmp_path)
        with _sq(db_path) as db:
            case_id = list_cases(db)[0].reconciliation_case_id
        assert main(["reconciliation", case_id, "--db", db_path]) == 0
        out = capsys.readouterr().out
        assert case_id in out
        assert "Audit trail:" in out
        assert "created" in out

    def test_resolve_and_reopen_via_cli(self, tmp_path, capsys) -> None:
        db_path = self._seed(tmp_path)
        with _sq(db_path) as db:
            case_id = list_cases(db)[0].reconciliation_case_id
        assert (
            main(
                [
                    "resolve", case_id, "--db", db_path,
                    "--resolution", "source_disagreement", "--reason", "genuine conflict",
                ]
            )
            == 0
        )
        out = capsys.readouterr().out
        assert "-> resolved" in out
        assert main(["reopen", case_id, "--db", db_path, "--reason", "re-examine"]) == 0
        assert "-> reopened" in capsys.readouterr().out
        with _sq(db_path) as db:
            case = get_case(db, case_id)
            assert case is not None and case.status is ReconciliationCaseStatus.REOPENED

    def test_resolve_unknown_case_fails(self, tmp_path, capsys) -> None:
        db_path = self._seed(tmp_path)
        assert (
            main(
                [
                    "resolve", "rec_nope", "--db", db_path,
                    "--resolution", "false_positive", "--reason", "x",
                ]
            )
            != 0
        )
        assert "error:" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Demo integration (verification-demo shows the workflow end to end)
# ---------------------------------------------------------------------------


class TestDemoIntegration:
    def test_demo_full_workflow(self, tmp_path) -> None:
        from hospital_extractor.verification.demo import run_verification_demo

        results = run_verification_demo(tmp_path / "demo.db")
        by_label = {result.label: result for result in results}
        assert set(by_label) == {"A", "B", "C"}

        # Scenario A: VERIFIED — no review required.
        a = by_label["A"]
        assert a.result_status == "verified"
        assert a.reconciliation_case_id is None

        # Scenario B: MISMATCH -> case opened by the worker, reviewed, resolved.
        b = by_label["B"]
        assert b.result_status == "mismatch"
        assert b.reconciliation_case_id is not None
        assert b.reconciliation_status == "resolved"
        assert b.reconciliation_trigger == "mismatch"
        assert b.reconciliation_resolution == "authoritative_source_confirmed"
        assert b.reconciliation_events >= 3

        # Scenario C: UNVERIFIED (opt-in) -> reviewable open case.
        c = by_label["C"]
        assert c.result_status == "unverified"
        assert c.reconciliation_case_id is not None
        assert c.reconciliation_status == "open"
        assert c.reconciliation_trigger == "unverified"
        assert c.reconciliation_resolution is None

    def test_verification_demo_cli_reports_reconciliation(self, tmp_path, capsys) -> None:
        db_path = tmp_path / "cli-demo.db"
        assert main(["verification-demo", "--db", str(db_path)]) == 0
        out = capsys.readouterr().out
        assert "→ resolved" in out or "-> resolved" in out or "reconciliation resolved" in out
        assert "reconciliation:" in out


# ---------------------------------------------------------------------------
# Worker auto-creation hook does not fire for FAILED jobs
# ---------------------------------------------------------------------------


class TestWorkerIntegration:
    def test_no_case_for_failed_job(self, tmp_path) -> None:
        db_path = str(tmp_path / "fail.db")
        with _sq(db_path) as db:
            db.save_snapshot(_dataset(procedures=("Knee Replacement",)))
            changed = db.save_snapshot(
                _dataset(procedures=("Knee Replacement", "Robotic Hip"))
            )
            job = schedule_job_for_capture(db, changed, now=NOW)
            assert job is not None

            async def capture(job: VerificationJob) -> HospitalDataset:
                raise RuntimeError("network down")

            _run_cycle_sync(db, capture, max_attempts=1)
            assert db.list_reconciliation_cases() == []