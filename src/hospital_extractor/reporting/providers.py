"""Notification/Submission provider abstraction.

External actions must NEVER silently modify external systems.
The architecture distinguishes:

    REPORT
        ↓
    NOTIFY
        ↓
    SUBMIT

Default behavior: generate evidence-backed report only.
External notification/submission requires explicit authorization/configuration.
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


class ProviderError(RuntimeError):
    """Base error for provider operations."""


class ConfigurationError(ProviderError):
    """Provider is not properly configured."""


class AuthorizationError(ProviderError):
    """External action not authorized."""


class SubmissionError(ProviderError):
    """External submission failed."""


class NotificationProvider(ABC):
    """Abstract interface for notification/submission providers.

    Every provider must implement:
    - validate_configuration(): check if config is valid
    - send(report): send a notification about a report
    - submit(report): submit a report to an external system
    - status(submission_id): check status of a submission
    - describe(): human-readable description
    """

    provider_type: NotificationProviderType

    @abstractmethod
    def validate_configuration(self, config: NotificationConfig) -> None:
        """Validate that the provider is properly configured.

        Raises ConfigurationError if config is invalid.
        """
        pass

    @abstractmethod
    def describe(self) -> str:
        """Human-readable description of this provider."""
        pass

    async def notify(
        self, report: Report, config: NotificationConfig
    ) -> Submission:
        """Send a notification about a report (e.g., email, webhook).

        Default implementation: create a SUBMITTED submission record.
        Override for actual notification delivery.
        """
        return await self._create_submission(
            report, config, status=SubmissionStatus.SUBMITTED, dry_run=False
        )

    async def submit(
        self, report: Report, config: NotificationConfig, *, dry_run: bool = False
    ) -> Submission:
        """Submit a report to an external system.

        If dry_run=True: validate config, build payload, show destination,
        make NO external mutation.
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
        return await self._create_submission(
            report, config, status=SubmissionStatus.SUBMITTED, dry_run=False
        )

    async def status(self, submission_id: str) -> Submission | None:
        """Check status of a submission.

        Default implementation returns None (not tracked).
        Override for providers that track submission status.
        """
        return None

    def _check_authorization(
        self, config: NotificationConfig, require_auth: bool = True
    ) -> None:
        """Check if external action is authorized.

        Raises AuthorizationError if not authorized.
        """
        if require_auth and not config.enabled:
            raise AuthorizationError(
                f"Provider {self.provider_type.value} is not enabled. "
                "Enable it in configuration to send notifications/submissions."
            )

    async def _create_submission(
        self,
        report: Report,
        config: NotificationConfig,
        status: SubmissionStatus,
        dry_run: bool,
    ) -> Submission:
        """Helper to create a submission record."""
        return Submission(
            submission_id=f"sub_{uuid.uuid4().hex[:12]}",
            report_id=report.report_id,
            provider_type=self.provider_type,
            idempotency_key=f"sub_{report.report_id}_{self.provider_type.value}",
            status=status,
            submitted_at=datetime.now(UTC) if status != SubmissionStatus.PENDING else None,
            dry_run=dry_run,
            created_by="system",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )


class LocalProvider(NotificationProvider):
    """Local provider: writes notification/submission to local storage only.

    This is the default "no external action" provider — it records the
    intent without making any network calls.
    """

    provider_type = NotificationProviderType.LOCAL

    def validate_configuration(self, config: NotificationConfig) -> None:
        # Local provider is always valid
        pass

    def describe(self) -> str:
        return "Local storage (no external action)"

    async def notify(
        self, report: Report, config: NotificationConfig
    ) -> Submission:
        self._check_authorization(config, require_auth=False)
        return await self._create_submission(
            report, config, status=SubmissionStatus.SUBMITTED, dry_run=False
        )

    async def submit(
        self, report: Report, config: NotificationConfig, *, dry_run: bool = False
    ) -> Submission:
        self._check_authorization(config, require_auth=False)
        return await self._create_submission(
            report, config, status=SubmissionStatus.SUBMITTED, dry_run=dry_run
        )


class MockProvider(NotificationProvider):
    """Mock provider: deterministic demonstrations with fake external responses.

    This provider is for testing and demos ONLY. It simulates successful
    and failing submissions without any real external calls.
    """

    provider_type = NotificationProviderType.MOCK

    def __init__(self, *, fail_submissions: bool = False) -> None:
        self._fail_submissions = fail_submissions
        self._submissions: dict[str, Submission] = {}

    def validate_configuration(self, config: NotificationConfig) -> None:
        if not config.enabled:
            raise ConfigurationError("Mock provider is disabled")
        # Mock provider is always valid when enabled

    def describe(self) -> str:
        return "Mock provider (simulated, no real external calls)"

    async def notify(
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
                status=SubmissionStatus.SUBMITTED,
                submitted_at=existing.submitted_at,
                external_reference=existing.external_reference,
                response_metadata=existing.response_metadata,
                dry_run=existing.dry_run,
                created_by=existing.created_by,
                created_at=existing.created_at,
                updated_at=datetime.now(UTC),
            )

        submission = await self._create_submission(
            report,
            config,
            status=SubmissionStatus.SUBMITTED,
            dry_run=False,
        )
        submission.external_reference = f"MOCK-NOTIFY-{uuid.uuid4().hex[:8]}"
        self._submissions[idempotency_key] = submission
        return submission

    async def submit(
        self, report: Report, config: NotificationConfig, *, dry_run: bool = False
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
                status=SubmissionStatus.ACKNOWLEDGED,
                submitted_at=existing.submitted_at,
                external_reference=existing.external_reference,
                response_metadata=existing.response_metadata,
                dry_run=existing.dry_run,
                created_by=existing.created_by,
                created_at=existing.created_at,
                updated_at=datetime.now(UTC),
            )

        if self._fail_submissions:
            submission = await self._create_submission(
                report,
                config,
                status=SubmissionStatus.FAILED,
                dry_run=dry_run,
            )
            submission.error = "Mock provider configured to fail submissions"
            submission.response_metadata = {"simulated_failure": True}
            self._submissions[idempotency_key] = submission
            return submission

        submission = await self._create_submission(
            report, config, status=SubmissionStatus.ACKNOWLEDGED, dry_run=dry_run
        )
        if not dry_run:
            submission.external_reference = f"MOCK-SUBMIT-{uuid.uuid4().hex[:8]}"
        else:
            submission.response_metadata = {
                "dry_run": True,
                "would_submit_to": self.describe(),
                "report_id": report.report_id,
            }
        self._submissions[idempotency_key] = submission
        return submission

    async def status(self, submission_id: str) -> Submission | None:
        for sub in self._submissions.values():
            if sub.submission_id == submission_id:
                return sub
        return None