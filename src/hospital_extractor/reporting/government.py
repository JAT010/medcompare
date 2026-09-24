"""Government Reporting Provider contract and mock implementation.

DO NOT assume a government API exists. Create an adapter contract only.

The mock provider must demonstrate:
    report generated
        ↓
    submission
        ↓
    receipt/reference ID
        ↓
    submitted status

Do NOT connect to real government systems unless an actual authorized API
integration is explicitly configured later.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime

from hospital_extractor.reporting.models import (
    NotificationConfig,
    NotificationProviderType,
    Report,
    Submission,
    SubmissionStatus,
)
from hospital_extractor.reporting.providers import (
    ConfigurationError,
    NotificationProvider,
    ProviderError,
)


class GovernmentReportingError(ProviderError):
    """Government reporting specific errors."""


class GovernmentReportingProvider(NotificationProvider, ABC):
    """Abstract contract for government reporting providers.

    Implementations must:
    - validate_configuration(): check if government endpoint is configured
    - submit(report): submit the report to the government portal
    - status(submission_id): check status of a government submission
    - describe(): human-readable description
    """

    provider_type = NotificationProviderType.GOVERNMENT_PORTAL

    @abstractmethod
    def validate_configuration(self, config: NotificationConfig) -> None:
        """Validate government portal configuration.

        Raises ConfigurationError if not configured.
        """
        pass

    @abstractmethod
    def describe(self) -> str:
        """Human-readable description of this government provider."""
        pass

    async def submit(
        self, report: Report, config: NotificationConfig, *, dry_run: bool = False
    ) -> Submission:
        """Submit report to government portal.

        If dry_run: validate config, build payload, show destination, no mutation.
        """
        if dry_run:
            return Submission(
                submission_id=f"sub_{uuid.uuid4().hex[:12]}",
                report_id=report.report_id,
                provider_type=self.provider_type,
                idempotency_key=f"sub_{report.report_id}_{self.provider_type.value}",
                status=SubmissionStatus.PENDING,
                dry_run=True,
                created_by="system",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                response_metadata={
                    "dry_run": True,
                    "would_submit_to": self.describe(),
                    "report_id": report.report_id,
                },
            )
        return await self._submit_to_government(report, config)

    @abstractmethod
    async def _submit_to_government(self, report: Report, config: NotificationConfig) -> Submission:
        """Actual government submission logic."""
        pass

    @abstractmethod
    async def status(self, submission_id: str) -> Submission | None:
        """Check government submission status."""
        pass


class MockGovernmentProvider(GovernmentReportingProvider):
    """Mock government provider for deterministic demonstrations.

    Simulates the full submission lifecycle:
    report generated -> submission -> receipt/reference ID -> acknowledged

    NEVER connects to real government systems.
    """

    def __init__(self, *, fail_submissions: bool = False, fail_on_receipt: bool = False) -> None:
        self._fail_submissions = fail_submissions
        self._fail_on_receipt = fail_on_receipt
        self._submissions: dict[str, Submission] = {}

    def validate_configuration(self, config: NotificationConfig) -> None:
        if not config.enabled:
            raise ConfigurationError("Mock government provider is disabled")
        # Mock provider is always valid when enabled

    def describe(self) -> str:
        return "Mock Government Portal (simulated, no real submission)"

    async def _submit_to_government(
        self, report: Report, config: NotificationConfig
    ) -> Submission:
        self._check_authorization(config)
        self.validate_configuration(config)

        idempotency_key = f"sub_{report.report_id}_{self.provider_type.value}"

        # Idempotency: return existing submission if present
        if idempotency_key in self._submissions:
            existing = self._submissions[idempotency_key]
            return Submission(
                submission_id=existing.submission_id,
                report_id=existing.report_id,
                provider_type=existing.provider_type,
                idempotency_key=existing.idempotency_key,
                status=existing.status,
                submitted_at=existing.submitted_at,
                external_reference=existing.external_reference,
                response_metadata=existing.response_metadata,
                error=existing.error,
                dry_run=existing.dry_run,
                created_by=existing.created_by,
                created_at=existing.created_at,
                updated_at=datetime.now(UTC),
            )

        if self._fail_submissions:
            submission = await self._create_submission(
                report, config, status=SubmissionStatus.FAILED, dry_run=False
            )
            submission.error = "Mock government portal configured to fail submissions"
            submission.response_metadata = {"simulated_failure": True}
            self._submissions[idempotency_key] = submission
            return submission

        # Simulate submission -> receipt -> acknowledged
        submission = await self._create_submission(
            report, config, status=SubmissionStatus.SUBMITTED, dry_run=False
        )
        submission.external_reference = f"GOV-{uuid.uuid4().hex[:8].upper()}"
        submission.response_metadata = {
            "portal": "Mock National Hospital Compliance Portal",
            "submission_format": "JSON envelope",
            "received_at": datetime.now(UTC).isoformat(),
        }
        self._submissions[idempotency_key] = submission
        return submission

    async def status(self, submission_id: str) -> Submission | None:
        for sub in self._submissions.values():
            if sub.submission_id == submission_id:
                # Simulate status progression: SUBMITTED -> ACKNOWLEDGED
                if sub.status == SubmissionStatus.SUBMITTED:
                    updated = Submission(
                        submission_id=sub.submission_id,
                        report_id=sub.report_id,
                        provider_type=sub.provider_type,
                        idempotency_key=sub.idempotency_key,
                        status=SubmissionStatus.ACKNOWLEDGED,
                        submitted_at=sub.submitted_at,
                        external_reference=sub.external_reference,
                        response_metadata=sub.response_metadata,
                        error=sub.error,
                        dry_run=sub.dry_run,
                        created_by=sub.created_by,
                        created_at=sub.created_at,
                        updated_at=datetime.now(UTC),
                    )
                    self._submissions[sub.idempotency_key] = updated
                    return updated
                return sub
        return None