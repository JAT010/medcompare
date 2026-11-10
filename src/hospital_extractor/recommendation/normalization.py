"""Attribute normalization from extracted hospital records to criterion scores.

Every observer returns a score only when usable evidence exists. Missing data
is reported as ``usable=False`` with zero coverage and is never converted into
a negative finding.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from hospital_extractor.models.base import EntityRecord
from hospital_extractor.models.contact import Contact
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.doctor import Doctor
from hospital_extractor.models.infrastructure import Infrastructure
from hospital_extractor.recommendation.criteria import CRITERIA_BY_KEY, CriterionKey
from hospital_extractor.recommendation.geo import haversine_km
from hospital_extractor.recommendation.profile import ScoringProfile
from hospital_extractor.recommendation.schemas import (
    CriterionEvidenceState,
    CriterionScore,
    EvidenceRef,
    HospitalCandidate,
    PreferenceRequest,
)
from hospital_extractor.recommendation.text import (
    dedupe_terms,
    first_matching_record,
    record_matches,
)

_DEPARTMENT_FIELDS = ("name", "category", "description")
_DOCTOR_FIELDS = ("department", "speciality", "designation")
_TREATMENT_FIELDS = ("name", "speciality", "description")
_FACILITY_FIELDS = ("name", "category", "description")
_ACCESSIBILITY_TERMS = (
    "wheelchair",
    "ramp",
    "accessible",
    "accessibility",
    "barrier free",
    "lift",
    "elevator",
    "disabled",
    "braille",
)
_EMERGENCY_TERMS = ("emergency", "casualty", "trauma")
_YEARS_RE = re.compile(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.IGNORECASE)


def first_contact(dataset: HospitalDataset) -> Contact | None:
    return dataset.contacts[0] if dataset.contacts else None


def first_infrastructure(dataset: HospitalDataset) -> Infrastructure | None:
    return dataset.infrastructure[0] if dataset.infrastructure else None


def associated_doctors(dataset: HospitalDataset) -> list[Doctor]:
    return [doctor for doctor in dataset.doctors if doctor.hospital_association != "not_associated"]


def category_status(dataset: HospitalDataset, *labels: str) -> str | None:
    for label in labels:
        status = dataset.quality.category_coverage.get(label)
        if status is not None:
            return status
    return None


def absence_supported(dataset: HospitalDataset, *labels: str) -> bool:
    """True when extraction coverage proves any listed category was found."""
    return any(category_status(dataset, label) == "found" for label in labels)


def parse_experience_years(value: str | None) -> int | None:
    if not value:
        return None
    match = _YEARS_RE.search(value)
    if match is None:
        return None
    years = int(match.group(1))
    return years if years > 0 else None


def has_emergency_evidence(dataset: HospitalDataset) -> bool:
    contact = first_contact(dataset)
    if contact is not None and (contact.emergency_phone or contact.emergency_email):
        return True
    for facility in dataset.facilities:
        for term in _EMERGENCY_TERMS:
            if record_matches(facility, term, _FACILITY_FIELDS):
                return True
    return False


def evidence_from_record(
    record: EntityRecord,
    entity: str,
    field: str | None = None,
    value: str | None = None,
) -> EvidenceRef:
    return EvidenceRef(
        entity=entity,
        source_url=record.source_url,
        field=field,
        value=value,
        extraction_method=record.extraction_method,
        source_strength=record.source_strength,
        confidence=record.confidence,
        context=record.context,
    )


def _score(
    key: CriterionKey,
    *,
    score: float | None,
    coverage: float,
    evidence: Sequence[EvidenceRef],
    explanation: str,
) -> CriterionScore:
    spec = CRITERIA_BY_KEY[key]
    bounded_score = None if score is None else round(min(max(score, 0.0), 1.0), 6)
    bounded_coverage = round(min(max(coverage, 0.0), 1.0), 6)
    return CriterionScore(
        criterion=key,
        label=spec.label,
        support=spec.support,
        preference_state=spec.support,
        evidence_state=_evidence_state(bounded_score, bounded_coverage),
        score=bounded_score,
        usable=bounded_score is not None,
        evidence_coverage=bounded_coverage,
        evidence=list(evidence),
        explanation=explanation,
    )


def _evidence_state(
    score: float | None,
    coverage: float,
) -> CriterionEvidenceState:
    if score is None:
        return "unknown"
    if coverage >= 1.0:
        return "verified_available" if score > 0.0 else "verified_unavailable"
    return "partial"


def _unknown(key: CriterionKey, explanation: str) -> CriterionScore:
    return _score(key, score=None, coverage=0.0, evidence=(), explanation=explanation)


def _department_terms(request: PreferenceRequest) -> list[str]:
    selection = request.departments
    if selection is None:
        return []
    return dedupe_terms([*selection.departments, *selection.specialities])


def _treatment_terms(request: PreferenceRequest) -> list[str]:
    selection = request.departments
    if selection is None:
        return []
    return dedupe_terms(selection.treatments)


def _observe_location(
    candidate: HospitalCandidate,
    request: PreferenceRequest,
    profile: ScoringProfile,
) -> CriterionScore:
    requirement = request.location
    if requirement is None:
        return _unknown("location", "No location requirement was supplied.")

    contact = first_contact(candidate.dataset)
    if contact is None:
        return _unknown("location", "No contact/location record was extracted for this hospital.")

    if requirement.radius_km is not None:
        origin_latitude = requirement.latitude
        origin_longitude = requirement.longitude
        if (
            origin_latitude is None
            or origin_longitude is None
            or contact.latitude is None
            or contact.longitude is None
        ):
            return _unknown(
                "location",
                "The hospital has no extracted coordinates, so the radius requirement is unknown.",
            )
        distance = haversine_km(
            origin_latitude,
            origin_longitude,
            contact.latitude,
            contact.longitude,
        )
        score = 1.0 if distance <= requirement.radius_km else 0.0
        radius_evidence = [
            evidence_from_record(
                contact,
                "Location & Contact",
                "latitude/longitude",
                f"{contact.latitude},{contact.longitude}",
            )
        ]
        explanation = (
            f"Great-circle distance {distance:.1f} km against a "
            f"{requirement.radius_km:.1f} km radius."
        )
        return _score(
            "location",
            score=score,
            coverage=1.0,
            evidence=radius_evidence,
            explanation=explanation,
        )

    requested: list[tuple[str, str, str | None]] = []
    if requirement.city:
        requested.append(("city", requirement.city, contact.city))
    if requirement.state:
        requested.append(("state", requirement.state, contact.state))
    if requirement.postal_code:
        requested.append(("postal_code", requirement.postal_code, contact.postal_code))

    if not requested:
        return _unknown("location", "The location requirement has no comparable text fields.")

    matched = 0
    compared = 0
    evidence: list[EvidenceRef] = []
    for field, expected, actual in requested:
        if not actual:
            continue
        compared += 1
        is_match = " ".join(actual.casefold().split()) == " ".join(expected.casefold().split())
        if is_match:
            matched += 1
        evidence.append(
            evidence_from_record(
                contact,
                "Location & Contact",
                field,
                f"candidate={actual!r}; requested={expected!r}; match={is_match}",
            )
        )

    if compared == 0:
        return _unknown(
            "location",
            "The hospital has no comparable city/state/PIN value, so location is unknown.",
        )

    score = matched / compared
    coverage = compared / len(requested)
    explanation = (
        f"Matched {matched} of {compared} comparable location fields "
        f"({compared} of {len(requested)} requested fields were available)."
    )
    return _score(
        "location",
        score=score,
        coverage=coverage,
        evidence=evidence,
        explanation=explanation,
    )


def _observe_infrastructure(
    candidate: HospitalCandidate,
    request: PreferenceRequest,
    profile: ScoringProfile,
) -> CriterionScore:
    infrastructure = first_infrastructure(candidate.dataset)
    if infrastructure is None:
        return _unknown("infrastructure", "No capacity/infrastructure record was extracted.")

    metrics: tuple[tuple[str, int | None, float], ...] = (
        ("total_beds", infrastructure.total_beds, profile.infrastructure_beds_reference),
        ("icu_beds", infrastructure.icu_beds, profile.infrastructure_icu_reference),
        (
            "operation_theatres",
            infrastructure.operation_theatres,
            profile.infrastructure_ot_reference,
        ),
        ("cath_labs", infrastructure.cath_labs, profile.infrastructure_cath_reference),
    )
    available = [
        (field, value, reference)
        for field, value, reference in metrics
        if value is not None and value > 0
    ]
    if not available:
        return _unknown("infrastructure", "No published capacity count was extracted.")

    score = sum(min(value / reference, 1.0) for _, value, reference in available) / len(available)
    coverage = len(available) / len(metrics)
    evidence = [
        evidence_from_record(infrastructure, "Capacity & Infrastructure", field, str(value))
        for field, value, _ in available
    ]
    explanation = (
        f"Normalized {len(available)} of {len(metrics)} published capacity metrics against "
        "documented saturation scales."
    )
    return _score(
        "infrastructure",
        score=score,
        coverage=coverage,
        evidence=evidence,
        explanation=explanation,
    )


def _observe_doctor_experience(
    candidate: HospitalCandidate,
    request: PreferenceRequest,
    profile: ScoringProfile,
) -> CriterionScore:
    doctors = associated_doctors(candidate.dataset)
    if not doctors:
        return _unknown("doctor_experience", "No associated doctor records were extracted.")

    parsed: list[tuple[Doctor, int]] = []
    for doctor in doctors:
        years = parse_experience_years(doctor.experience)
        if years is not None:
            parsed.append((doctor, years))
    if not parsed:
        return _unknown(
            "doctor_experience",
            "No doctor record contains an explicitly published years-of-experience value.",
        )

    maximum = max(years for _, years in parsed)
    score = min(maximum / profile.experience_reference_years, 1.0)
    coverage = len(parsed) / len(doctors)
    evidence = [
        evidence_from_record(doctor, "Doctors", "experience", doctor.experience)
        for doctor, _ in parsed
    ]
    explanation = (
        f"Highest published experience is {maximum} years across {len(parsed)} of "
        f"{len(doctors)} associated doctors."
    )
    return _score(
        "doctor_experience",
        score=score,
        coverage=coverage,
        evidence=evidence,
        explanation=explanation,
    )


def _observe_department_expertise(
    candidate: HospitalCandidate,
    request: PreferenceRequest,
    profile: ScoringProfile,
) -> CriterionScore:
    requested = _department_terms(request)
    if not requested:
        return _unknown("department_expertise", "No department or speciality was requested.")

    records: list[EntityRecord] = [*candidate.dataset.departments, *candidate.dataset.specialities]
    doctors = associated_doctors(candidate.dataset)
    matched: list[str] = []
    evidence: list[EvidenceRef] = []
    for term in requested:
        record = first_matching_record(records, term, _DEPARTMENT_FIELDS)
        if record is None:
            record = first_matching_record(doctors, term, _DOCTOR_FIELDS)
        if record is not None:
            matched.append(term)
            evidence.append(
                evidence_from_record(record, "Departments / Specialities", "name", term)
            )

    supported_absence = absence_supported(candidate.dataset, "Departments", "Specialities")
    if not matched:
        if supported_absence:
            return _score(
                "department_expertise",
                score=0.0,
                coverage=1.0,
                evidence=(),
                explanation="The hospital published departments/specialities but none matched.",
            )
        return _unknown(
            "department_expertise",
            "No matching department/speciality was extracted and coverage does not prove absence.",
        )

    score = len(matched) / len(requested)
    coverage = 1.0 if supported_absence else len(matched) / len(requested)
    explanation = (
        f"Matched {len(matched)} of {len(requested)} requested department/speciality terms."
    )
    return _score(
        "department_expertise",
        score=score,
        coverage=coverage,
        evidence=evidence,
        explanation=explanation,
    )


def _observe_emergency_services(
    candidate: HospitalCandidate,
    request: PreferenceRequest,
    profile: ScoringProfile,
) -> CriterionScore:
    dataset = candidate.dataset
    evidence: list[EvidenceRef] = []
    contact = first_contact(dataset)
    if contact is not None:
        if contact.emergency_phone:
            evidence.append(
                evidence_from_record(
                    contact, "Location & Contact", "emergency_phone", contact.emergency_phone
                )
            )
        if contact.emergency_email:
            evidence.append(
                evidence_from_record(
                    contact, "Location & Contact", "emergency_email", contact.emergency_email
                )
            )
    for facility in dataset.facilities:
        for term in _EMERGENCY_TERMS:
            if record_matches(facility, term, _FACILITY_FIELDS):
                evidence.append(evidence_from_record(facility, "Facilities", "name", facility.name))
                break

    if evidence:
        return _score(
            "emergency_services",
            score=1.0,
            coverage=1.0,
            evidence=evidence,
            explanation="Explicit emergency contact or emergency facility evidence was extracted.",
        )
    return _unknown(
        "emergency_services",
        "No emergency evidence was extracted; absence is not treated as a negative finding.",
    )


def _observe_doctor_availability(
    candidate: HospitalCandidate,
    request: PreferenceRequest,
    profile: ScoringProfile,
) -> CriterionScore:
    doctors = associated_doctors(candidate.dataset)
    if not doctors:
        return _unknown("doctor_availability", "No associated doctor records were extracted.")

    with_hours = [doctor for doctor in doctors if doctor.opd_hours]
    if not with_hours:
        return _unknown(
            "doctor_availability",
            "No doctor record contains published OPD hours; absence is not a negative finding.",
        )

    coverage = len(with_hours) / len(doctors)
    evidence = [
        evidence_from_record(doctor, "Doctors", "opd_hours", doctor.opd_hours)
        for doctor in with_hours
    ]
    explanation = (
        f"Published OPD hours exist for {len(with_hours)} of {len(doctors)} associated doctors."
    )
    return _score(
        "doctor_availability",
        score=1.0,
        coverage=coverage,
        evidence=evidence,
        explanation=explanation,
    )


def _observe_diagnostic_facilities(
    candidate: HospitalCandidate,
    request: PreferenceRequest,
    profile: ScoringProfile,
) -> CriterionScore:
    requested = dedupe_terms(request.diagnostics)
    if not requested:
        return _unknown("diagnostic_facilities", "No diagnostic service was requested.")

    records: list[EntityRecord] = [
        *candidate.dataset.facilities,
        *candidate.dataset.technologies,
        *candidate.dataset.procedures,
    ]
    matched: list[str] = []
    evidence: list[EvidenceRef] = []
    for term in requested:
        record = first_matching_record(records, term, _FACILITY_FIELDS)
        if record is not None:
            matched.append(term)
            evidence.append(
                evidence_from_record(record, "Facilities / Medical Technology", "name", term)
            )

    supported_absence = absence_supported(
        candidate.dataset, "Facilities", "Medical Technology", "Procedures"
    )
    if not matched:
        if supported_absence:
            return _score(
                "diagnostic_facilities",
                score=0.0,
                coverage=1.0,
                evidence=(),
                explanation="The hospital published facilities/technology but none matched.",
            )
        return _unknown(
            "diagnostic_facilities",
            "No matching diagnostic evidence was extracted and coverage does not prove absence.",
        )

    score = len(matched) / len(requested)
    coverage = 1.0 if supported_absence else len(matched) / len(requested)
    explanation = f"Matched {len(matched)} of {len(requested)} requested diagnostic services."
    return _score(
        "diagnostic_facilities",
        score=score,
        coverage=coverage,
        evidence=evidence,
        explanation=explanation,
    )


def _observe_accessibility(
    candidate: HospitalCandidate,
    request: PreferenceRequest,
    profile: ScoringProfile,
) -> CriterionScore:
    dataset = candidate.dataset
    records: list[EntityRecord] = [*dataset.facilities, *dataset.technologies]
    if dataset.hospital is not None:
        records.append(dataset.hospital)

    matched: list[str] = []
    evidence: list[EvidenceRef] = []
    for term in _ACCESSIBILITY_TERMS:
        record = first_matching_record(records, term, _FACILITY_FIELDS)
        if record is not None:
            matched.append(term)
            evidence.append(evidence_from_record(record, "Facilities", "name", term))

    if not matched:
        return _unknown(
            "accessibility",
            "No accessibility evidence was extracted; absence is not treated as a "
            "negative finding.",
        )

    explanation = f"Explicit accessibility evidence found for: {', '.join(matched)}."
    return _score(
        "accessibility",
        score=1.0,
        coverage=1.0,
        evidence=evidence,
        explanation=explanation,
    )


def _observe_treatment_facilities(
    candidate: HospitalCandidate,
    request: PreferenceRequest,
    profile: ScoringProfile,
) -> CriterionScore:
    requested = _treatment_terms(request)
    if not requested:
        return _unknown("treatment_facilities", "No treatment was requested.")

    dataset = candidate.dataset
    records: list[EntityRecord] = [
        *dataset.treatments,
        *dataset.procedures,
        *dataset.technologies,
        *dataset.specialities,
        *dataset.departments,
    ]
    matched: list[str] = []
    evidence: list[EvidenceRef] = []
    for term in requested:
        record = first_matching_record(records, term, _TREATMENT_FIELDS)
        if record is not None:
            matched.append(term)
            evidence.append(
                evidence_from_record(record, "Treatments / Procedures", "name", term)
            )

    supported_absence = absence_supported(
        dataset,
        "Treatments / Conditions",
        "Procedures",
        "Departments",
        "Specialities",
    )
    if not matched:
        if supported_absence:
            return _score(
                "treatment_facilities",
                score=0.0,
                coverage=1.0,
                evidence=(),
                explanation="The hospital published treatment records but none matched.",
            )
        return _unknown(
            "treatment_facilities",
            "No matching treatment evidence was extracted and coverage does not prove absence.",
        )

    score = len(matched) / len(requested)
    coverage = 1.0 if supported_absence else len(matched) / len(requested)
    explanation = f"Matched {len(matched)} of {len(requested)} requested treatments."
    return _score(
        "treatment_facilities",
        score=score,
        coverage=coverage,
        evidence=evidence,
        explanation=explanation,
    )


def _observe_unsupported(key: CriterionKey) -> CriterionScore:
    spec = CRITERIA_BY_KEY[key]
    required = "; ".join(spec.required_extraction) or "no extracted data source exists"
    result = _unknown(
        key,
        f"{spec.label} is not supported by current extracted data. "
        f"Required extraction: {required}.",
    )
    return result.model_copy(update={"evidence_state": "unsupported"})


_Observer = Callable[[HospitalCandidate, PreferenceRequest, ScoringProfile], CriterionScore]

_OBSERVERS: dict[CriterionKey, _Observer] = {
    "location": _observe_location,
    "doctor_experience": _observe_doctor_experience,
    "department_expertise": _observe_department_expertise,
    "infrastructure": _observe_infrastructure,
    "emergency_services": _observe_emergency_services,
    "doctor_availability": _observe_doctor_availability,
    "diagnostic_facilities": _observe_diagnostic_facilities,
    "accessibility": _observe_accessibility,
    "treatment_facilities": _observe_treatment_facilities,
}


def observe_criterion(
    key: CriterionKey,
    candidate: HospitalCandidate,
    request: PreferenceRequest,
    profile: ScoringProfile,
) -> CriterionScore:
    observer = _OBSERVERS.get(key)
    if observer is None:
        return _observe_unsupported(key)
    return observer(candidate, request, profile)


def observe_criteria(
    keys: Sequence[CriterionKey],
    candidate: HospitalCandidate,
    request: PreferenceRequest,
    profile: ScoringProfile,
) -> list[CriterionScore]:
    return [observe_criterion(key, candidate, request, profile) for key in keys]
