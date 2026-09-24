"""P19-D Reporting layer: generate, notify, submit.

REPORT -> NOTIFY -> SUBMIT are distinct operations.
Default behavior: generate evidence-backed report.
External notification/submission requires explicit authorization.
"""

from __future__ import annotations

from hospital_extractor.reporting.formatters import (
    format_report,
    format_report_html,
    format_report_json,
    format_report_markdown,
)
from hospital_extractor.reporting.government import (
    GovernmentReportingProvider,
    MockGovernmentProvider,
)
from hospital_extractor.reporting.models import (
    NotificationConfig,
    NotificationProviderType,
    Report,
    ReportSnapshotReference,
    ReportStatus,
    ReportType,
    Submission,
    SubmissionIdempotencyKey,
    SubmissionStatus,
)
from hospital_extractor.reporting.providers import (
    AuthorizationError,
    ConfigurationError,
    LocalProvider,
    MockProvider,
    NotificationProvider,
    ProviderError,
    SubmissionError,
)
from hospital_extractor.reporting.service import (
    ReportGenerationError,
    generate_change_report,
    generate_reconciliation_report,
    generate_report,
    generate_verification_report,
)
from hospital_extractor.reporting.settings import (
    ReportingSettings,
    reporting_settings,
)

__all__ = [
    # Models
    "NotificationConfig",
    "NotificationProviderType",
    "Report",
    "ReportSnapshotReference",
    "ReportStatus",
    "ReportType",
    "Submission",
    "SubmissionIdempotencyKey",
    "SubmissionStatus",
    # Settings
    "ReportingSettings",
    "reporting_settings",
    # Service
    "ReportGenerationError",
    "generate_change_report",
    "generate_reconciliation_report",
    "generate_report",
    "generate_verification_report",
    # Formatters
    "format_report",
    "format_report_html",
    "format_report_json",
    "format_report_markdown",
    # Providers
    "AuthorizationError",
    "ConfigurationError",
    "LocalProvider",
    "MockProvider",
    "NotificationProvider",
    "ProviderError",
    "SubmissionError",
    # Government
    "GovernmentReportingProvider",
    "MockGovernmentProvider",
]