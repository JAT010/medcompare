"""Typed benchmark models (P26). No overall score; raw counts always kept."""

from __future__ import annotations

import platform
import sys
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AVAILABLE",
    "NOT_APPLICABLE",
    "NOT_MEASURED",
    "BenchmarkCase",
    "BenchmarkResult",
    "BenchmarkRun",
    "BenchmarkSummary",
    "EnvironmentInfo",
    "MetricResult",
    "MetricStatus",
    "RealWorldObservation",
]


class MetricStatus(StrEnum):
    """Whether a metric was actually produced in this run."""

    AVAILABLE = "available"
    NOT_MEASURED = "not_measured"
    NOT_APPLICABLE = "not_applicable"


AVAILABLE = MetricStatus.AVAILABLE
NOT_MEASURED = MetricStatus.NOT_MEASURED
NOT_APPLICABLE = MetricStatus.NOT_APPLICABLE


class EnvironmentInfo(BaseModel):
    """Machine/runtime context for reproducibility (no secrets)."""

    model_config = ConfigDict(frozen=True)

    python_version: str
    platform: str
    package_version: str
    implementation: str = Field(default_factory=lambda: sys.implementation.name)

    @classmethod
    def capture(cls, package_version: str = "0.1.0") -> EnvironmentInfo:
        return cls(
            python_version=platform.python_version(),
            platform=f"{platform.system()} {platform.release()}",
            package_version=package_version,
        )


class MetricResult(BaseModel):
    """One decomposed metric with raw counts behind every derived value."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str
    definition: str
    status: MetricStatus = MetricStatus.AVAILABLE
    unit: str = "count"
    # Raw counts (always present when AVAILABLE).
    numerator: int | float | None = None
    denominator: int | float | None = None
    # Derived value only when denominator is known / applicable.
    value: int | float | bool | str | None = None
    # Expected value from ground truth (fixture cases only).
    expected: int | float | bool | str | None = None
    passed: bool | None = None
    detail: str = ""
    fixture_id: str = ""

    @classmethod
    def available(
        cls,
        *,
        name: str,
        definition: str,
        value: int | float | bool | str | None,
        numerator: int | float | None = None,
        denominator: int | float | None = None,
        expected: int | float | bool | str | None = None,
        passed: bool | None = None,
        unit: str = "count",
        detail: str = "",
        fixture_id: str = "",
    ) -> MetricResult:
        return cls(
            name=name,
            definition=definition,
            status=AVAILABLE,
            value=value,
            numerator=numerator,
            denominator=denominator,
            expected=expected,
            passed=passed,
            unit=unit,
            detail=detail,
            fixture_id=fixture_id,
        )

    @classmethod
    def not_measured(cls, *, name: str, definition: str, detail: str) -> MetricResult:
        return cls(name=name, definition=definition, status=NOT_MEASURED, detail=detail)

    @classmethod
    def not_applicable(cls, *, name: str, definition: str, detail: str) -> MetricResult:
        return cls(name=name, definition=definition, status=NOT_APPLICABLE, detail=detail)


class BenchmarkCase(BaseModel):
    """One named scenario under test."""

    model_config = ConfigDict(str_strip_whitespace=True)

    case_id: str
    label: str
    category: str
    description: str = ""
    fixture_ids: list[str] = Field(default_factory=list)
    ground_truth_source: str = "deterministic_fixture"


class BenchmarkResult(BaseModel):
    """Outcome of one case: pass/fail + metrics + timing."""

    model_config = ConfigDict(str_strip_whitespace=True)

    case: BenchmarkCase
    # pass = fixture/ground-truth checks passed; None = observation only
    passed: bool | None = None
    status: Literal["pass", "fail", "observation", "error"] = "observation"
    metrics: list[MetricResult] = Field(default_factory=list)
    duration_ms: float = 0.0
    error: str = ""
    notes: str = ""

    @property
    def available_metrics(self) -> list[MetricResult]:
        return [m for m in self.metrics if m.status is AVAILABLE]


class RealWorldObservation(BaseModel):
    """Documented prior real-world run — observation, not model accuracy."""

    model_config = ConfigDict(str_strip_whitespace=True)

    source_id: str
    url: str
    recorded_date: str
    classification: str
    summary: str
    known_limitations: list[str] = Field(default_factory=list)
    evidence_doc: str = ""
    raw_counts: dict[str, int | float | str] = Field(default_factory=dict)
    measurement_kind: str = "real_world_observation"


class BenchmarkSummary(BaseModel):
    """Aggregates only — never a single opaque score."""

    model_config = ConfigDict(frozen=True)

    cases_total: int
    cases_passed: int
    cases_failed: int
    cases_observation: int
    cases_error: int
    metrics_available: int
    metrics_not_measured: int
    metrics_not_applicable: int
    metrics_passed: int
    metrics_failed: int
    total_duration_ms: float
    note: str = (
        "No composite overall score is computed. Metric statuses are "
        "AVAILABLE / NOT_MEASURED / NOT_APPLICABLE; failures are never hidden."
    )


class BenchmarkRun(BaseModel):
    """Full benchmark document suitable for paper transfer."""

    model_config = ConfigDict(str_strip_whitespace=True)

    run_id: str = Field(default_factory=lambda: f"bench_{uuid4().hex[:16]}")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    environment: EnvironmentInfo
    methodology: str
    cases: list[BenchmarkResult] = Field(default_factory=list)
    real_world_observations: list[RealWorldObservation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    summary: BenchmarkSummary | None = None

    def build_summary(self) -> BenchmarkSummary:
        cases_passed = sum(1 for c in self.cases if c.status == "pass")
        cases_failed = sum(1 for c in self.cases if c.status == "fail")
        cases_obs = sum(1 for c in self.cases if c.status == "observation")
        cases_err = sum(1 for c in self.cases if c.status == "error")
        all_metrics = [m for c in self.cases for m in c.metrics]
        summary = BenchmarkSummary(
            cases_total=len(self.cases),
            cases_passed=cases_passed,
            cases_failed=cases_failed,
            cases_observation=cases_obs,
            cases_error=cases_err,
            metrics_available=sum(1 for m in all_metrics if m.status is AVAILABLE),
            metrics_not_measured=sum(1 for m in all_metrics if m.status is NOT_MEASURED),
            metrics_not_applicable=sum(
                1 for m in all_metrics if m.status is NOT_APPLICABLE
            ),
            metrics_passed=sum(1 for m in all_metrics if m.passed is True),
            metrics_failed=sum(1 for m in all_metrics if m.passed is False),
            total_duration_ms=round(sum(c.duration_ms for c in self.cases), 3),
        )
        object.__setattr__(self, "summary", summary)
        return summary
