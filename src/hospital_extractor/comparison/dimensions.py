"""Evidence-backed dimension computation.

Takes the matched entities for a hospital and computes one
:class:`~hospital_extractor.comparison.models.DimensionResult` per
comparison dimension.  Every dimension carries a tri-state decision
(``yes`` / ``no`` / ``unknown``), an evidence trail, and an evidence
coverage indicator.

Design rules:

- **Absence of evidence → ``unknown``**, never ``no``.
- Every ``yes`` or ``unknown`` decision must have at least one evidence
  item explaining *why*.
- ``evidence_coverage`` is a 0..1 float measuring what fraction of the
  expected evidence was actually found.
- Accreditation is displayed as context, never used to infer medical
  superiority.
"""

from __future__ import annotations

from hospital_extractor.comparison.models import (
    NO_EVIDENCE_MESSAGE,
    ComparisonEvidence,
    DimensionResult,
    EntityMatch,
    SourceConflict,
)
from hospital_extractor.comparison.target import ComparisonTarget
from hospital_extractor.models.dataset import HospitalDataset

__all__ = [
    "DIMENSION_DEFS",
    "apply_conflicts",
    "compute_dimensions",
    "compute_evidence_coverage",
    "detect_dimension_conflicts",
]

# ---------------------------------------------------------------------------
# Dimension definitions
# ---------------------------------------------------------------------------


class _DimensionDef:
    """Internal definition of one comparison dimension."""

    __slots__ = ("key", "label", "match_categories", "count_value")

    def __init__(
        self,
        key: str,
        label: str,
        match_categories: str | tuple[str, ...],
        count_value: bool = False,
    ) -> None:
        self.key = key
        self.label = label
        self.match_categories = (
            (match_categories,) if isinstance(match_categories, str) else match_categories
        )
        self.count_value = count_value


DIMENSION_DEFS: tuple[_DimensionDef, ...] = (
    _DimensionDef(
        "procedure_available", "Procedure Available",
        ("procedures", "treatments"),
    ),
    _DimensionDef(
        "relevant_department", "Relevant Department",
        ("departments", "specialities"),
    ),
    _DimensionDef(
        "relevant_specialities", "Relevant Specialities", "specialities",
        count_value=True,
    ),
    _DimensionDef(
        "relevant_doctors", "Relevant Doctors", "doctors",
        count_value=True,
    ),
    _DimensionDef(
        "relevant_treatments", "Relevant Treatments", "treatments",
        count_value=True,
    ),
    _DimensionDef(
        "related_technology", "Related Technology", "technologies",
        count_value=True,
    ),
    _DimensionDef(
        "relevant_facilities", "Relevant Facilities", "facilities",
        count_value=True,
    ),
)

# Intake boolean dimensions — one per target-related boolean key.
_BOOLEAN_DIMENSION_PREFIX = "bool_"

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_dimensions(
    dataset: HospitalDataset,
    entity_matches: list[EntityMatch],
    boolean_matches: list[EntityMatch],
    target: ComparisonTarget,
    *,
    hospital_id: str = "",
) -> list[DimensionResult]:
    """Compute all comparison dimensions for one hospital.

    ``entity_matches`` are the output of :func:`~matcher.match_entities`.
    ``boolean_matches`` are the output of :func:`~matcher.match_booleans`.
    """
    dimensions: list[DimensionResult] = []

    # Pre-group matches by entity category
    by_category: dict[str, list[EntityMatch]] = {}
    for match in entity_matches:
        by_category.setdefault(match.entity_category, []).append(match)

    # 1. Static dimensions from entity matches
    for dim_def in DIMENSION_DEFS:
        dim = _compute_entity_dimension(
            dim_def, by_category, target, hospital_id=hospital_id
        )
        dimensions.append(dim)

    # 2. Boolean dimensions from boolean matches
    for bool_match in boolean_matches:
        dim = _compute_boolean_dimension(bool_match, target, hospital_id=hospital_id)
        dimensions.append(dim)

    # 3. Infrastructure dimension — numeric values from dataset
    dim = _compute_infrastructure_dimension(dataset, hospital_id=hospital_id)
    # Replace the placeholder infrastructure dimension
    dimensions = [d for d in dimensions if d.dimension_key != "infrastructure"]
    dimensions.append(dim)

    # 4. Accreditations dimension
    dim = _compute_accreditation_dimension(dataset, hospital_id=hospital_id)
    dimensions = [d for d in dimensions if d.dimension_key != "accreditations"]
    dimensions.append(dim)

    # 5. Evidence coverage dimension
    coverage = compute_evidence_coverage(dimensions)
    dimensions.append(
        DimensionResult(
            dimension_key="evidence_coverage",
            label="Evidence Coverage",
            decision="yes" if coverage > 0 else "unknown",
            value=round(coverage, 4),
            evidence=[],
            evidence_coverage=coverage,
        )
    )

    # 6. Detect cross-source contradictions (never silently pick a winner)
    apply_conflicts(dimensions)

    return dimensions


