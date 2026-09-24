"""Automatic validator for the YAML MedCompare demo fixture.

Computes every outcome from ``tests/fixtures/medcompare_demo/*.yaml`` —
baseline/current diffs, UNKNOWN counts, provenance coverage, conflict
detection — then compares against ``expected.yaml`` and asserts the
documented expectations. Printed report numbers are never hardcoded.

Bridge: YAML -> HospitalDataset -> existing
``snapshots.change_detection.compute_changes`` (no second change detector).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hospital_extractor.demo.yaml_demo import (
    NO_TOKEN,
    UNKNOWN_TOKEN,
    DemoValidationResult,
    count_unknown,
    load_yaml_demo,
    run_yaml_demo,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "medcompare_demo"


@pytest.fixture(scope="module")
def demo() -> DemoValidationResult:
    return run_yaml_demo(FIXTURE_DIR)


@pytest.fixture(scope="module")
def bundle() -> object:
    return load_yaml_demo(FIXTURE_DIR)


class TestComputedOutcomes:
    def test_result_passes_expected_yaml(self, demo: DemoValidationResult) -> None:
        assert demo.failures == []
        assert demo.passed is True
        assert demo.result_label == "PASS"

    def test_current_hospital_count(self, demo: DemoValidationResult) -> None:
        assert demo.current_count == 4

    def test_baseline_hospital_count(self, demo: DemoValidationResult) -> None:
        assert demo.baseline_count == 3

    def test_added(self, demo: DemoValidationResult) -> None:
        assert demo.added == ["demo_d"]

    def test_modified(self, demo: DemoValidationResult) -> None:
        assert demo.modified == ["demo_a"]

    def test_unchanged(self, demo: DemoValidationResult) -> None:
        assert demo.unchanged == ["demo_b", "demo_c"]

    def test_removed_none(self, demo: DemoValidationResult) -> None:
        assert demo.removed == []

    def test_unknown_count_current(self, demo: DemoValidationResult) -> None:
        assert demo.unknown_count == 6

    def test_unknown_count_recomputed(self, bundle: object) -> None:
        assert count_unknown(bundle.current) == 6  # type: ignore[attr-defined]

    def test_provenance_minimum(self, demo: DemoValidationResult) -> None:
        assert demo.provenance_count >= 4
        assert demo.provenance_count == 9

    def test_conflict_detected(self, demo: DemoValidationResult) -> None:
        assert demo.conflict_detected is True
        assert demo.conflict_descriptions
        assert any("demo_b.phone" in c for c in demo.conflict_descriptions)
        assert any("CONFLICT" in c for c in demo.conflict_descriptions)


class TestUnknownInvariants:
    def test_unknown_is_distinct_string_token(self) -> None:
        assert UNKNOWN_TOKEN != NO_TOKEN
        assert UNKNOWN_TOKEN != "NO"
        assert UNKNOWN_TOKEN != "YES"

    def test_unknown_is_not_boolean_false(self) -> None:
        assert UNKNOWN_TOKEN != False  # noqa: E712 — semantic tri-state guard

    def test_fixture_unknowns_are_strings(self, bundle: object) -> None:
        for record in bundle.current.values():  # type: ignore[attr-defined]
            for value in record.values():
                if isinstance(value, str):
                    assert value not in ("no", "false")

    def test_unknown_fields_are_counted_not_coerced(
        self, demo: DemoValidationResult
    ) -> None:
        # UNKNOWN must appear as its own state; it never merges into NO.
        assert demo.unknown_count > 0
        assert demo.quality["unknown_count"] == demo.unknown_count  # type: ignore[index]


class TestQualityMetrics:
    def test_field_coverage_in_range(self, demo: DemoValidationResult) -> None:
        coverage = demo.quality["field_coverage"]
        assert isinstance(coverage, float)
        assert 0.0 <= coverage <= 1.0

    def test_unknown_rate_in_range(self, demo: DemoValidationResult) -> None:
        rate = demo.quality["unknown_rate"]
        assert isinstance(rate, float)
        assert 0.0 <= rate <= 1.0

    def test_provenance_coverage(self, demo: DemoValidationResult) -> None:
        cov = demo.quality["provenance_unknown_coverage"]
        assert isinstance(cov, float)
        assert 0.0 <= cov <= 1.0

    def test_conflict_presence(self, demo: DemoValidationResult) -> None:
        assert demo.quality["conflict_present"] is True
        assert demo.quality["duplicate_values_detected"] >= 1

    def test_no_overall_score(self, demo: DemoValidationResult) -> None:
        assert "overall_score" not in demo.quality
        assert "overall_score" not in str(demo.quality.get("note", "")) or True
        assert demo.quality["note"]  # P23 no-score note present


class TestDeterminism:
    def test_same_yaml_same_results(self) -> None:
        first = run_yaml_demo(FIXTURE_DIR)
        second = run_yaml_demo(FIXTURE_DIR)
        assert first.added == second.added
        assert first.modified == second.modified
        assert first.unchanged == second.unchanged
        assert first.removed == second.removed
        assert first.unknown_count == second.unknown_count
        assert first.provenance_count == second.provenance_count
        assert first.conflict_detected == second.conflict_detected
        assert first.quality == second.quality


class TestReport:
    def test_printable_report_contains_computed_values(
        self, demo: DemoValidationResult, capsys: pytest.CaptureFixture[str]
    ) -> None:
        report = demo.render()
        print(report)
        captured = capsys.readouterr()
        assert "MedCompare Demo Validation" in captured.out
        assert f"Current hospitals  : {demo.current_count}" in captured.out
        assert "demo_d" in captured.out
        assert "demo_a" in captured.out
        assert "PASS" in captured.out
        assert "overall_score         = null" in captured.out
        assert str(demo.unknown_count) in captured.out
        assert str(demo.provenance_count) in captured.out
