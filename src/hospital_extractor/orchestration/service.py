"""Orchestration service: end-to-end run coordination for P19-A through P19-D.

This service composes existing services without duplicating them:

    Hospital/source
          ↓
    Capture
          ↓
    Snapshot
          ↓
    Change Detection
          ↓
    Verification
          ↓
    Reconciliation
          ↓
    Evidence Report
          ↓
    Authorized Submission
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from hospital_extractor.config import load_config
from hospital_extractor.models.change import ChangeSetStatus
from hospital_extractor.models.verification import VerificationResultStatus
from hospital_extractor.orchestration.models import (
    OrchestrationEvent,
    OrchestrationRun,
    OrchestrationStage,
    OrchestrationStatus,
)
from hospital_extractor.orchestration.settings import (
    OrchestrationSettings,
    orchestration_settings,
)
from hospital_extractor.reporting.models import (
    ReportType,
)
from hospital_extractor.sources.pipeline import harvest
from hospital_extractor.sources.registry import get_default_registry
from hospital_extractor.storage import HospitalDatabase, default_database_path

__all__ = [
    "OrchestrationError",
    "OrchestrationService",
    "run_orchestration",
    "resume_orchestration",
]


class OrchestrationError(RuntimeError):
    """Orchestration failed."""

    def __init__(
        self,
        run_id: str,
        stage: str,
        message: str,
        error_class: str | None = None,
    ) -> None:
        self.run_id = run_id
        self.stage = stage
        self.error_class = error_class
        super().__init__(f"Orchestration {run_id} failed at {stage}: {message}")


class OrchestrationService:
    """End-to-end orchestration service for P19-A through P19-D."""

    def __init__(
        self,
        db: HospitalDatabase,
        *,
        settings: OrchestrationSettings | None = None,
        registry: Any = None,
    ) -> None:
        self.db = db
        self.settings = settings or orchestration_settings()
        self.registry = registry or get_default_registry()

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _record_event(
        self,
        run: OrchestrationRun,
        stage: str,
        event_type: str,
        message: str | None = None,
        duration_ms: int | None = None,
        artifact_id: str | None = None,
        artifact_type: str | None = None,
        error: str | None = None,
        error_class: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrchestrationEvent:
        event = OrchestrationEvent(
            event_id=f"evt_{uuid4().hex[:12]}",
            run_id=run.run_id,
            stage=stage,
            event_type=event_type,
            message=message,
            duration_ms=duration_ms,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            error=error,
            error_class=error_class,
            metadata=metadata or {},
            created_at=self._now(),
            created_by="system",
        )
        return event

    def _transition_stage(
        self,
        run: OrchestrationRun,
        new_stage: str,
        *,
        message: str | None = None,
        error: str | None = None,
        error_class: str | None = None,
    ) -> None:
        if not run.can_transition_to(new_stage):
            raise OrchestrationError(
                run.run_id,
                run.current_stage,
                f"Invalid stage transition: {run.current_stage} -> {new_stage}",
            )
        run.current_stage = new_stage
        run.updated_at = self._now()

    async def _run_capture_stage(
        self,
        run: OrchestrationRun,
        hospital_id: str,
        source_type: str,
        target_ref: str,
        config: Any,
    ) -> tuple[str, str]:
        """Run the capture stage through the registered connector (P21).

        All source types (website / hospital_api / json_feed / mock_api) go
        through ``harvest`` → SourceRegistry → connector so acquisition is an
        implementation detail of the source, not a branch that duplicates
        downstream snapshot/change logic.
        """
        stage_start = time.perf_counter()

        harvested = await harvest(
            [target_ref],
            source_type=source_type,
            registry=self.registry,
        )
        dataset = harvested[0].dataset

        # Save snapshot
        with HospitalDatabase(default_database_path()) as db:
            capture = db.save_snapshot(dataset, hospital_id=hospital_id)
            snapshot_id = capture.snapshot.snapshot_id
            change_set = capture.change_set
            change_set_id = change_set.change_set_id
            _ = change_set.status.value  # available for metrics if needed

        # Record metrics
        duration_ms = int((time.perf_counter() - stage_start) * 1000)
        run.metrics.setdefault("stage_durations_ms", {})["capture"] = duration_ms

        return snapshot_id, change_set_id

    async def _run_change_detection_stage(
        self,
        run: OrchestrationRun,
    ) -> str:
        """Run the change detection stage."""
        stage_start = time.perf_counter()
        run.metrics.setdefault("stage_durations_ms", {})["change_detection"] = int(
            (time.perf_counter() - stage_start) * 1000
        )
        return run.change_set_id or ""

    async def _run_verification_stage(
        self,
        run: OrchestrationRun,
    ) -> str:
        """Run the verification stage."""
        stage_start = time.perf_counter()

        if not run.change_set_id:
            run.verification_result_status = "no_job"
            return ""

        # Get the change set and run verification
        with HospitalDatabase(default_database_path()) as db:
            change_set = db.get_change_set(run.change_set_id)
            if change_set is None:
                raise OrchestrationError(
                    run.run_id,
                    "change_detection",
                    f"Change set {run.change_set_id} not found",
                )

            # Schedule verification job
            from hospital_extractor.verification.service import (
                run_worker_cycle,
                schedule_job_for_capture,
            )
            from hospital_extractor.verification.settings import verification_settings

            # Schedule verification job
            job = schedule_job_for_capture(
                HospitalDatabase(default_database_path()),
                type("Capture", (), {
                    "snapshot": type("Snapshot", (), {
                        "hospital_id": run.hospital_id,
                        "snapshot_id": run.snapshot_id,
                        "source_type": run.source_type,
                    }),
                    "change_set": type("ChangeSet", (), {
                        "status": type("Status", (), {"value": "changes_detected"}),
                        "change_set_id": run.change_set_id,
                    }),
                })()
            )

            if job is None:
                run.metrics["verification_job_created"] = False
                run.verification_result_status = "no_job"
                return ""

            run.verification_job_id = job.verification_job_id
            run.metrics["verification_job_created"] = True

            # Run worker cycle to process the job
            with HospitalDatabase(default_database_path()) as db:
                await run_worker_cycle(
                    db,
                    settings=verification_settings(),
                    registry=None,
                    capture=None,
                )

            # Get verification result
            result = db.get_verification_result(run.verification_job_id)
            if result:
                run.verification_result_id = result.verification_result_id
                run.verification_result_status = result.status.value
                run.metrics["verified_count"] = result.verified_count
                run.metrics["mismatch_count"] = result.mismatch_count
                run.metrics["unverified_count"] = result.unverified_count

        run.metrics.setdefault("stage_durations_ms", {})["verification"] = int(
            (time.perf_counter() - stage_start) * 1000
        )
        return run.verification_result_id or ""

    async def _run_reconciliation_stage(
        self,
        run: OrchestrationRun,
    ) -> str:
        """Run the reconciliation stage."""
        stage_start = time.perf_counter()

        with HospitalDatabase(default_database_path()) as db:
            if run.verification_result_id:
                from hospital_extractor.reconciliation.service import (
                    create_cases_for_result,
                )
                from hospital_extractor.reconciliation.settings import (
                    reconciliation_settings,
                )

                result = db.get_verification_result(run.verification_result_id)
                if result:
                    from hospital_extractor.reconciliation.service import (
                        create_cases_for_result,
                    )
                    from hospital_extractor.reconciliation.settings import (
                        reconciliation_settings,
                    )

                    cases = create_cases_for_result(
                        db,
                        result,
                        settings=reconciliation_settings(),
                        actor="system",
                    )
                    if cases:
                        run.reconciliation_case_id = cases[0].reconciliation_case_id
                        run.reconciliation_case_status = cases[0].status.value
                        run.metrics["reconciliation_case_created"] = True
                        run.metrics["reconciliation_case_id"] = cases[0].reconciliation_case_id
                        run.metrics["reconciliation_trigger"] = cases[0].trigger.value
                        run.metrics["reconciliation_events"] = len(cases[0].items)

                run.metrics.setdefault("stage_durations_ms", {})["reconciliation"] = int(
            (time.perf_counter() - stage_start) * 1000
        )
        return run.reconciliation_case_id or ""

    async def _run_report_generation_stage(
        self,
        run: OrchestrationRun,
    ) -> str:
        """Run the report generation stage."""
        stage_start = time.perf_counter()

        if not self.settings.auto_generate_report:
            return ""

        with HospitalDatabase(default_database_path()) as db:
            if run.reconciliation_case_id:
                case = self.db.get_reconciliation_case(run.reconciliation_case_id)
                if case:
                    from hospital_extractor.reporting.service import generate_report

                    report = generate_report(
                        db,
                        case,
                        report_type=ReportType(self.settings.default_report_type),
                        format=self.settings.default_report_format,
                        generated_by="system",
                    )
                    db.save_report(report)
                    run.report_id = report.report_id
                    run.report_idempotency_key = f"rpt_{run.report_id}"
                    run.metrics["report_generated"] = True
                    run.metrics["report_id"] = report.report_id
                    run.metrics["report_type"] = report.report_type.value
                    run.metrics["report_format"] = report.format

        run.metrics.setdefault("stage_durations_ms", {})["report_generation"] = int(
            (time.perf_counter() - stage_start) * 1000
        )
        return run.report_id or ""

    async def _run_submission_stage(
        self,
        run: OrchestrationRun,
    ) -> str:
        """Run the submission stage."""
        stage_start = time.perf_counter()

        if not self.settings.auto_submit_report or not run.report_id:
            return ""

        with HospitalDatabase(default_database_path()) as db:
            report = db.get_report(run.report_id)
            if report:
                from hospital_extractor.reporting.models import (
                    NotificationConfig,
                    NotificationProviderType,
                )
                from hospital_extractor.reporting.providers import (
                    MockProvider,
                )

                config = NotificationConfig(
                    provider_type=NotificationProviderType.MOCK,
                    enabled=True,
                )

                report_obj = db.get_report(run.report_id)
                if report_obj:
                    submission = await MockProvider().submit(
                        report_obj, config, dry_run=False
                    )
                    db.save_submission(submission)
                    run.submission_id = submission.submission_id
                    run.submission_idempotency_key = submission.idempotency_key
                    run.metrics["submission_created"] = True
                    run.metrics["submission_id"] = submission.submission_id
                    run.metrics["submission_status"] = submission.status.value
                    run.metrics["submission_provider"] = submission.provider_type.value

        run.metrics.setdefault("stage_durations_ms", {})["submission"] = int(
            (time.perf_counter() - stage_start) * 1000
        )
        return run.submission_id or ""

    async def run(
        self,
        hospital_id: str,
        source_type: str,
        target_ref: str,
        *,
        run_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> OrchestrationRun:
        """Run the full end-to-end orchestration workflow."""
        # Create or get existing run
        run_id = run_id or f"run_{uuid4().hex[:12]}"
        idempotency_key = idempotency_key or f"{hospital_id}_{source_type}_{target_ref}"

        with HospitalDatabase(default_database_path()) as db:
            # Check for existing run with same idempotency key
            existing = db.get_orchestration_run_by_idempotency(idempotency_key)
            if existing:
                return existing

        run = OrchestrationRun(
            run_id=run_id,
            hospital_id=hospital_id,
            source_type=source_type,
            trigger_ref=target_ref,
            status=OrchestrationStatus.RUNNING,
            current_stage=OrchestrationStage.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            created_by="system",
            idempotency_key=idempotency_key,
        )

        # Save initial run
        with HospitalDatabase(default_database_path()) as db:
            db.save_orchestration_run(run)

        try:
            # Stage 1: Capture
            self._transition_stage(run, OrchestrationStage.CAPTURE)
            snapshot_id, change_set_id = await self._run_capture_stage(
                run, run.hospital_id, run.source_type, run.trigger_ref, load_config()
            )
            run.snapshot_id = snapshot_id
            run.change_set_id = change_set_id

            # Stage 2: Change Detection
            self._transition_stage(run, OrchestrationStage.CHANGE_DETECTION)
            await self._run_change_detection_stage(run)

            # Check change set status
            with HospitalDatabase(default_database_path()) as db:
                change_set = db.get_change_set(run.change_set_id)
                if change_set and change_set.status == ChangeSetStatus.NO_CHANGE:
                    run.status = OrchestrationStatus.NO_CHANGE
                    run.current_stage = OrchestrationStage.NO_CHANGE
                    run.completed_at = datetime.now(UTC)
                    run.status = OrchestrationStatus.NO_CHANGE
                    with HospitalDatabase(default_database_path()) as db:
                        db.update_orchestration_run(run)
                    return run

            # Stage 3: Verification
            self._transition_stage(run, OrchestrationStage.VERIFICATION_SCHEDULED)
            self._transition_stage(run, OrchestrationStage.VERIFICATION_RUNNING)
            await self._run_verification_stage(run)

            # Check verification result
            with HospitalDatabase(default_database_path()) as db:
                result = (
                    db.get_verification_result(run.verification_result_id)
                    if run.verification_result_id
                    else None
                )
                if result and result.status == VerificationResultStatus.VERIFIED:
                    # Verified - no reconciliation needed
                    self._transition_stage(run, OrchestrationStage.COMPLETED)
                    run.status = OrchestrationStatus.COMPLETED
                    run.completed_at = datetime.now(UTC)
                    with HospitalDatabase(default_database_path()) as db:
                        db.update_orchestration_run(run)
                    return run

            # Stage 4: Reconciliation (if needed)
            if run.verification_result_status in ("mismatch", "unverified"):
                self._transition_stage(run, OrchestrationStage.RECONCILIATION_PENDING)
                self._transition_stage(run, OrchestrationStage.RECONCILIATION_OPEN)
                await self._run_reconciliation_stage(run)

            # Stage 5: Report Generation
            if self.settings.auto_generate_report and run.reconciliation_case_id:
                self._transition_stage(run, OrchestrationStage.REPORT_GENERATION)
                await self._run_report_generation_stage(run)
                self._transition_stage(run, OrchestrationStage.REPORT_READY)

            # Stage 6: Submission
            if self.settings.auto_submit_report and run.report_id:
                self._transition_stage(run, OrchestrationStage.SUBMISSION_PENDING)
                await self._run_submission_stage(run)

            # Complete
            self._transition_stage(run, OrchestrationStage.COMPLETED)
            run.status = OrchestrationStatus.COMPLETED
            run.completed_at = datetime.now(UTC)
            run.updated_at = datetime.now(UTC)

            with HospitalDatabase(default_database_path()) as db:
                db.update_orchestration_run(run)

            return run

        except Exception as exc:
            run.status = OrchestrationStatus.FAILED
            run.error = str(exc)
            run.error_class = exc.__class__.__name__
            run.updated_at = datetime.now(UTC)
            with HospitalDatabase(default_database_path()) as db:
                db.update_orchestration_run(run)
            raise


async def run_orchestration(
    hospital_id: str,
    source_type: str,
    target_ref: str,
    *,
    run_id: str | None = None,
    idempotency_key: str | None = None,
    db_path: Path | str | None = None,
) -> OrchestrationRun:
    """Run the full end-to-end orchestration workflow."""
    db = HospitalDatabase(db_path or default_database_path())
    service = OrchestrationService(db)
    try:
        return await service.run(
            hospital_id=hospital_id,
            source_type=source_type,
            target_ref=target_ref,
            run_id=run_id,
            idempotency_key=idempotency_key,
        )
    finally:
        db.close()


async def resume_orchestration(
    run_id: str,
    *,
    db_path: Path | str | None = None,
) -> OrchestrationRun:
    """Resume a failed or interrupted orchestration run."""
    db = HospitalDatabase(db_path or default_database_path())
    try:
        run = db.get_orchestration_run(run_id)
        if run is None:
            raise ValueError(f"Orchestration run {run_id} not found")

        if not run.is_retryable and run.status != OrchestrationStatus.RUNNING:
            raise ValueError(f"Run {run_id} is not retryable (status: {run.status})")

        # Resume from the current stage
        run.status = OrchestrationStatus.RUNNING
        run.updated_at = datetime.now(UTC)
        with HospitalDatabase(default_database_path()) as db:
            db.update_orchestration_run(run)

        # TODO: Implement proper resume logic
        # For now, just mark as failed
        run.status = OrchestrationStatus.FAILED
        run.error = "Resume not fully implemented yet"
        with HospitalDatabase(default_database_path()) as db:
            db.update_orchestration_run(run)

        return run
    finally:
        db.close()