def apply_conflicts(dimensions: list[DimensionResult]) -> None:
    """Stamp conflicts onto dimensions and force their decision to ``unknown``.

    This is the **single authority** for the conflict rule: when real evidence
    disagrees (a concrete ``yes`` from one source and a concrete ``no`` from
    another) the dimension is marked as conflicting and its decision becomes
    ``unknown`` — no winner is ever picked.  Callers deriving a decision or a
    verdict must not reimplement this rule themselves.
    """
    conflicts_by_key = {
        conflict.dimension_key: conflict
        for conflict in detect_dimension_conflicts(dimensions)
    }
    for dimension in dimensions:
        conflict = conflicts_by_key.get(dimension.dimension_key)
        if conflict is not None:
            dimension.conflicts = [conflict]
            dimension.decision = "unknown"


def detect_dimension_conflicts(
    dimensions: list[DimensionResult],
) -> list[SourceConflict]:
    """Return one :class:`SourceConflict` per dimension with contradictory real evidence.

    A conflict exists when a dimension carries *real* evidence items (not the
    ``NO_EVIDENCE_MESSAGE`` placeholder) whose decisions disagree — e.g. a
    ``yes`` from one source and a ``no`` from another.  The conflict is
    surfaced instead of picking a silent winner.
    """
    conflicts: list[SourceConflict] = []
    for dimension in dimensions:
        real = [ev for ev in dimension.evidence if ev.text != NO_EVIDENCE_MESSAGE]
        if len(real) < 2:
            continue
        decisions = {ev.decision for ev in real}
        if len(decisions) < 2:
            continue
        # A conflict requires *both* a concrete yes and a concrete no.  Mixed
        # evidence with an UNKNOWN placeholder (yes+unknown, no+unknown) is NOT
        # a conflict — UNKNOWN carries no claim, so there is no disagreement.
        if "yes" not in decisions or "no" not in decisions:
            continue
        conflicts.append(
            SourceConflict(
                dimension_key=dimension.dimension_key,
                dimension_label=dimension.label,
                description=(
                    "Sources disagree on this dimension; no silent winner was picked. "
                    "Review the conflicting facts below."
                ),
                facts=real,
            )
        )
    return conflicts


def compute_evidence_coverage(dimensions: list[DimensionResult]) -> float:
    """Compute overall evidence coverage across all dimensions.

    Coverage is the fraction of dimensions that have at least one evidence
    item with text other than the ``NO_EVIDENCE_MESSAGE`` placeholder
    (excluding the ``evidence_coverage`` dimension itself).
    """
    comparable = [
        d for d in dimensions
        if d.dimension_key != "evidence_coverage"
    ]
    if not comparable:
        return 0.0
    with_real_evidence = sum(
        1 for d in comparable
        if any(ev.text != NO_EVIDENCE_MESSAGE for ev in d.evidence)
    )
    return with_real_evidence / len(comparable)


# ---------------------------------------------------------------------------
# Entity dimension computation
# ---------------------------------------------------------------------------


def _dimension_label(dim_def: _DimensionDef, target: ComparisonTarget) -> str:
    """Present the availability dimension label for the target's category."""
    if dim_def.key != "procedure_available":
        return dim_def.label
    return {
        "department": "Department Available",
        "speciality": "Speciality Available",
        "treatment": "Treatment Available",
    }.get(target.category, dim_def.label)


