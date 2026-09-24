"""P26 research benchmark tests (deterministic fixtures, no network).

Covers the P26 matrix without fabricating accuracy claims:

- Package import + public API surface
- Models: no overall score; AVAILABLE / NOT_MEASURED / NOT_APPLICABLE
- Fixture determinism and ground-truth construction
- Runner: all fixture cases pass; observation cases never fail
- Case filter
- JSON / Markdown formatters with raw counts
- Real-world observations reuse documented prior runs only
- CLI smoke: --format, --output, --case
- Secret safety: password / tokens never appear in report output
"""

from __future__ import annotations

import json
from pathlib import Path

from hospital_extractor.benchmark import (
    AVAILABLE,
    NOT_APPLICABLE,
    NOT_MEASURED,
    BenchmarkRun,
    BenchmarkSummary,
    EnvironmentInfo,
    MetricResult,
    MetricStatus,
    RealWorldObservation,
    format_benchmark_json,
    format_benchmark_markdown,
    run_benchmarks,
)
from hospital_extractor.benchmark.fixtures import build_fixtures
from hospital_extractor.benchmark.models import BenchmarkCase, BenchmarkResult
from hospital_extractor.cli import build_benchmark_parser
from hospital_extractor.cli import main as cli_main

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestBenchmarkModels:
    def test_metric_status_values(self) -> None:
        assert MetricStatus.AVAILABLE.value == "available"
        assert MetricStatus.NOT_MEASURED.value == "not_measured"
        assert MetricStatus.NOT_APPLICABLE.value == "not_applicable"
        assert AVAILABLE is MetricStatus.AVAILABLE
        assert NOT_MEASURED is MetricStatus.NOT_MEASURED
        assert NOT_APPLICABLE is MetricStatus.NOT_APPLICABLE

    def test_metric_available_keeps_raw_counts(self) -> None:
        m = MetricResult.available(
            name="n",
            definition="d",
            value=0.5,
            numerator=1,
            denominator=2,
            expected=1.0,
            passed=True,
        )
        assert m.status is AVAILABLE
        assert m.numerator == 1
        assert m.denominator == 2

    def test_metric_not_measured_and_not_applicable(self) -> None:
        nm = MetricResult.not_measured(name="a", definition="d", detail="x")
        na = MetricResult.not_applicable(name="b", definition="d", detail="y")
        assert nm.status is NOT_MEASURED
        assert na.status is NOT_APPLICABLE
        assert nm.value is None
        assert na.value is None

    def test_summary_has_no_overall_score_field(self) -> None:
        summary = BenchmarkSummary(
            cases_total=1,
            cases_passed=1,
            cases_failed=0,
            cases_observation=0,
            cases_error=0,
            metrics_available=1,
            metrics_not_measured=0,
            metrics_not_applicable=0,
            metrics_passed=1,
            metrics_failed=0,
            total_duration_ms=1.0,
        )
        assert "overall_score" not in BenchmarkSummary.model_fields
        assert "score" not in BenchmarkSummary.model_fields
        assert "No composite overall score" in summary.note

    def test_run_has_no_overall_score_field(self) -> None:
        run = BenchmarkRun(
            environment=EnvironmentInfo.capture(),
            methodology="m",
        )
        assert "overall_score" not in BenchmarkRun.model_fields
        assert run.summary is None
        run.build_summary()
        assert isinstance(run.summary, BenchmarkSummary)
        assert "overall_score" not in BenchmarkSummary.model_fields

    def test_observation_status_is_not_pass(self) -> None:
        result = BenchmarkResult(
            case=BenchmarkCase(
                case_id="x",
                label="X",
                category="observation",
            ),
            passed=None,
            status="observation",
        )
        assert result.status == "observation"
        assert result.passed is None

    def test_real_world_observation_is_not_accuracy(self) -> None:
        obs = RealWorldObservation(
            source_id="s",
            url="https://example.test",
            recorded_date="2026-09-24",
            classification="observed",
            summary="prior run",
            raw_counts={"pages": 1},
        )
        assert obs.measurement_kind == "real_world_observation"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class TestFixtures:
    def test_fixtures_are_deterministic(self) -> None:
        a = build_fixtures()
        b = build_fixtures()
        assert a.ground_truth == b.ground_truth
        assert a.unchanged.hospital is not None
        assert b.unchanged.hospital is not None
        assert a.unchanged.hospital.bed_count == b.unchanged.hospital.bed_count
        assert a.reconciliation_transitions == b.reconciliation_transitions

    def test_ground_truth_bed_counts(self) -> None:
        fx = build_fixtures()
        assert fx.ground_truth["unchanged_bed_count"] == 300
        assert fx.ground_truth["modified_bed_count"] == 450
        assert fx.ground_truth["unknown_bed_count"] is None
        assert fx.unchanged.hospital is not None
        assert fx.unchanged.hospital.bed_count == 300
        assert fx.modified.hospital is not None
        assert fx.modified.hospital.bed_count == 450

    def test_unknown_bed_count_stays_none(self) -> None:
        fx = build_fixtures()
        assert fx.unknown.hospital is not None
        assert fx.unknown.hospital.bed_count is None

    def test_reconciliation_ground_truth_matches_spec_table(self) -> None:
        fx = build_fixtures()
        assert fx.reconciliation_transitions["open"] == [
            "under_review",
            "resolved",
            "dismissed",
        ]
        assert fx.reconciliation_transitions["resolved"] == ["reopened"]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class TestRunner:
    def test_all_fixture_cases_pass(self) -> None:
        run = run_benchmarks()
        failures = [c.case.case_id for c in run.cases if c.status == "fail"]
        errors = [c.case.case_id for c in run.cases if c.status == "error"]
        assert failures == []
        assert errors == []
        assert len(run.cases) >= 13
        assert run.summary is not None
        assert run.summary.cases_failed == 0
        assert run.summary.cases_error == 0
        assert run.summary.cases_observation >= 1

    def test_observation_cases_never_fail(self) -> None:
        run = run_benchmarks()
        for case in run.cases:
            if case.status == "observation":
                assert case.passed is None
                for metric in case.metrics:
                    assert metric.passed is not True

    def test_case_filter_by_id(self) -> None:
        run = run_benchmarks(case_filter="extraction")
        assert len(run.cases) == 1
        assert run.cases[0].case.case_id == "extraction"

    def test_case_filter_unknown_returns_empty(self) -> None:
        run = run_benchmarks(case_filter="no_such_case_xyz")
        assert run.cases == []

    def test_performance_is_observation_not_pass(self) -> None:
        run = run_benchmarks(case_filter="performance")
        assert len(run.cases) == 1
        assert run.cases[0].status == "observation"
        statuses = {m.status for m in run.cases[0].metrics}
        assert NOT_MEASURED in statuses
        assert NOT_APPLICABLE in statuses

    def test_real_world_observations_present_and_documented(self) -> None:
        run = run_benchmarks()
        ids = {o.source_id for o in run.real_world_observations}
        assert ids == {
            "max_healthcare",
            "manipal_hospitals",
            "apollo_hospitals",
            "fortis_healthcare",
        }
        for obs in run.real_world_observations:
            assert obs.recorded_date == "2026-09-24"
            assert obs.evidence_doc.startswith("docs/")
            assert obs.measurement_kind == "real_world_observation"
            assert obs.raw_counts

    def test_max_healthcare_raw_counts_match_prior_report(self) -> None:
        run = run_benchmarks()
        max_obs = next(o for o in run.real_world_observations if o.source_id == "max_healthcare")
        assert max_obs.raw_counts["pages_fetched"] == 103
        assert max_obs.raw_counts["records_extracted"] == 1632
        assert max_obs.raw_counts["duplicates_removed"] == 804
        assert max_obs.raw_counts["conflicts_detected"] == 152

    def test_limitations_include_accuracy_vs_correctness(self) -> None:
        run = run_benchmarks()
        joined = " ".join(run.limitations)
        assert "TEST CORRECTNESS" in joined
        assert "accuracy" in joined.lower() or "NOT MEASURED" in joined
        assert any("No composite overall score" in item for item in run.limitations)

    def test_available_metrics_carry_value_or_explicit_raw_counts(self) -> None:
        run = run_benchmarks()
        for case in run.cases:
            for metric in case.metrics:
                if metric.status is not AVAILABLE:
                    continue
                # Boolean UNKNOWN preservation legitimately has value=None;
                # every other AVAILABLE metric must expose a value or raw count.
                if metric.name == "extraction_unknown_preserved":
                    assert metric.value is None
                    assert metric.expected is None
                    assert metric.passed is True
                    continue
                assert metric.value is not None or metric.numerator is not None


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


