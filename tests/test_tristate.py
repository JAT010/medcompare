"""Tri-state intake integrity tests.

These tests lock the distinction between a resolved ``no`` and an unresolved
``unknown``. The default submission policy preserves UNKNOWN, while the explicit
legacy FALSE policy must not mutate the projection's tri-state value.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from openpyxl import load_workbook

from hospital_extractor.cli import build_parser
from hospital_extractor.config import (
    UNKNOWN_FALSE,
    UNKNOWN_KEEP,
    UNKNOWN_POLICY_DEFAULT,
    ExtractionConfig,
    load_config,
)
from hospital_extractor.export import export_master_csv, export_master_excel
from hospital_extractor.export.master_csv import submit_boolean
from hospital_extractor.export.master_excel import MASTER_SHEET
from hospital_extractor.models.intake import IntakeEvidence, MasterIntake
from hospital_extractor.schema.boolean_resolver import resolve
from hospital_extractor.schema.intake import boolean_keys, intake_labels


def _csv_row(path: Path) -> list[str]:
    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    return rows[1]


def _xlsx_value(path: Path, label: str) -> object:
    workbook = load_workbook(path, read_only=True)
    try:
        sheet = workbook[MASTER_SHEET]
        for row in sheet.iter_rows(min_row=1, values_only=True):
            if row[1] == label:
                return row[2]
        raise AssertionError(f"{label!r} not found in {MASTER_SHEET!r}")
    finally:
        workbook.close()


def test_missing_boolean_state_is_unknown() -> None:
    intake = MasterIntake()

    assert intake.boolean_state("icu") == "unknown"
    assert set(intake.unknown_boolean_keys()) == boolean_keys()


def test_explicit_false_is_not_unknown() -> None:
    intake = MasterIntake(booleans={"icu": "no"})

    assert intake.boolean_state("icu") == "no"
    assert "icu" not in intake.unknown_boolean_keys()
    assert intake.to_dict()["icu"] == "no"


def test_explicit_yes_is_preserved() -> None:
    intake = MasterIntake(booleans={"icu": "yes"})

    assert intake.boolean_state("icu") == "yes"
    assert "icu" not in intake.unknown_boolean_keys()
    assert intake.to_dict()["icu"] == "yes"


def test_unknown_boolean_keys_only_lists_unresolved_fields() -> None:
    intake = MasterIntake(booleans={"icu": "yes", "jci_accredited": "no"})
    unknown = set(intake.unknown_boolean_keys())

    assert "icu" not in unknown
    assert "jci_accredited" not in unknown
    assert "mri_3_tesla_or_higher" in unknown


def test_resolver_never_converts_missing_evidence_to_no() -> None:
    intake, _ = resolve({}, {})

    assert set(intake.unknown_boolean_keys()) == boolean_keys()


def test_resolver_keeps_balanced_evidence_unknown() -> None:
    evidence = {
        "icu": [
            IntakeEvidence.yes(
                "icu",
                "fictional ICU evidence",
                "https://fictional.example/yes",
                "test",
                strength=1.0,
            ),
            IntakeEvidence.no(
                "icu",
                "fictional no-ICU evidence",
                "https://fictional.example/no",
                "test",
                strength=1.0,
            ),
        ]
    }

    intake, _ = resolve({}, evidence)

    assert intake.boolean_state("icu") == "unknown"


def test_unknown_keep_export_preserves_unknown(tmp_path) -> None:
    intake = MasterIntake(hospital_name="Fictional Hospital", booleans={"icu": "unknown"})

    csv_path, quality_path = export_master_csv(
        intake, tmp_path, boolean_unknown_policy=UNKNOWN_KEEP
    )

    row = _csv_row(csv_path)
    assert row[intake_labels().index("ICU")] == "UNKNOWN"
    assert "UNKNOWN" in quality_path.read_text(encoding="utf-8")


def test_legacy_false_policy_converts_unknown_without_mutating_projection(
    tmp_path: Path,
) -> None:
    intake = MasterIntake(hospital_name="Fictional Hospital", booleans={"icu": "unknown"})

    csv_path, quality_path = export_master_csv(
        intake, tmp_path, boolean_unknown_policy=UNKNOWN_FALSE
    )

    row = _csv_row(csv_path)
    assert row[intake_labels().index("ICU")] == "FALSE"
    assert "UNKNOWN converted to FALSE" in quality_path.read_text(encoding="utf-8")
    assert intake.boolean_state("icu") == "unknown"
    assert intake.to_dict()["icu"] == "unknown"


def test_default_export_preserves_unknown(tmp_path: Path) -> None:
    intake = MasterIntake(hospital_name="Fictional Hospital", booleans={"icu": "unknown"})

    csv_path, quality_path = export_master_csv(intake, tmp_path)

    row = _csv_row(csv_path)
    assert row[intake_labels().index("ICU")] == "UNKNOWN"
    assert "boolean_unknown_policy=keep_unknown" in quality_path.read_text(encoding="utf-8")


def test_default_config_preserves_unknown() -> None:
    assert UNKNOWN_POLICY_DEFAULT == UNKNOWN_KEEP
    assert ExtractionConfig().boolean_unknown_policy == UNKNOWN_KEEP
    assert load_config().boolean_unknown_policy == UNKNOWN_KEEP


def test_legacy_policy_can_be_selected_from_config(tmp_path: Path) -> None:
    path = tmp_path / "legacy.toml"
    path.write_text(
        '[extraction]\nboolean_unknown_policy = "false"\n',
        encoding="utf-8",
    )

    assert load_config(path).boolean_unknown_policy == UNKNOWN_FALSE


def test_invalid_unknown_policy_is_rejected() -> None:
    with pytest.raises(ValueError, match="boolean_unknown_policy"):
        ExtractionConfig(boolean_unknown_policy="bogus")
    with pytest.raises(ValueError, match="unknown boolean policy"):
        submit_boolean("unknown", "bogus")


def test_csv_and_xlsx_preserve_unknown_consistently(tmp_path: Path) -> None:
    intake = MasterIntake(hospital_name="Fictional Hospital", booleans={"icu": "unknown"})

    csv_path, _ = export_master_csv(intake, tmp_path)
    xlsx_path = export_master_excel(intake, tmp_path)

    assert _csv_row(csv_path)[intake_labels().index("ICU")] == "UNKNOWN"
    assert _xlsx_value(xlsx_path, "ICU") == "UNKNOWN"


def test_cli_exposes_explicit_legacy_unknown_policy() -> None:
    parser = build_parser()

    default_args = parser.parse_args(["https://fictional.example/"])
    legacy_args = parser.parse_args(
        ["https://fictional.example/", "--boolean-unknown-policy", UNKNOWN_FALSE]
    )

    assert default_args.boolean_unknown_policy is None
    assert legacy_args.boolean_unknown_policy == UNKNOWN_FALSE
