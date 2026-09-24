"""Missing-data analysis.

After extraction the dataset knows exactly which canonical fields were
populated. This module turns the absence of a field into an explicit state so
the consumer never has to guess between "the site does not publish this" and
"extraction failed".

States:
- ``not_detected``: the category has records but a schema field is empty.
- ``not_published``: the category itself produced nothing and was not
  attempted — the site does not publish it.
- ``extraction_failed``: the category was attempted (or produced candidates)
  but nothing survived validation.
- ``not_applicable``: the category does not apply to this target (only set
  with explicit evidence; never inferred).
"""

from __future__ import annotations

from dataclasses import dataclass

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.schema.canonical import CANONICAL_CATEGORIES
from hospital_extractor.schema.discovery import category_records

__all__ = ["MissingField", "MissingFieldAnalysis", "analyze_missing_fields"]

_PROVENANCE_FIELDS = frozenset(
    {
        "source_url",
        "page_url",
        "page_title",
        "extracted_at",
        "context",
        "extraction_method",
        "source_strength",
        "confidence",
    }
)

_STATE_PRIORITY = {"failed": 0, "not_applicable": 1, "not_found": 2, "not_detected": 3}


@dataclass(frozen=True)
class MissingField:
    """One expected-but-absent schema field, with an explicit state."""

    category: str
    field: str
    state: str


@dataclass(frozen=True)
class MissingFieldAnalysis:
    """Per-category missing-field summary."""

    fields: tuple[MissingField, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.fields

    def by_category(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for item in self.fields:
            result.setdefault(item.category, []).append(item.field)
        return result


def analyze_missing_fields(
    dataset: HospitalDataset,
    coverage: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Map every canonical category to its missing fields with explicit states.

    Returns ``{category_label: [field, ...]}`` for every category that has
    something missing. Fields of categories with no records are not enumerated;
    the category's coverage status carries the state (``not_published`` /
    ``extraction_failed`` / ``not_applicable``).
    """
    coverage = coverage or {}
    result: dict[str, list[str]] = {}
    for category in CANONICAL_CATEGORIES:
        records = category_records(dataset, category)
        if not records:
            continue
        status = coverage.get(category.label, "found")
        if status == "extraction_failed":
            result[category.label] = []
            continue
        missing = [
            field
            for field in category.model.model_fields
            if field not in _PROVENANCE_FIELDS
            and not any(_field_value(record, field) is not None for record in records)
        ]
        if missing:
            result[category.label] = missing
    return result


def _field_value(record: object, field: str) -> object:
    value = getattr(record, field, None)
    if value is not None:
        return value
    dump = getattr(record, "model_dump", None)
    if callable(dump):
        return dict(dump()).get(field)
    return None