def _compute_entity_dimension(
    dim_def: _DimensionDef,
    by_category: dict[str, list[EntityMatch]],
    target: ComparisonTarget,
    *,
    hospital_id: str,
) -> DimensionResult:
    """Compute a dimension from entity matches."""
    matches: list[EntityMatch] = []
    for cat in dim_def.match_categories:
        matches.extend(by_category.get(cat, []))

    if not matches:
        # No matches → unknown, with explanation
        return DimensionResult(
            dimension_key=dim_def.key,
            label=_dimension_label(dim_def, target),
            decision="unknown",
            value=None,
            evidence=[
                ComparisonEvidence(
                    hospital_id=hospital_id,
                    dimension_key=dim_def.key,
                    decision="unknown",
                    text=NO_EVIDENCE_MESSAGE,
                    source_url="",
                    method="comparison-engine",
                ),
            ],
            evidence_coverage=0.0,
        )

    # At least one match → yes
    evidence_items = [
        ComparisonEvidence(
            hospital_id=hospital_id,
            dimension_key=dim_def.key,
            decision="yes",
            text=_format_match_evidence(m),
            source_url=m.source_url,
            method=m.extraction_method or "unknown",
            source_strength=m.source_strength,
            confidence=m.confidence,
            source_type=m.source_type,
        )
        for m in matches
    ]

    if dim_def.count_value:
        value: str | int | float | list[str] | None = len(matches)
    elif len(matches) == 1:
        value = matches[0].entity_name
    else:
        value = [m.entity_name for m in matches if m.entity_name]

    return DimensionResult(
        dimension_key=dim_def.key,
        label=_dimension_label(dim_def, target),
        decision="yes",
        value=value,
        evidence=evidence_items,
        evidence_coverage=1.0,
    )


# ---------------------------------------------------------------------------
# Boolean dimension computation
# ---------------------------------------------------------------------------


def _compute_boolean_dimension(
    bool_match: EntityMatch,
    target: ComparisonTarget,
    *,
    hospital_id: str,
) -> DimensionResult:
    """Compute a dimension from a boolean intake match."""
    key = bool_match.entity_name or "unknown_boolean"

    decision = bool_match.boolean_decision or (
        "yes" if bool_match.availability_evidence else "unknown"
    )
    if decision == "yes":
        text = (
            bool_match.context
            if bool_match.availability_evidence
            else f"Boolean field '{key}' resolved as YES based on extracted evidence."
        )
    elif decision == "no":
        text = bool_match.context or (
            f"Boolean field '{key}' resolved as NO based on explicit negative evidence."
        )
    else:
        text = NO_EVIDENCE_MESSAGE

    evidence_items = [
        ComparisonEvidence(
            hospital_id=hospital_id,
            dimension_key=key,
            decision=decision,
            text=text,
            source_url=bool_match.source_url,
            method=bool_match.extraction_method or "comparison-engine",
            source_strength=bool_match.source_strength,
            confidence=bool_match.confidence,
            source_type=bool_match.source_type,
        ),
    ]

    return DimensionResult(
        dimension_key=key,
        label=_boolean_display_label(key),
        decision=decision,
        value=None,
        evidence=evidence_items,
        evidence_coverage=1.0 if bool_match.source_url else 0.0,
    )


# ---------------------------------------------------------------------------
# Infrastructure dimension
# ---------------------------------------------------------------------------


