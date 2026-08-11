"""Final master intake CSV export plus its accompanying quality report.

This is the submission-facing output built purely from a completed
:class:`~hospital_extractor.models.intake.MasterIntake` projection:

- ``master.csv`` — exactly the 25 schema fields, in the exact schema order,
  as ONE hospital row. Booleans are serialized as ``TRUE`` / ``FALSE`` (with
  ``UNKNOWN`` handled by the configured ``boolean_unknown_policy``), text stays
  text, numbers stay numbers and the PIN code is written as text so leading
  zeroes survive. Output is deterministic, UTF-8 and properly CSV-escaped.
- ``master_quality.md`` — the provenance/quality trail kept OUT of the 25
  columns: per-field evidence, resolved vs submitted booleans, which UNKNOWN
  values the submission policy converted to FALSE, conflicts and
  low-confidence signals.

Nothing here re-scrapes the website and nothing mutates the intake: the
policy only affects how an already-resolved ``unknown`` is *submitted*.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import TYPE_CHECKING

from hospital_extractor.config import UNKNOWN_FALSE, UNKNOWN_KEEP
from hospital_extractor.models.intake import MasterIntake
from hospital_extractor.schema.intake import INTAKE_FIELDS, intake_field, intake_labels

if TYPE_CHECKING:
    from hospital_extractor.models.dataset import HospitalDataset

__all__ = [
    "MASTER_CSV_NAME",
    "MASTER_QUALITY_NAME",
    "export_master_csv",
    "master_row_values",
    "submit_boolean",
]

MASTER_CSV_NAME = "master.csv"
MASTER_QUALITY_NAME = "master_quality.md"

_NABH_RE = re.compile(r"\bnabh\b", re.I)
_LOW_CONFIDENCE_STRENGTH = 0.5


def submit_boolean(decision: str, policy: str) -> str:
    """Serialize a resolved boolean decision for the submission CSV.

    ``yes`` -> ``TRUE``, ``no`` -> ``FALSE``. ``unknown`` follows *policy*:
    ``keep_unknown`` keeps ``UNKNOWN``; anything else submits ``FALSE``.
    """
    if decision == "yes":
        return "TRUE"
    if decision == "no":
        return "FALSE"
    if policy == UNKNOWN_KEEP:
        return "UNKNOWN"
    return "FALSE"


def export_master_csv(
    intake: MasterIntake,
    output_dir: Path | str,
    *,
    boolean_unknown_policy: str = UNKNOWN_FALSE,
    dataset: HospitalDataset | None = None,
) -> tuple[Path, Path]:
    """Write the 25-column ``master.csv`` and ``master_quality.md``.

    Returns ``(csv_path, quality_path)``. *dataset* is optional and only used
    to enrich the quality report's provenance for non-boolean fields.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / MASTER_CSV_NAME
    quality_path = out / MASTER_QUALITY_NAME

    row = master_row_values(intake, boolean_unknown_policy)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(intake_labels())
        writer.writerow(row)

    quality_path.write_text(
        _quality_report(intake, dataset=dataset, policy=boolean_unknown_policy),
        encoding="utf-8",
    )
    return csv_path, quality_path


def master_row_values(intake: MasterIntake, boolean_unknown_policy: str) -> list[object]:
    """The exact final submission values, one per intake field, in schema order.

    Shared by the Master CSV and Master XLSX exporters so both outputs are
    guaranteed to carry identical final values. Booleans go through
    :func:`submit_boolean` (the configured submission policy), numbers stay
    numbers and missing non-boolean values become empty strings.
    """
    return [_row_cell(intake, field.key, boolean_unknown_policy) for field in INTAKE_FIELDS]


def _row_cell(intake: MasterIntake, key: str, policy: str) -> object:
    field = intake_field(key)
    if field.is_boolean:
        return submit_boolean(intake.booleans.get(key, "unknown"), policy)
    value = getattr(intake, key, None)
    return value if value is not None else ""


# --- quality / provenance report ------------------------------------------


