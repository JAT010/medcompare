"""Reproducible research benchmarking (P26).

Honest measurement only:

- Deterministic fixtures with explicit ground truth (TEST CORRECTNESS).
- Separate real-world observations that reuse documented prior runs
  (REAL-WORLD OBSERVATION — never re-labeled as model accuracy).
- No opaque overall score; every metric keeps raw counts and a status of
  AVAILABLE / NOT_MEASURED / NOT_APPLICABLE.
"""

from __future__ import annotations

from hospital_extractor.benchmark.formatters import (
    format_benchmark_json,
    format_benchmark_markdown,
)
from hospital_extractor.benchmark.models import (
    AVAILABLE,
    NOT_APPLICABLE,
    NOT_MEASURED,
    BenchmarkCase,
    BenchmarkResult,
    BenchmarkRun,
    BenchmarkSummary,
    EnvironmentInfo,
    MetricResult,
    MetricStatus,
    RealWorldObservation,
)
from hospital_extractor.benchmark.runner import run_benchmarks

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
    "format_benchmark_json",
    "format_benchmark_markdown",
    "run_benchmarks",
]
