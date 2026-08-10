"""Tests for the extraction coverage report and quality counters."""

from __future__ import annotations

from hospital_extractor.extraction.coverage import format_coverage_report
from hospital_extractor.extraction.engine import build_dataset
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.quality import DataQuality
from hospital_extractor.models.service import PatientService
from hospital_extractor.models.visiting import VisitingInformation

TARGET = "https://example.com"

VISITING_PAGE = """
<html><head><title>Visiting Hours</title></head>
<body>
<h1>Visiting Hours</h1>
<table>
  <tr><th>Day</th><th>Visiting Hours</th></tr>
  <tr><td>Monday</td><td>8 AM - 8 PM</td></tr>
  <tr><td>Sunday</td><td>10 AM - 6 PM</td></tr>
</table>
</body></html>
"""


class TestCoverageReport:
    def test_report_lists_pages_and_counts(self) -> None:
        dataset = HospitalDataset(
            quality=DataQuality(pages_considered=3, pages_without_information=3)
        )
        report = format_coverage_report(dataset)
        assert "Extraction coverage:" in report
        assert "  Considered:       3" in report
        assert "  With records:     0" in report
        assert "  Without records:  3" in report

    def test_report_lists_category_coverage(self) -> None:
        dataset = HospitalDataset(
            hospital=None,
            visiting_information=[
                VisitingInformation(
                    source_url=TARGET,
                    visiting_hours="Monday: 8 AM - 8 PM",
                    extraction_method="table",
                )
            ],
        )
        report = format_coverage_report(dataset)
        assert "Visiting / Admission: 1  (covered)" in report
        assert "Doctors: 0  (not found)" in report

    def test_report_lists_extraction_methods_sorted(self) -> None:
        dataset = HospitalDataset(
            quality=DataQuality(extraction_methods={"card": 1, "table": 1}),
            visiting_information=[
                VisitingInformation(source_url=TARGET, visiting_hours="8 AM - 8 PM",
                                    extraction_method="table")
            ],
            patient_services=[
                PatientService(source_url=TARGET, name="Pharmacy",
                               extraction_method="card")
            ],
        )
        report = format_coverage_report(dataset)
        assert "Extraction methods:" in report
        assert "  card: 1" in report
        assert "  table: 1" in report


class TestQualityCounters:
    def test_table_records_counted_by_method_and_category(self) -> None:
        dataset = build_dataset([(f"{TARGET}/visiting", VISITING_PAGE)], target_url=TARGET)
        quality = dataset.quality
        assert quality.records_by_category.get("Visiting / Admission") == 1
        assert quality.extraction_methods.get("table") == 1
        assert quality.pages_considered >= 1
        assert "visiting_information" in quality.categories_discovered

    def test_pages_without_information_from_difference(self) -> None:
        dataset = build_dataset([(f"{TARGET}/empty", "<html><body></body></html>")],
                                target_url=TARGET)
        assert dataset.quality.pages_without_information == 1