def _quality_report(
    intake: MasterIntake,
    *,
    dataset: HospitalDataset | None,
    policy: str,
) -> str:
    provenance = _value_provenance(dataset) if dataset is not None else {}
    lines: list[str] = []
    lines.append("# Master Hospital Intake — Quality Report")
    lines.append("")
    lines.append(f"Hospital: {intake.hospital_name or '—'}")
    lines.append("")
    lines.append(f"Submission policy: `boolean_unknown_policy={policy}`")
    lines.append("")

    lines.append("## Boolean submission values")
    lines.append("")
    lines.append("| Field | Resolved | Submitted | Notes |")
    lines.append("|-------|----------|-----------|-------|")
    for field in INTAKE_FIELDS:
        if not field.is_boolean:
            continue
        decision = intake.booleans.get(field.key, "unknown")
        submitted = submit_boolean(decision, policy)
        notes = (
            "UNKNOWN converted to FALSE by submission policy"
            if decision == "unknown" and policy == UNKNOWN_FALSE
            else ""
        )
        lines.append(f"| {field.label} | {decision} | {submitted} | {notes} |")

    lines.append("")
    lines.append("## Field evidence")
    lines.append("")
    evidence_seen = False
    for field in INTAKE_FIELDS:
        if not field.is_boolean:
            continue
        items = intake.evidence.get(field.key, [])
        if not items:
            continue
        evidence_seen = True
        lines.append(f"### {field.label}")
        for item in items:
            lines.append(
                f"- {item.decision} — {item.text!r} — source: {item.source_url or '—'} "
                f"— method: {item.method} — strength: {item.strength}"
            )
        lines.append("")
    if not evidence_seen:
        lines.append("No boolean evidence recorded.")
        lines.append("")

    lines.append("## Conflicts")
    lines.append("")
    conflicts = [
        field
        for field in INTAKE_FIELDS
        if field.is_boolean
        and any(item.decision == "yes" for item in intake.evidence.get(field.key, []))
        and any(item.decision == "no" for item in intake.evidence.get(field.key, []))
    ]
    if conflicts:
        for field in conflicts:
            lines.append(f"- {field.label}: both supporting and opposing evidence recorded")
    else:
        lines.append("None.")

    low_confidence = [
        item
        for items in intake.evidence.values()
        for item in items
        if item.strength < _LOW_CONFIDENCE_STRENGTH
    ]
    if low_confidence:
        lines.append("")
        lines.append("## Low-confidence evidence")
        lines.append("")
        for item in low_confidence:
            lines.append(
                f"- {intake_field(item.field_key).label} ({item.decision}) — "
                f"strength {item.strength} — {item.text!r}"
            )

    lines.append("")
    lines.append("## Identity & numeric values")
    lines.append("")
    lines.append("| Field | Value | Source |")
    lines.append("|-------|-------|--------|")
    for field in INTAKE_FIELDS:
        if field.is_boolean:
            continue
        value = getattr(intake, field.key, None)
        source = provenance.get(field.key) or "—"
        lines.append(f"| {field.label} | {value if value is not None else ''} | {source} |")
    return "\n".join(lines) + "\n"


def _value_provenance(dataset: HospitalDataset) -> dict[str, str]:
    """Map each non-boolean field key to the source URL that supplied it."""
    result: dict[str, str] = {}
    overview = dataset.hospital
    if overview is not None:
        source = getattr(overview, "source_url", None) or ""
        for key in ("hospital_name", "established_year", "logo_image_url"):
            result[key] = source
        if getattr(overview, "doctor_count", None) is not None:
            result["doctor_count"] = source

    contacts = dataset.contacts
    if contacts:
        source = getattr(contacts[0], "source_url", None) or ""
        for key in ("pin_code", "city", "area_locality"):
            result.setdefault(key, source)

    infrastructure = dataset.infrastructure
    if infrastructure:
        source = getattr(infrastructure[0], "source_url", None) or ""
        for key in ("total_beds", "icu_beds"):
            result.setdefault(key, source)

    for record in dataset.accreditations:
        name = " ".join(
            part
            for part in (getattr(record, "name", None), getattr(record, "organization", None))
            if part
        )
        if _NABH_RE.search(name):
            result["nabh_status"] = getattr(record, "source_url", None) or ""
            break

    if "doctor_count" not in result and dataset.doctors:
        result["doctor_count"] = getattr(dataset.doctors[0], "source_url", None) or ""
    return result
