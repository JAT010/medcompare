"""Comparison data models.

All models consumed or produced by the comparison engine.  They are pure
data containers with no extraction, crawling or scoring logic.

Design principles (inherited from the extractor):

- Every claim carries explicit provenance — source URL, extraction method,
  source strength and optional confidence.
- Absence of evidence is NEVER treated as a negative claim.  The standard
  placeholder for missing evidence is
  :data:`NO_EVIDENCE_MESSAGE`.
- ``BooleanDecision`` is ``"yes" | "no" | "unknown"`` — the same tri-state
  used by the existing intake resolver.
- All models are Pydantic v2 ``BaseModel`` instances so they round-trip
  through JSON cleanly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BooleanDecision",
    "ComparisonEvidence",
    "ComparisonResult",
    "ComparisonTarget",
    "DimensionResult",
    "EntityMatch",
    "HospitalComparison",
    "NO_EVIDENCE_MESSAGE",
    "SourceConflict",
]

BooleanDecision = Literal["yes", "no", "unknown"]

# Canonical language for absent evidence — never fabricate a negative claim.
NO_EVIDENCE_MESSAGE = "No supporting evidence found in analyzed pages."


def _utc_now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Target
# ---------------------------------------------------------------------------


class ComparisonTarget(BaseModel):
    """What the user wants to compare hospitals for.

    ``category`` is the canonical entity category the target belongs to
    (``"procedure"``, ``"treatment"``, ``"speciality"``, ``"department"``).
    ``keywords`` are the normalised search terms derived from ``name``.
    ``intake_boolean_keys`` are the related boolean keys from the 25-field
    intake schema (e.g. ``total_knee_replacement`` for "Knee Replacement").
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str
    category: str
    keywords: list[str] = Field(default_factory=list)
    intake_boolean_keys: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Entity matching
# ---------------------------------------------------------------------------


class EntityMatch(BaseModel):
    """One extracted entity that matched the comparison target.

    ``relevance_score`` is a deterministic 0..1 confidence that the entity
    relates to the target procedure / department.  It is NOT a probability;
    it is a keyword-signal strength computed by the matcher.

    ``availability_evidence`` indicates whether this particular entity
    provides *service-level* evidence (the hospital actually *offers* the
    target) as opposed to a generic text mention (e.g. a blog post about
    recovery).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    hospital_id: str
    entity_category: str
    entity_name: str | None = None
    relevance_score: float = 0.0
    availability_evidence: bool = False
    boolean_decision: BooleanDecision | None = None
    source_url: str = ""
    extraction_method: str | None = None
    source_strength: str | None = None
    confidence: float | None = None
    context: str | None = None
    source_type: str = "website"


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


class ComparisonEvidence(BaseModel):
    """One discrete piece of evidence supporting a comparison claim.

    Every field that makes a claim about a hospital's capability must carry
    at least one ``ComparisonEvidence`` item so the end user can trace the
    claim back to the original source.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    hospital_id: str
    dimension_key: str
    decision: BooleanDecision
    text: str
    source_url: str
    method: str
    source_strength: str | None = None
    confidence: float | None = None
    source_type: str = "website"


# ---------------------------------------------------------------------------
# Source conflict
# ---------------------------------------------------------------------------


class SourceConflict(BaseModel):
    """Contradictory evidence for one dimension (no silent winner).

    Reported whenever a dimension carries real evidence items with mixed
    decisions (e.g. a ``yes`` from one source and a ``no`` from another).
    The platform never silently picks a winner — the conflict is surfaced
    with every contributing fact so a human can judge.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    dimension_key: str
    dimension_label: str
    description: str
    facts: list[ComparisonEvidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Dimension
# ---------------------------------------------------------------------------


class DimensionResult(BaseModel):
    """One comparison dimension for one hospital.

    ``decision`` is the tri-state answer for this dimension.
    ``value`` carries an optional payload: a count, a list of matched entity
    names, or a numeric infrastructure value.
    ``evidence`` lists every discrete evidence item that supports (or
    contradicts) the decision.
    ``evidence_coverage`` is a 0..1 indicator of how well-supported the
    decision is given the available evidence.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    dimension_key: str
    label: str
    decision: BooleanDecision = "unknown"
    value: str | int | float | list[str] | dict[str, int] | None = None
    evidence: list[ComparisonEvidence] = Field(default_factory=list)
    evidence_coverage: float = 0.0
    conflicts: list[SourceConflict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Hospital comparison
# ---------------------------------------------------------------------------


class HospitalComparison(BaseModel):
    """Full comparison profile of one hospital against the target.

    Aggregates all dimension results and the overall evidence coverage for
    this hospital.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    hospital_id: str
    hospital_name: str | None = None
    target: ComparisonTarget
    dimensions: list[DimensionResult] = Field(default_factory=list)
    evidence_coverage: float = 0.0
    quality_warnings: list[str] = Field(default_factory=list)
    source_type: str = "website"
    provider: str | None = None
    source_types: list[str] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Top-level result
# ---------------------------------------------------------------------------


class ComparisonResult(BaseModel):
    """The complete multi-hospital comparison.

    This is the top-level output of the comparison engine.  It is
    serialisable to JSON and can be served directly by the API or the CLI.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    target: ComparisonTarget
    hospitals: list[HospitalComparison] = Field(default_factory=list)
    compared_at: datetime = Field(default_factory=_utc_now)
