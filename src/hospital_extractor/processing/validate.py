"""Validation of extracted records.

Rejects records that are obviously unusable (empty names, no provenance, no
meaningful fields) while keeping records that merely lack optional fields.
Requires the hospital to carry enough identity (name or website) to be a real
target. Deduplication must run before final validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.schema.canonical import CanonicalCategory
from hospital_extractor.utils.urls import InvalidURLError, normalize_url

__all__ = ["ValidationIssue", "ValidationResult", "dataset_issues", "validate_records"]

_PROVENANCE_FIELDS = frozenset(
    {
        "source_url",
        "page_title",
        "extracted_at",
        "context",
        "extraction_method",
        "source_strength",
        "confidence",
    }
)


@dataclass(frozen=True)
class ValidationIssue:
    """One validation finding for a record or the whole dataset."""

    category: str
    severity: str  # "error" or "warning"
    message: str
    field: str | None = None
    record_index: int | None = None


@dataclass
class ValidationResult:
    """Outcome of validating one category's records."""

    accepted: list[Any] = field(default_factory=list)
    rejected: list[Any] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)


def validate_records(category: CanonicalCategory, records: list[Any]) -> ValidationResult:
    """Validate *records* for *category*; rejected records are returned separately."""
    result = ValidationResult()
    for index, record in enumerate(records):
        data = _dump(record)
        if _invalid_url(data.get("source_url")):
            result.rejected.append(record)
            result.issues.append(
                ValidationIssue(
                    category.key,
                    "error",
                    "source URL is not a valid URL",
                    "source_url",
                    index,
                )
            )
            continue

        meaningful = _meaningful_fields(data)
        if not meaningful:
            result.rejected.append(record)
            result.issues.append(
                ValidationIssue(
                    category.key, "error", "record has no meaningful fields", None, index
                )
            )
            continue

        if "name" in category.required_fields and not _has_name(data):
            result.rejected.append(record)
            result.issues.append(
                ValidationIssue(category.key, "error", "record has no name", "name", index)
            )
            continue

        if category.key == "other_information" and not (data.get("label") and data.get("value")):
            result.rejected.append(record)
            result.issues.append(
                ValidationIssue(
                    category.key,
                    "error",
                    "other-information needs both label and value",
                    None,
                    index,
                )
            )
            continue

        if category.key == "hospital" and not (data.get("name") or data.get("website")):
            result.rejected.append(record)
            result.issues.append(
                ValidationIssue(
                    category.key, "error", "hospital lacks identity (name or website)", None, index
                )
            )
            continue

        result.accepted.append(record)
    return result


def dataset_issues(dataset: HospitalDataset) -> list[ValidationIssue]:
    """Dataset-level checks: target identity and known categories."""
    issues: list[ValidationIssue] = []
    hospital = dataset.hospital
    if hospital is None:
        issues.append(
            ValidationIssue("hospital", "warning", "no hospital overview was extracted")
        )
    elif not (hospital.name or hospital.website):
        issues.append(
            ValidationIssue("hospital", "warning", "hospital overview has no name or website")
        )
    return issues


def _dump(record: Any) -> dict[str, Any]:
    dump = getattr(record, "model_dump", None)
    if callable(dump):
        return dict(dump())
    return dict(record)


def _meaningful_fields(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in data.items()
        if key not in _PROVENANCE_FIELDS and value is not None
    }


def _has_name(data: dict[str, Any]) -> bool:
    name = data.get("name") or data.get("label")
    return isinstance(name, str) and bool(name.strip())


def _invalid_url(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return True
    try:
        normalize_url(value)
    except InvalidURLError:
        return True
    return False
