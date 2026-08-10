"""Schema-level quality tests: dynamic dataset, shallow entities, deterministic
JSON, coverage states, missing-field analysis, extraction summary.
"""

from __future__ import annotations

import json
from datetime import datetime

from hospital_extractor.extraction.engine import CrawlStats, build_dataset
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.doctor import Doctor
from hospital_extractor.schema.discovery import (
    discover_categories,
    empty_categories,
    format_dataset_summary,
)
from hospital_extractor.schema.missing import analyze_missing_fields
from hospital_extractor.storage.json_store import read_dataset, write_dataset

TARGET = "https://example.com"

VISITING_PAGE = """
<html><head><title>Visiting Hours</title></head>
<body>
<h1>Visiting Hours</h1>
<table>
  <tr><th>Day</th><th>Visiting Hours</th></tr>
  <tr><td>Monday</td><td>8 AM - 8 PM</td></tr>
</table>
</body></html>
"""

# Classified "insurance" by URL/title, but carries no insurance content the
# extractor can parse -> coverage "failed" for the insurance category.
INSURANCE_CONTENT_PAGE = """
<html><head><title>Insurance</title></head>
<body>
<h1>Insurance</h1>
<p>Our hospital campus is spread across five acres with a twenty four hour
pharmacy, an in-house diagnostic centre, a modern cafeteria and ample visitor
parking for over three hundred vehicles. The front desk team assists every
visitor with directions, wheelchairs and registration. Daily cleaning and
security are provided by a dedicated facility management team.</p>
</body></html>
"""


class TestDynamicDataset:
    def test_only_populated_categories_are_discovered(self) -> None:
        dataset = build_dataset([(f"{TARGET}/visiting", VISITING_PAGE)],
                                target_url=TARGET)
        assert discover_categories(dataset) == ["visiting_information"]
        assert "doctors" in empty_categories(dataset)
        assert dataset.other_information == []
        assert dataset.doctors == []
        assert dataset.quality.categories_discovered == ["visiting_information"]

    def test_no_artificial_empty_records(self) -> None:
        dataset = build_dataset([(f"{TARGET}/visiting", VISITING_PAGE)],
                                target_url=TARGET)
        payload = json.loads(dataset.model_dump_json())
        records = [
            value
            for value in payload.values()
            if isinstance(value, list) and value
        ]
        assert all(len(bucket) >= 1 for bucket in records)

    def test_hospital_is_none_when_no_anchor_page(self) -> None:
        dataset = build_dataset([(f"{TARGET}/visiting", VISITING_PAGE)],
                                target_url=TARGET)
        assert dataset.hospital is None


class TestShallowEntities:
    def test_doctor_fields_are_all_scalar(self) -> None:
        doctor = Doctor(name="Dr. A Sharma", speciality="Cardiology",
                        source_url=f"{TARGET}/d")
        scalars = (str, int, float, bool, type(None), datetime)
        for value in doctor.model_dump().values():
            assert isinstance(value, scalars), f"nested value: {value!r}"

    def test_doctor_has_no_deep_biography_fields(self) -> None:
        deep_fields = {"publications", "education", "memberships", "awards",
                       "university", "fellowships"}
        assert deep_fields.isdisjoint(Doctor.model_fields)


