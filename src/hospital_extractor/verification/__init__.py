"""Verification: compare detected hospital changes against website evidence."""

from hospital_extractor.verification.compare import (
    ChangeVerification,
    VerificationAggregate,
    build_verification,
)
from hospital_extractor.verification.evidence import WebsiteEvidence
from hospital_extractor.verification.service import (
    VerificationOutcome,
    VerificationService,
    VerificationServiceError,
    job_status_for_result,
)

__all__ = [
    "ChangeVerification",
    "VerificationAggregate",
    "VerificationOutcome",
    "VerificationService",
    "VerificationServiceError",
    "WebsiteEvidence",
    "build_verification",
    "job_status_for_result",
]
