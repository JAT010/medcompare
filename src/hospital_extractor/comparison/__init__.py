"""Hospital comparison: criteria, evidence provenance and matrix assembly.

Public surface used by the API layer and the tests. Nothing in this package
performs network access or database access on import; the API resolves
facilities and passes them in.
"""

from __future__ import annotations

from hospital_extractor.comparison.capabilities import assess_capabilities
from hospital_extractor.comparison.evidence import (
    evidence_from_intake,
    registry_evidence,
)
from hospital_extractor.comparison.interpret import interpret_prompt, standard_criteria
from hospital_extractor.comparison.schemas import (
    CapabilityReport,
    CapabilityStatus,
    ColumnCoverage,
    ComparisonCell,
    ComparisonColumn,
    ComparisonRequest,
    ComparisonResponse,
    ComparisonRow,
    CrawlRequest,
    CrawlResponse,
    CrawlRunDetail,
    CriterionDefinition,
    EvidenceItem,
    EvidenceSource,
    EvidenceStatus,
    HospitalInput,
    InterpretRequest,
    InterpretResponse,
    RegisteredHospital,
    SourceStatus,
    UnregisteredHospital,
)
from hospital_extractor.comparison.service import (
    ComparisonSubject,
    build_comparison,
    resolve_criteria,
)

__all__ = [
    "CapabilityReport",
    "CapabilityStatus",
    "ComparisonCell",
    "ComparisonColumn",
    "ComparisonRequest",
    "ComparisonResponse",
    "ComparisonRow",
    "ComparisonSubject",
    "ColumnCoverage",
    "CriterionDefinition",
    "CrawlRequest",
    "CrawlResponse",
    "CrawlRunDetail",
    "EvidenceItem",
    "EvidenceSource",
    "EvidenceStatus",
    "HospitalInput",
    "InterpretRequest",
    "InterpretResponse",
    "RegisteredHospital",
    "SourceStatus",
    "UnregisteredHospital",
    "assess_capabilities",
    "build_comparison",
    "evidence_from_intake",
    "interpret_prompt",
    "registry_evidence",
    "resolve_criteria",
    "standard_criteria",
]
