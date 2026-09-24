"""Deterministic field resolution and filter evaluation (P22).

Resolves dotted paths against a :class:`HospitalDataset` and evaluates
:class:`~hospital_extractor.search.models.Filter` predicates.

Semantics
---------
- A missing / ``None`` field is UNKNOWN.  It never equals a value, never
  contains a value, and never satisfies numeric or date comparisons.
- ``exists`` is True only when at least one concrete (non-``None``) value
  is present.  ``not_exists`` is the exact dual — it does **not** mean
  "the hospital does not offer this".
- Multi-record categories (``specialities``, ``doctors``, …) match when
  **any** record satisfies a positive predicate, or when **no** record
  satisfies a negative predicate (``not_equals`` / ``not_contains`` /
  ``not_in``) and at least one value exists.
- ``equals`` on a multi-record field matches if any record equals.
  ``not_equals`` matches only when every existing value differs (and at
  least one exists).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.schema.canonical import CANONICAL_CATEGORIES, category_for
from hospital_extractor.search.models import EvidenceReference, Filter, FilterOperator

__all__ = [
    "DEFAULT_COMPARISON_FIELDS",
    "FIELD_LABELS",
    "InvalidQueryError",
    "compare_field_values",
    "evidence_for",
    "label_for_field",
    "matches_filter",
    "resolve_field_values",
    "validate_field_path",
]


class InvalidQueryError(ValueError):
    """Raised for an unknown field path or unusable filter value."""


# Non-category roots that are valid dotted paths.
_SPECIAL_ROOTS = frozenset({"source", "quality", "dataset"})

DEFAULT_COMPARISON_FIELDS: tuple[str, ...] = (
    "hospital.name",
    "hospital.hospital_type",
    "hospital.established_year",
    "hospital.bed_count",
    "hospital.doctor_count",
    "contacts.city",
    "contacts.state",
    "contacts.phone",
    "contacts.email",
    "infrastructure.total_beds",
    "infrastructure.icu_beds",
    "source.source_type",
    "source.provider",
    "quality.records_extracted",
)

FIELD_LABELS: dict[str, str] = {
    "hospital.name": "Hospital name",
    "hospital.hospital_type": "Hospital type",
    "hospital.established_year": "Established",
    "hospital.bed_count": "Bed count",
    "hospital.doctor_count": "Doctor count",
    "hospital.description": "Description",
    "hospital.status": "Status",
    "hospital.website": "Website",
    "contacts.city": "City",
    "contacts.state": "State",
    "contacts.country": "Country",
    "contacts.address": "Address",
    "contacts.phone": "Phone",
    "contacts.email": "Email",
    "contacts.postal_code": "Postal code",
    "infrastructure.total_beds": "Total beds",
    "infrastructure.icu_beds": "ICU beds",
    "infrastructure.operation_theatres": "Operation theatres",
    "infrastructure.cath_labs": "Cath labs",
    "infrastructure.critical_care_beds": "Critical care beds",
    "source.source_type": "Source type",
    "source.provider": "Provider",
    "source.base_url": "Source base URL",
    "source.hospital_ref": "Source hospital ref",
    "quality.records_extracted": "Records extracted",
    "quality.pages_extracted": "Pages extracted",
    "quality.pages_considered": "Pages considered",
    "quality.duplicates_removed": "Duplicates removed",
    "quality.conflicts": "Conflicts detected",
}

# Plural category key → human label for multi-record paths.
_CATEGORY_ALIASES: dict[str, str] = {
    category.key: category.label for category in CANONICAL_CATEGORIES
}
_CATEGORY_ALIASES.update(
    {
        "contacts": "Location & Contact",
        "infrastructure": "Capacity & Infrastructure",
        "source": "Source metadata",
        "quality": "Data quality",
        "dataset": "Dataset",
    }
)


def label_for_field(path: str) -> str:
    """Human label for a dotted field path."""
    if path in FIELD_LABELS:
        return FIELD_LABELS[path]
    root, _, leaf = path.rpartition(".")
    if leaf:
        leaf_label = leaf.replace("_", " ").title()
        root_label = _CATEGORY_ALIASES.get(root, root.replace("_", " ").title())
        return f"{root_label} · {leaf_label}"
    return path.replace("_", " ").title()


def validate_field_path(path: str) -> None:
    """Raise :class:`InvalidQueryError` when *path* is not a known field."""
    if not path or "." not in path:
        raise InvalidQueryError(
            f"unknown field {path!r}; expected a dotted path like 'hospital.name'"
        )
    parts = path.split(".")
    if len(parts) != 2 or not all(parts):
        raise InvalidQueryError(
            f"unknown field {path!r}; expected exactly one dot (root.leaf)"
        )
    root, leaf = parts

    if root in _SPECIAL_ROOTS:
        _validate_special_leaf(root, leaf)
        return

    try:
        category = category_for(root)
    except KeyError as exc:
        raise InvalidQueryError(
            f"unknown field {path!r}; unknown section {root!r}"
        ) from exc
    model_fields = category.model.model_fields
    if leaf not in model_fields:
        raise InvalidQueryError(
            f"unknown field {path!r}; {root!r} has no field {leaf!r}"
        )


def _validate_special_leaf(root: str, leaf: str) -> None:
    from hospital_extractor.models.metadata import SourceMetadata
    from hospital_extractor.models.quality import DataQuality

    if root == "source":
        if leaf not in SourceMetadata.model_fields:
            raise InvalidQueryError(f"unknown field 'source.{leaf}'")
        return
    if root == "quality":
        # computed properties are also filterable
        if leaf in DataQuality.model_fields or leaf in {
            "conflicts_detected",
            "conflicts_resolved",
            "conflicts_unresolved",
        }:
            return
        raise InvalidQueryError(f"unknown field 'quality.{leaf}'")
    if root == "dataset":
        if leaf not in {"hospital_ref", "hospital_name", "source_type"}:
            raise InvalidQueryError(f"unknown field 'dataset.{leaf}'")


def resolve_field_values(
    dataset: HospitalDataset,
    path: str,
    *,
    hospital_ref: str = "",
    hospital_name: str | None = None,
    source_type: str = "website",
) -> list[tuple[Any, Any]]:
    """Return ``(value, origin_record)`` pairs for *path* on *dataset*.

    ``origin_record`` is the Pydantic record (or dataset) the value came
    from, used to build evidence.  An empty list means the field is not
    published (UNKNOWN) for this hospital.
    """
    validate_field_path(path)
    root, leaf = path.split(".", 1)

    if root == "source":
        source = dataset.source
        if source is None:
            return []
        value = getattr(source, leaf, None)
        if value is None:
            return []
        if hasattr(value, "value"):  # StrEnum
            value = value.value
        return [(value, source)]

    if root == "quality":
        quality = dataset.quality
        if leaf == "conflicts":
            q_value: Any = quality.conflicts_detected
        else:
            q_value = getattr(quality, leaf, None)
        if q_value is None:
            return []
        return [(q_value, quality)]

    if root == "dataset":
        value = {
            "hospital_ref": hospital_ref
            or (
                dataset.source.hospital_ref
                if dataset.source is not None
                else ""
            ),
            "hospital_name": hospital_name
            or (dataset.hospital.name if dataset.hospital is not None else None),
            "source_type": source_type,
        }.get(leaf)
        if value is None:
            return []
        return [(value, dataset)]

    if root == "hospital":
        if dataset.hospital is None:
            return []
        value = getattr(dataset.hospital, leaf, None)
        if value is None:
            return []
        return [(value, dataset.hospital)]

    records = list(getattr(dataset, root, []) or [])
    pairs: list[tuple[Any, Any]] = []
    for record in records:
        value = getattr(record, leaf, None)
        if value is None:
            continue
        pairs.append((value, record))
    return pairs


def matches_filter(
    values: list[tuple[Any, Any]],
    filter_: Filter,
) -> bool:
    """Evaluate one filter against resolved ``(value, origin)`` pairs.

    ``values == []`` means UNKNOWN for the field.
    """
    op = filter_.operator
    expected = filter_.value

    if op is FilterOperator.EXISTS:
        return bool(values)
    if op is FilterOperator.NOT_EXISTS:
        return not values

    if not values:
        # UNKNOWN never satisfies a positive or negative content predicate
        # that requires a concrete value (including not_equals — we do not
        # invent a "differs" claim from missing data).
        return False

    concrete = [value for value, _ in values]

    if op is FilterOperator.EQUALS:
        return any(_loose_equals(value, expected) for value in concrete)
    if op is FilterOperator.NOT_EQUALS:
        return all(not _loose_equals(value, expected) for value in concrete)
    if op is FilterOperator.CONTAINS:
        return any(_contains(value, expected) for value in concrete)
    if op is FilterOperator.NOT_CONTAINS:
        return all(not _contains(value, expected) for value in concrete)
    if op is FilterOperator.IN:
        options = _as_list(expected)
        return any(_loose_equals(value, option) for value in concrete for option in options)
    if op is FilterOperator.NOT_IN:
        options = _as_list(expected)
        return all(
            not _loose_equals(value, option) for value in concrete for option in options
        )
    if op in (
        FilterOperator.GT,
        FilterOperator.GTE,
        FilterOperator.LT,
        FilterOperator.LTE,
        FilterOperator.AFTER,
        FilterOperator.BEFORE,
    ):
        return any(
            _compare_numeric_or_date(value, expected, op) is True for value in concrete
        )
    return False


def _loose_equals(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) == bool(right) and left is not None and right is not None
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    if isinstance(left, datetime) or isinstance(right, datetime):
        left_iso = left.isoformat() if isinstance(left, datetime) else str(left)
        right_iso = right.isoformat() if isinstance(right, datetime) else str(right)
        return left_iso == right_iso
    return str(left).casefold() == str(right).casefold()


def _contains(value: Any, needle: Any) -> bool:
    if needle is None:
        return False
    return str(needle).casefold() in str(value).casefold()


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str) and "," in value:
        return [part.strip() for part in value.split(",") if part.strip()]
    return [value]


def _compare_numeric_or_date(value: Any, expected: Any, op: FilterOperator) -> bool | None:
    """Return True/False, or None when the pair is not comparable."""
    if op in (FilterOperator.AFTER, FilterOperator.BEFORE):
        left = _as_datetime(value)
        right = _as_datetime(expected)
        if left is None or right is None:
            return None
        if op is FilterOperator.AFTER:
            return left > right
        return left < right

    left_num = _as_number(value)
    right_num = _as_number(expected)
    if left_num is None or right_num is None:
        return None
    if op is FilterOperator.GT:
        return left_num > right_num
    if op is FilterOperator.GTE:
        return left_num >= right_num
    if op is FilterOperator.LT:
        return left_num < right_num
    if op is FilterOperator.LTE:
        return left_num <= right_num
    return None


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", ""))
        except ValueError:
            return None
    return None


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def compare_field_values(
    dataset: HospitalDataset,
    path: str,
    *,
    hospital_ref: str = "",
    hospital_name: str | None = None,
    source_type: str = "website",
) -> tuple[Any, bool, list[EvidenceReference]]:
    """Resolve *path* for comparison output.

    Returns ``(value, unknown, evidence)``.  Multi-record fields collapse
    to a sorted list of unique stringified values (or a single scalar when
    only one value exists).  ``unknown=True`` means nothing was published.
    """
    pairs = resolve_field_values(
        dataset,
        path,
        hospital_ref=hospital_ref,
        hospital_name=hospital_name,
        source_type=source_type,
    )
    if not pairs:
        return None, True, []

    evidence: list[EvidenceReference] = []
    rendered: list[str] = []
    for value, origin in pairs:
        text = _render_value(value)
        if text not in rendered:
            rendered.append(text)
        evidence.append(evidence_for(origin, source_type=source_type, text=text))

    if len(rendered) == 1 and len(pairs) == 1:
        scalar: Any = pairs[0][0]
        if hasattr(scalar, "value"):
            scalar = scalar.value
        return scalar, False, evidence[:1]

    # Multi-value or multi-record: keep deterministic order.
    unique_sorted = sorted(rendered, key=str.casefold)
    return unique_sorted, False, evidence


def _render_value(value: Any) -> str:
    if hasattr(value, "value"):
        value = value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def evidence_for(
    origin: Any,
    *,
    source_type: str,
    text: str,
) -> EvidenceReference:
    source_url = getattr(origin, "source_url", None) or ""
    if not source_url:
        source_url = str(getattr(origin, "base_url", None) or "")
    if not source_url:
        source_url = str(getattr(origin, "endpoint", None) or "")
    st = getattr(origin, "source_type", None)
    st_value = getattr(st, "value", st)
    return EvidenceReference(
        source_url=source_url,
        source_type=str(st_value or source_type or "website"),
        page_title=getattr(origin, "page_title", None),
        captured_at=getattr(origin, "extracted_at", None)
        or getattr(origin, "retrieved_at", None),
        confidence=getattr(origin, "confidence", None),
        extraction_method=getattr(origin, "extraction_method", None),
        source_strength=getattr(origin, "source_strength", None),
        context=(text[:200] if text else None),
    )
