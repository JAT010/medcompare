"""Tests for the master CSV / Excel export of the 25-field intake."""

from __future__ import annotations

import csv

from hospital_extractor.export import dataset_basename, export_master_csv, export_master_excel
from hospital_extractor.extraction import build_dataset
from hospital_extractor.extraction.intake import project_intake
from hospital_extractor.schema.intake import intake_labels


def test_master_csv_writes_header_and_row(pages, config, tmp_path):
    dataset = build_dataset(pages, target_url="https://example.test/", config=config)
    intake = project_intake(dataset)

    csv_path, quality_path = export_master_csv(intake, tmp_path, dataset=dataset)

    assert csv_path.exists()
    assert quality_path.exists()

    rows = list(csv.reader(csv_path.read_text(encoding="utf-8").splitlines()))
    assert len(rows) == 2  # header + one hospital row
    assert rows[0] == intake_labels()
    assert rows[1][0] == "City Hospital"
    assert "411001" in rows[1]


def test_master_excel_writes_file(pages, config, tmp_path):
    dataset = build_dataset(pages, target_url="https://example.test/", config=config)
    intake = project_intake(dataset)

    xlsx_path = export_master_excel(intake, tmp_path, dataset=dataset)

    assert xlsx_path.exists()
    assert xlsx_path.suffix == ".xlsx"
    assert xlsx_path.stat().st_size > 0


def test_dataset_basename_strips_trailing_period(pages, config):
    dataset = build_dataset(pages, target_url="https://example.test/", config=config)
    dataset.hospital.name = "Fortis Healthcare Ltd."
    assert dataset_basename(dataset) == "Fortis Healthcare Ltd"


def test_dataset_basename_keeps_interior_periods(pages, config):
    dataset = build_dataset(pages, target_url="https://example.test/", config=config)
    dataset.hospital.name = "St. Mary's Multispeciality"
    assert dataset_basename(dataset) == "St. Mary s Multispeciality"