def _compute_infrastructure_dimension(
    dataset: HospitalDataset,
    *,
    hospital_id: str,
) -> DimensionResult:
    """Compute the infrastructure dimension from dataset records."""
    infra = dataset.infrastructure
    evidence_items: list[ComparisonEvidence] = []
    values: dict[str, int] = {}

    for record in infra:
        for field in ("total_beds", "icu_beds", "operation_theatres",
                       "cath_labs", "critical_care_beds"):
            val = getattr(record, field, None)
            if isinstance(val, int) and val > 0:
                values[field] = val
                evidence_items.append(
                    ComparisonEvidence(
                        hospital_id=hospital_id,
                        dimension_key="infrastructure",
                        decision="yes",
                        text=f"{field}: {val}",
                        source_url=getattr(record, "source_url", "") or "",
                        method=getattr(record, "extraction_method", None) or "unknown",
                        source_strength=getattr(record, "source_strength", None),
                        confidence=getattr(record, "confidence", None),
                        source_type=getattr(record, "source_type", None) or "website",
                    )
                )

    # Also check hospital overview for bed count
    overview = dataset.hospital
    if overview is not None:
        bed_count = getattr(overview, "bed_count", None)
        if isinstance(bed_count, int) and bed_count > 0 and "total_beds" not in values:
            values["total_beds"] = bed_count
            evidence_items.append(
                ComparisonEvidence(
                    hospital_id=hospital_id,
                    dimension_key="infrastructure",
                    decision="yes",
                    text=f"total_beds: {bed_count}",
                    source_url=getattr(overview, "source_url", "") or "",
                    method=getattr(overview, "extraction_method", None) or "unknown",
                    source_strength=getattr(overview, "source_strength", None),
                    confidence=getattr(overview, "confidence", None),
                    source_type=getattr(overview, "source_type", None) or "website",
                )
            )

    if not evidence_items:
        return DimensionResult(
            dimension_key="infrastructure",
            label="Infrastructure",
            decision="unknown",
            value=None,
            evidence=[
                ComparisonEvidence(
                    hospital_id=hospital_id,
                    dimension_key="infrastructure",
                    decision="unknown",
                    text=NO_EVIDENCE_MESSAGE,
                    source_url="",
                    method="comparison-engine",
                ),
            ],
            evidence_coverage=0.0,
        )

    return DimensionResult(
        dimension_key="infrastructure",
        label="Infrastructure",
        decision="yes",
        value=values,
        evidence=evidence_items,
        evidence_coverage=1.0,
    )


# ---------------------------------------------------------------------------
# Accreditation dimension
# ---------------------------------------------------------------------------


def _compute_accreditation_dimension(
    dataset: HospitalDataset,
    *,
    hospital_id: str,
) -> DimensionResult:
    """Compute the accreditations dimension.

    Accreditations are displayed as context only — they do not imply
    medical superiority for the target procedure.
    """
    evidence_items: list[ComparisonEvidence] = []
    accred_names: list[str] = []

    for record in dataset.accreditations:
        name = getattr(record, "name", None)
        org = getattr(record, "organization", None)
        display = " ".join(part for part in (name, org) if part).strip()
        if display:
            accred_names.append(display)
            evidence_items.append(
                ComparisonEvidence(
                    hospital_id=hospital_id,
                    dimension_key="accreditations",
                    decision="yes",
                    text=display,
                    source_url=getattr(record, "source_url", "") or "",
                    method=getattr(record, "extraction_method", None) or "unknown",
                    source_strength=getattr(record, "source_strength", None),
                    confidence=getattr(record, "confidence", None),
                    source_type=getattr(record, "source_type", None) or "website",
                )
            )

    # Also check intake for NABH status
    if dataset.hospital is not None:
        pass  # NABH status is derived from accreditations

    if not evidence_items:
        return DimensionResult(
            dimension_key="accreditations",
            label="Accreditations",
            decision="unknown",
            value=None,
            evidence=[
                ComparisonEvidence(
                    hospital_id=hospital_id,
                    dimension_key="accreditations",
                    decision="unknown",
                    text=NO_EVIDENCE_MESSAGE,
                    source_url="",
                    method="comparison-engine",
                ),
            ],
            evidence_coverage=0.0,
        )

    return DimensionResult(
        dimension_key="accreditations",
        label="Accreditations",
        decision="yes",
        value=accred_names,
        evidence=evidence_items,
        evidence_coverage=1.0,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_match_evidence(match: EntityMatch) -> str:
    """Format an entity match as human-readable evidence text."""
    parts = [match.entity_name or "Unknown entity"]
    if match.context:
        parts.append(f'— "{match.context}"')
    return " ".join(parts)


def _boolean_display_label(key: str) -> str:
    """Convert a snake_case boolean key to a human-readable label."""
    from hospital_extractor.schema.intake import INTAKE_FIELDS

    for field in INTAKE_FIELDS:
        if field.key == key:
            return field.label
    return key.replace("_", " ").title()
