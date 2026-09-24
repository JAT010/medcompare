"""Tests for the P19-D reporting layer."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hospital_extractor.reporting.formatters import (
    format_report,
    format_report_html,
    format_report_json,
    format_report_markdown,
)
from hospital_extractor.reporting.government import MockGovernmentProvider
from hospital_extractor.reporting.models import (
    NotificationConfig,
    NotificationProviderType,
    Report,
    ReportStatus,
    ReportType,
    Submission,
    SubmissionStatus,
)
from hospital_extractor.reporting.providers import (
    LocalProvider,
    MockProvider,
)
from hospital_extractor.reporting.settings import reporting_settings


class TestReportingSettings:
    def test_default_settings(self) -> None:
        settings = reporting_settings()
        assert settings.default_report_format == "json"
        assert settings.enable_mock_provider is True
        assert settings.require_authorization_for_external is True

    def test_override_settings(self) -> None:
        settings = reporting_settings(default_report_format="markdown")
        assert settings.default_report_format == "markdown"


class TestReportModels:
    def test_report_creation(self) -> None:
        now = datetime.now(UTC)
        report = Report(
            report_id="rpt_test123",
            hospital_id="h_test",
            report_type=ReportType.RECONCILIATION_REPORT,
            status=ReportStatus.GENERATED,
            format="json",
            content={"key": "value"},
            generated_at=now,
            generated_by="system",
            created_at=now,
            updated_at=now,
        )
        assert report.report_id == "rpt_test123"
        assert report.report_type == ReportType.RECONCILIATION_REPORT
        assert report.status == ReportStatus.GENERATED

    def test_submission_creation(self) -> None:
        now = datetime.now(UTC)
        submission = Submission(
            submission_id="sub_test123",
            report_id="rpt_test123",
            provider_type=NotificationProviderType.MOCK,
            idempotency_key="sub_rpt_test123_mock",
            status=SubmissionStatus.PENDING,
            created_by="system",
            created_at=now,
            updated_at=now,
        )
        assert submission.provider_type == NotificationProviderType.MOCK
        assert submission.status == SubmissionStatus.PENDING

    def test_idempotency_key(self) -> None:
        key = "sub_rpt_test123_mock"
        submission = Submission(
            submission_id="sub_test123",
            report_id="rpt_test123",
            provider_type=NotificationProviderType.MOCK,
            idempotency_key=key,
            status=SubmissionStatus.PENDING,
            created_by="system",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        assert submission.idempotency_key == key


class TestReportFormatters:
    def test_format_json(self) -> None:
        now = datetime.now(UTC)
        report = Report(
            report_id="rpt_test123",
            hospital_id="h_test",
            report_type=ReportType.RECONCILIATION_REPORT,
            status=ReportStatus.GENERATED,
            format="json",
            content={"items": []},
            generated_at=now,
            generated_by="system",
            created_at=now,
            updated_at=now,
        )
        result = format_report_json(report)
        assert "rpt_test123" in result
        assert "reconciliation_report" in result

    def test_format_html(self) -> None:
        now = datetime.now(UTC)
        report = Report(
            report_id="rpt_test123",
            hospital_id="h_test",
            report_type=ReportType.RECONCILIATION_REPORT,
            status=ReportStatus.GENERATED,
            format="json",
            content={"items": []},
            generated_at=now,
            generated_by="system",
            created_at=now,
            updated_at=now,
        )
        result = format_report_html(report)
        assert "<html" in result
        assert "rpt_test123" in result
        assert "GENERATED" in result

    def test_format_markdown(self) -> None:
        now = datetime.now(UTC)
        report = Report(
            report_id="rpt_test123",
            hospital_id="h_test",
            report_type=ReportType.RECONCILIATION_REPORT,
            status=ReportStatus.GENERATED,
            format="json",
            content={"items": []},
            generated_at=now,
            generated_by="system",
            created_at=now,
            updated_at=now,
        )
        result = format_report_markdown(report)
        assert "# Report" in result
        assert "rpt_test123" in result

    def test_format_report_auto(self) -> None:
        now = datetime.now(UTC)
        report = Report(
            report_id="rpt_test123",
            hospital_id="h_test",
            report_type=ReportType.RECONCILIATION_REPORT,
            status=ReportStatus.GENERATED,
            format="json",
            content={"items": []},
            generated_at=now,
            generated_by="system",
            created_at=now,
            updated_at=now,
        )
        # Test default (json)
        result = format_report(report)
        assert "rpt_test123" in result
        # Test html
        result = format_report(report, "html")
        assert "<html" in result
        # Test markdown
        result = format_report(report, "markdown")
        assert "# Report" in result


class TestProviders:
    def test_local_provider_always_valid(self) -> None:
        provider = LocalProvider()
        config = NotificationConfig(provider_type=NotificationProviderType.LOCAL, enabled=True)
        provider.validate_configuration(config)  # Should not raise
        assert provider.describe() == "Local storage (no external action)"

    @pytest.mark.asyncio
    async def test_local_provider_submit(self) -> None:
        provider = LocalProvider()
        now = datetime.now(UTC)
        report = Report(
            report_id="rpt_test123",
            hospital_id="h_test",
            report_type=ReportType.RECONCILIATION_REPORT,
            status=ReportStatus.GENERATED,
            format="json",
            content={},
            generated_at=now,
            generated_by="system",
            created_at=now,
            updated_at=now,
        )
        config = NotificationConfig(provider_type=NotificationProviderType.LOCAL, enabled=True)
        submission = await provider.submit(report, config, dry_run=False)
        assert submission.status == SubmissionStatus.SUBMITTED
        assert not submission.dry_run

    @pytest.mark.asyncio
    async def test_local_provider_dry_run(self) -> None:
        provider = LocalProvider()
        now = datetime.now(UTC)
        report = Report(
            report_id="rpt_test123",
            hospital_id="h_test",
            report_type=ReportType.RECONCILIATION_REPORT,
            status=ReportStatus.GENERATED,
            format="json",
            content={},
            generated_at=now,
            generated_by="system",
            created_at=now,
            updated_at=now,
        )
        config = NotificationConfig(provider_type=NotificationProviderType.LOCAL, enabled=True)
        submission = await provider.submit(report, config, dry_run=True)
        assert submission.status == SubmissionStatus.SUBMITTED
        assert submission.dry_run is True

    def test_mock_provider_requires_enabled(self) -> None:
        provider = MockProvider()
        config = NotificationConfig(provider_type=NotificationProviderType.MOCK, enabled=False)
        from hospital_extractor.reporting.providers import ConfigurationError
        with pytest.raises(ConfigurationError):
            provider.validate_configuration(config)

    @pytest.mark.asyncio
    async def test_mock_provider_idempotency(self) -> None:
        provider = MockProvider()
        now = datetime.now(UTC)
        report = Report(
            report_id="rpt_test123",
            hospital_id="h_test",
            report_type=ReportType.RECONCILIATION_REPORT,
            status=ReportStatus.GENERATED,
            format="json",
            content={},
            generated_at=now,
            generated_by="system",
            created_at=now,
            updated_at=now,
        )
        config = NotificationConfig(provider_type=NotificationProviderType.MOCK, enabled=True)

        # First submission
        sub1 = await provider.submit(report, config, dry_run=False)
        # Second submission (should return same)
        sub2 = await provider.submit(report, config, dry_run=False)
        assert sub1.submission_id == sub2.submission_id
        assert sub1.idempotency_key == sub2.idempotency_key

    @pytest.mark.asyncio
    async def test_mock_provider_failure_mode(self) -> None:
        provider = MockProvider(fail_submissions=True)
        now = datetime.now(UTC)
        report = Report(
            report_id="rpt_test123",
            hospital_id="h_test",
            report_type=ReportType.RECONCILIATION_REPORT,
            status=ReportStatus.GENERATED,
            format="json",
            content={},
            generated_at=now,
            generated_by="system",
            created_at=now,
            updated_at=now,
        )
        config = NotificationConfig(provider_type=NotificationProviderType.MOCK, enabled=True)
        submission = await provider.submit(report, config, dry_run=False)
        assert submission.status == SubmissionStatus.FAILED
        assert submission.error is not None

    @pytest.mark.asyncio
    async def test_mock_government_provider(self) -> None:
        provider = MockGovernmentProvider()
        now = datetime.now(UTC)
        report = Report(
            report_id="rpt_test123",
            hospital_id="h_test",
            report_type=ReportType.RECONCILIATION_REPORT,
            status=ReportStatus.GENERATED,
            format="json",
            content={},
            generated_at=now,
            generated_by="system",
            created_at=now,
            updated_at=now,
        )
        config = NotificationConfig(
            provider_type=NotificationProviderType.GOVERNMENT_PORTAL, enabled=True
        )

        submission = await provider.submit(report, config, dry_run=False)
        assert submission.status == SubmissionStatus.SUBMITTED
        assert submission.external_reference is not None
        assert submission.external_reference.startswith("GOV-")

        # Check status progression
        status_check = await provider.status(submission.submission_id)
        assert status_check is not None
        assert status_check.status == SubmissionStatus.ACKNOWLEDGED

    @pytest.mark.asyncio
    async def test_mock_government_provider_idempotency(self) -> None:
        provider = MockGovernmentProvider()
        now = datetime.now(UTC)
        report = Report(
            report_id="rpt_test123",
            hospital_id="h_test",
            report_type=ReportType.RECONCILIATION_REPORT,
            status=ReportStatus.GENERATED,
            format="json",
            content={},
            generated_at=now,
            generated_by="system",
            created_at=now,
            updated_at=now,
        )
        config = NotificationConfig(
            provider_type=NotificationProviderType.GOVERNMENT_PORTAL, enabled=True
        )

        sub1 = await provider.submit(report, config, dry_run=False)
        sub2 = await provider.submit(report, config, dry_run=False)
        assert sub1.submission_id == sub2.submission_id


class TestReportGeneration:
    def test_generate_report_basic(self, populated_db) -> None:
        from hospital_extractor.reconciliation.service import get_case
        _ = get_case(populated_db, "rec_")
        # This test will need a proper fixture
        pass