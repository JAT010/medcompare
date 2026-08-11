"""Final 25-field intake projection.

Takes an assembled :class:`HospitalDataset` (plus, optionally, the analyzed
pages) and produces the single :class:`MasterIntake` row for one hospital.

Identity, location and numeric fields are copied 1:1 from the merged records;
boolean fields are decided from evidence that never gets lost:

- **record evidence**: extracted entities (accreditations, specialities,
  procedures, ...) whose names match a feature prove the feature exists.
- **page evidence**: when ``pages`` is supplied, the boolean detector corpus
  is run over every page's visible text.

All evidence accumulates on the intake so a yes/no can always be explained.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from hospital_extractor.extraction.page_analyzer import AnalyzedPage
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.intake import IntakeEvidence, MasterIntake
from hospital_extractor.schema.boolean_resolver import apply_detectors, resolve
from hospital_extractor.schema.canonical import category_for
from hospital_extractor.schema.discovery import category_records

__all__ = ["project_intake"]

_NABH_RE = re.compile(r"\bnabh\b", re.I)

# Boolean feature -> categories + regex on the entity name/category text that
# prove the feature exists. Kept in sync with schema/intake.py boolean fields.
_BOOLEAN_RECORD_RULES: tuple[tuple[str, tuple[str, ...], re.Pattern[str]], ...] = (
    ("jci_accredited", ("accreditations",), re.compile(r"\bjci\b|joint commission", re.I)),
    ("icu", ("facilities",), re.compile(r"\bicu\b|intensive care\s+unit", re.I)),
    (
        "da_vinci_robotic_surgery",
        ("technologies", "facilities"),
        re.compile(r"da\s+vinci|robotic surgery|robot", re.I),
    ),
    (
        "advanced_laparoscopy_mis",
        ("technologies", "procedures"),
        re.compile(r"laparoscop|minimal[-\s]?invasive|mis\b", re.I),
    ),
    (
        "mri_3_tesla_or_higher",
        ("technologies", "facilities"),
        re.compile(r"3[-\s]?tesla|three[-\s]?tesla", re.I),
    ),
    (
        "cardiac_sciences_enabled",
        ("specialities", "departments", "procedures", "treatments"),
        re.compile(r"cardiac|cardio|heart", re.I),
    ),
    (
        "bypass_surgery_cabg",
        ("procedures", "treatments"),
        re.compile(r"bypass|\bcabg\b|coronary artery", re.I),
    ),
    (
        "angioplasty_stenting",
        ("procedures", "treatments"),
        re.compile(r"angioplasty|stent", re.I),
    ),
    (
        "heart_valve_replacement",
        ("procedures", "treatments"),
        re.compile(r"valve replacement|\bavr\b|\bmvr\b", re.I),
    ),
    (
        "heart_valve_repair",
        ("procedures", "treatments"),
        re.compile(r"valve repair", re.I),
    ),
    (
        "pacemaker_icd_implantation",
        ("procedures", "treatments"),
        re.compile(r"pacemaker|\bicd\b|defibrillator", re.I),
    ),
    (
        "heart_transplant",
        ("procedures", "treatments"),
        re.compile(r"heart transplant", re.I),
    ),
    (
        "orthopaedics_joint_replacement_enabled",
        ("specialities", "departments"),
        re.compile(r"orthop|joint replacement|arthroplasty", re.I),
    ),
    (
        "total_knee_replacement",
        ("procedures", "treatments"),
        re.compile(r"total\s+knee|\btkr\b|knee replacement", re.I),
    ),
    (
        "total_hip_replacement",
        ("procedures", "treatments"),
        re.compile(r"total\s+hip|\bthr\b|hip replacement", re.I),
    ),
)


def project_intake(
    dataset: HospitalDataset,
    pages: Sequence[AnalyzedPage] = (),
) -> MasterIntake:
    """Project the final 25-field intake row from an assembled dataset.

    ``pages`` is optional: when given, the boolean corpus is also run over each
    page's visible text, adding page-level evidence to the record-level
    evidence.
    """
    evidence: dict[str, list[IntakeEvidence]] = {}
    _collect_record_evidence(dataset, evidence)
    _collect_page_evidence(pages, evidence)

    intake, _ = resolve({}, evidence)

    overview = dataset.hospital
    intake.hospital_name = overview.name if overview is not None else None
    intake.established_year = overview.established_year if overview is not None else None
    intake.logo_image_url = overview.logo_url if overview is not None else None

    contact = _first_record(dataset, "contacts")
    if contact is not None:
        intake.pin_code = _postal_code(contact)
        intake.city = _get_str(contact, "city")
        intake.area_locality = _get_str(contact, "locality")

    intake.total_beds = _first_value(dataset, "infrastructure", "total_beds")
    if intake.total_beds is None and overview is not None:
        intake.total_beds = overview.bed_count
    intake.icu_beds = _first_value(dataset, "infrastructure", "icu_beds")
    intake.doctor_count = _doctor_count(dataset, overview)
    intake.nabh_status = _nabh_status(dataset)
    return intake


# --- record-level evidence ----------------------------------------------


def _collect_record_evidence(
    dataset: HospitalDataset, evidence: dict[str, list[IntakeEvidence]]
) -> None:
    for key, categories, pattern in _BOOLEAN_RECORD_RULES:
        for record in _records_in(dataset, categories):
            text = " ".join(
                part
                for part in (
                    _get_str(record, "name"),
                    _get_str(record, "category"),
                    _get_str(record, "description"),
                )
                if part
            )
            if not pattern.search(text):
                continue
            source = _get_str(record, "source_url") or ""
            evidence.setdefault(key, []).append(
                IntakeEvidence.yes(
                    key,
                    text=text[:300],
                    source_url=source,
                    method="record",
                    strength=1.0,
                    label=key,
                )
            )

    # Infrastructure bed counts are direct proof of an ICU.
    for record in _records_in(dataset, ("infrastructure",)):
        for field in ("icu_beds", "critical_care_beds"):
            value = _get_int(record, field)
            if value is not None:
                source = _get_str(record, "source_url") or ""
                evidence.setdefault("icu", []).append(
                    IntakeEvidence.yes(
                        "icu",
                        text=f"{field}: {value}",
                        source_url=source,
                        method="record",
                        strength=1.0,
                        label="icu",
                    )
                )
                break


def _records_in(dataset: HospitalDataset, categories: tuple[str, ...]) -> list[object]:
    records: list[object] = []
    for key in categories:
        try:
            records.extend(category_records(dataset, category_for(key)))
        except KeyError:  # pragma: no cover - defensive
            continue
    return records


# --- page-level evidence -------------------------------------------------


def _collect_page_evidence(
    pages: Sequence[AnalyzedPage], evidence: dict[str, list[IntakeEvidence]]
) -> None:
    for page in pages:
        for item in apply_detectors(page.visible_text, page.url):
            evidence.setdefault(item.field_key, []).append(item)


# --- identity fields -----------------------------------------------------


def _first_record(dataset: HospitalDataset, key: str) -> object | None:
    try:
        records = category_records(dataset, category_for(key))
    except KeyError:  # pragma: no cover - defensive
        return None
    return records[0] if records else None


def _first_value(dataset: HospitalDataset, key: str, attr: str) -> int | None:
    for record in _records_in(dataset, (key,)):
        value = _get_int(record, attr)
        if value is not None:
            return value
    return None


def _postal_code(record: object) -> str | None:
    value = _get_str(record, "postal_code")
    if value is None:
        raw = getattr(record, "postal_code", None)
        value = str(raw) if raw is not None else None
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return digits or clean_str(value)


def _doctor_count(dataset: HospitalDataset, overview: object | None) -> int | None:
    published = _get_int(overview, "doctor_count")
    if published is not None:
        return published
    names = {
        name for record in _records_in(dataset, ("doctors",)) if (name := _get_str(record, "name"))
    }
    return len(names) if names else None


def _nabh_status(dataset: HospitalDataset) -> str | None:
    for record in _records_in(dataset, ("accreditations",)):
        text = " ".join(
            part for part in (_get_str(record, "name"), _get_str(record, "organization")) if part
        )
        if _NABH_RE.search(text):
            return "accredited"
    return None


def _get_str(record: object, attr: str) -> str | None:
    value = getattr(record, attr, None)
    return value if isinstance(value, str) and value else None


def _get_int(record: object, attr: str) -> int | None:
    value = getattr(record, attr, None)
    return value if isinstance(value, int) and value > 0 else None


def clean_str(value: str) -> str | None:
    cleaned = " ".join(value.split())
    return cleaned or None