class TestFormatters:
    def test_json_round_trip(self) -> None:
        run = run_benchmarks(case_filter="extraction")
        text = format_benchmark_json(run)
        data = json.loads(text)
        assert data["methodology"]
        assert data["cases"]
        assert "overall_score" not in data
        assert data["summary"]["note"]
        restored = BenchmarkRun.model_validate(data)
        assert restored.run_id == run.run_id
        assert len(restored.cases) == len(run.cases)

    def test_markdown_contains_required_sections(self) -> None:
        run = run_benchmarks()
        md = format_benchmark_markdown(run)
        assert md.startswith("# Hospital Website Extractor")
        assert "## Summary (no composite score)" in md
        assert "## Cases" in md
        assert "## Real-world observations" in md
        assert "## Limitations" in md
        assert "TEST CORRECTNESS" in md
        assert "REAL-WORLD ACCURACY" in md
        assert "No composite overall score is reported." in md

    def test_json_and_markdown_have_raw_counts(self) -> None:
        run = run_benchmarks(case_filter="extraction")
        text = format_benchmark_json(run)
        data = json.loads(text)
        provenance = None
        for case in data["cases"]:
            for metric in case["metrics"]:
                if metric["name"] == "extraction_provenance_coverage":
                    provenance = metric
        assert provenance is not None
        assert provenance["numerator"] is not None
        assert provenance["denominator"] is not None

    def test_outputs_do_not_contain_fixture_password(self) -> None:
        run = run_benchmarks()
        text = format_benchmark_json(run) + format_benchmark_markdown(run)
        assert "Bench-Str0ng-x" not in text
        assert "password_hash" not in text or "scrypt" in text

    def test_outputs_do_not_claim_composite_score(self) -> None:
        run = run_benchmarks()
        text = format_benchmark_json(run)
        data = json.loads(text)
        assert "overall_score" not in data
        assert "composite_overall_score" not in data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCliBenchmark:
    def test_parser_defaults(self) -> None:
        args = build_benchmark_parser().parse_args([])
        assert args.format == "json"
        assert args.output is None
        assert args.case is None

    def test_parser_rejects_bad_format(self) -> None:
        try:
            build_benchmark_parser().parse_args(["--format", "yaml"])
        except SystemExit as exc:
            assert exc.code == 2
        else:  # pragma: no cover
            raise AssertionError("expected SystemExit for bad --format")

    def test_cli_benchmark_json_stdout(
        self, capsys: object, tmp_path: Path
    ) -> None:
        code = cli_main(["benchmark", "--case", "extraction"])
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        assert code == 0
        data = json.loads(captured.out)
        assert data["cases"]
        assert data["cases"][0]["case"]["case_id"] == "extraction"

    def test_cli_benchmark_markdown_output_file(self, tmp_path: Path) -> None:
        out = tmp_path / "bench.md"
        code = cli_main(
            ["benchmark", "--format", "markdown", "--case", "recon", "--output", str(out)]
        )
        # --case "recon" does not match a case id; empty run still exits 0
        # unless case is unknown — our runner returns empty cases and CLI
        # returns 0 when summary has no failures. Re-run with known id:
        if code != 0 or not out.exists():
            code = cli_main(
                [
                    "benchmark",
                    "--format",
                    "markdown",
                    "--case",
                    "reconciliation",
                    "--output",
                    str(out),
                ]
            )
        assert out.exists()
        text = out.read_text(encoding="utf-8")
        assert text.startswith("# Hospital Website Extractor")
        assert "No composite overall score" in text

    def test_cli_benchmark_unknown_case_exits_nonzero(
        self, capsys: object
    ) -> None:
        code = cli_main(["benchmark", "--case", "does_not_exist_case"])
        assert code != 0

    def test_cli_dispatch_is_registered(self) -> None:
        # main() routes on argv[0] == "benchmark" before the default crawl path
        assert cli_main.__code__.co_varnames  # callable
        # Smoke: help for the subcommand parser
        help_text = build_benchmark_parser().format_help()
        assert "benchmark" in help_text
        assert "--format" in help_text
        assert "--output" in help_text
        assert "--case" in help_text


# ---------------------------------------------------------------------------
# Hygiene / honesty guards
# ---------------------------------------------------------------------------


class TestHonestyGuards:
    def test_no_fabricated_accuracy_metrics_marked_available(self) -> None:
        run = run_benchmarks()
        for case in run.cases:
            for metric in case.metrics:
                if "accuracy" in metric.name and "real_world" in metric.name:
                    assert metric.status is NOT_MEASURED

    def test_docker_and_remote_ci_are_not_measured(self) -> None:
        run = run_benchmarks()
        by_id = {c.case.case_id: c for c in run.cases}
        for cid in ("docker_runtime", "remote_ci"):
            assert cid in by_id
            assert by_id[cid].status == "observation"
            for metric in by_id[cid].metrics:
                assert metric.status is NOT_MEASURED

    def test_package_version_recorded(self) -> None:
        run = run_benchmarks(package_version="0.1.0")
        assert run.environment.package_version == "0.1.0"
        assert run.environment.python_version.startswith("3.")

    def test_repo_pyproject_declares_benchmark_capability_implicitly(self) -> None:
        # Entry point already existed; ensure pyproject still healthy for P26
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert 'hospital-extractor = "hospital_extractor.cli:main"' in text
        assert 'version = "0.1.0"' in text