class TestDeterministicJson:
    def test_same_dataset_serializes_identically(self) -> None:
        dataset = build_dataset([(f"{TARGET}/visiting", VISITING_PAGE)],
                                target_url=TARGET)
        copy = dataset.model_copy(deep=True)
        assert dataset.model_dump_json() == copy.model_dump_json()

    def test_write_dataset_produces_identical_bytes(self, tmp_path) -> None:
        dataset = build_dataset([(f"{TARGET}/visiting", VISITING_PAGE)],
                                target_url=TARGET)
        one = write_dataset(dataset, tmp_path / "a.json")
        two = write_dataset(dataset, tmp_path / "b.json")
        assert one.read_text(encoding="utf-8") == two.read_text(encoding="utf-8")

    def test_read_round_trip_preserves_quality(self, tmp_path) -> None:
        dataset = build_dataset([(f"{TARGET}/visiting", VISITING_PAGE)],
                                target_url=TARGET)
        path = write_dataset(dataset, tmp_path / "dataset.json")
        restored = read_dataset(path)
        assert restored.quality.category_coverage == dataset.quality.category_coverage
        assert restored.quality.records_by_category == dataset.quality.records_by_category

    def test_quality_metadata_is_serialized(self, tmp_path) -> None:
        dataset = build_dataset([(f"{TARGET}/visiting", VISITING_PAGE)],
                                target_url=TARGET)
        path = write_dataset(dataset, tmp_path / "dataset.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert "quality" in payload
        assert "category_coverage" in payload["quality"]
        assert "missing_fields" in payload["quality"]


class TestCoverageStates:
    def test_found_and_not_found_states(self) -> None:
        dataset = build_dataset([(f"{TARGET}/visiting", VISITING_PAGE)],
                                target_url=TARGET)
        coverage = dataset.quality.category_coverage
        assert coverage["Visiting / Admission"] == "found"
        assert coverage["Doctors"] == "not_found"
        assert coverage["Facilities"] == "not_found"

    def test_content_failed_state(self) -> None:
        dataset = build_dataset(
            [(f"{TARGET}/visiting", VISITING_PAGE),
             (f"{TARGET}/insurance/help", INSURANCE_CONTENT_PAGE)],
            target_url=TARGET,
        )
        coverage = dataset.quality.category_coverage
        assert coverage["Insurance & Payment"] == "failed"
        assert any("no records extracted" in w for w in dataset.quality.warnings)

    def test_short_pages_are_not_failed(self) -> None:
        dataset = build_dataset(
            [(f"{TARGET}/visiting", VISITING_PAGE),
             (f"{TARGET}/insurance/help", "<html><body><p>short</p></body></html>")],
            target_url=TARGET,
        )
        coverage = dataset.quality.category_coverage
        assert coverage["Insurance & Payment"] == "not_found"


class TestMissingFields:
    def test_missing_fields_exclude_provenance(self) -> None:
        dataset = build_dataset([(f"{TARGET}/visiting", VISITING_PAGE)],
                                target_url=TARGET)
        missing = dataset.quality.missing_fields
        assert "Visiting / Admission" in missing
        assert "visiting_hours" not in missing["Visiting / Admission"]
        assert "source_url" not in missing["Visiting / Admission"]
        assert "opd_hours" in missing["Visiting / Admission"]

    def test_analyze_missing_fields_direct(self) -> None:
        dataset = build_dataset([(f"{TARGET}/visiting", VISITING_PAGE)],
                                target_url=TARGET)
        result = analyze_missing_fields(dataset, dataset.quality.category_coverage)
        assert result["Visiting / Admission"] == [
            "opd_hours",
            "appointment_information",
            "registration_information",
            "admission_information",
            "discharge_information",
        ]


class TestExtractionSummary:
    def test_summary_includes_coverage_and_missing_fields(self) -> None:
        dataset = build_dataset([(f"{TARGET}/visiting", VISITING_PAGE)],
                                target_url=TARGET)
        summary = format_dataset_summary(dataset)
        assert "Category coverage:" in summary
        assert "Visiting / Admission: found" in summary
        assert "Missing fields:" in summary
        assert "opd_hours" in summary
        assert "Visiting / Admission: 1" in summary

    def test_coverage_report_uses_coverage_states(self) -> None:
        from hospital_extractor.extraction.coverage import format_coverage_report

        dataset = build_dataset(
            [(f"{TARGET}/visiting", VISITING_PAGE),
             (f"{TARGET}/insurance/help", INSURANCE_CONTENT_PAGE)],
            target_url=TARGET,
        )
        report = format_coverage_report(dataset)
        assert "Insurance & Payment: 0  (failed)" in report
        assert "Visiting / Admission: 1  (found)" in report


class TestCrawlStats:
    def test_crawl_stats_feed_quality_metadata(self) -> None:
        stats = CrawlStats(
            pages_discovered=10,
            pages_fetched=5,
            pages_failed=3,
            pages_skipped=2,
        )
        dataset = build_dataset(
            [(f"{TARGET}/visiting", VISITING_PAGE)],
            target_url=TARGET,
            crawl_stats=stats,
        )
        quality = dataset.quality
        assert quality.pages_crawled == 5
        assert quality.pages_classified == 5
        assert quality.pages_parsed == 1
        assert quality.pages_considered == 1
        assert any("3 page(s) inaccessible" in w for w in quality.warnings)

    def test_without_crawl_stats_falls_back_to_analyzed(self) -> None:
        dataset = build_dataset([(f"{TARGET}/visiting", VISITING_PAGE)],
                                target_url=TARGET)
        assert dataset.quality.pages_crawled == 1
        assert dataset.quality.pages_classified == 1

    def test_empty_pages_builds_empty_dataset(self) -> None:
        dataset = build_dataset([], target_url=TARGET)
        assert dataset.hospital is None
        assert dataset.quality.category_coverage
        assert all(
            status == "not_found"
            for status in dataset.quality.category_coverage.values()
        )


class TestDatasetModel:
    def test_dataset_is_hospital_dataset_instance(self) -> None:
        dataset = build_dataset([(f"{TARGET}/visiting", VISITING_PAGE)],
                                target_url=TARGET)
        assert isinstance(dataset, HospitalDataset)
        assert dataset.quality is not None
