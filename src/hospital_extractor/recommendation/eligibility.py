"""Mandatory-constraint evaluation performed before any scoring."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hospital_extractor.models.base import EntityRecord
from hospital_extractor.recommendation.geo import haversine_km
from hospital_extractor.recommendation.normalization import (
    absence_supported,
    associated_doctors,
    first_contact,
    first_infrastructure,
    has_emergency_evidence,
)
from hospital_extractor.recommendation.schemas import (
    HospitalCandidate,
    MandatoryCheck,
    MandatoryStatus,
    PreferenceRequest,
)
from hospital_extractor.recommendation.text import first_matching_record, normalize_text

_DEPARTMENT_FIELDS = ("name", "category", "description", "department", "speciality", "designation")
_TREATMENT_FIELDS = ("name", "speciality", "description")
_ACCREDITATION_FIELDS = ("name", "organization")


@dataclass(frozen=True)
class MandatoryEvaluation:
    checks: tuple[MandatoryCheck, ...]

    @property
    def satisfied(self) -> bool:
        return all(check.status == "satisfied" for check in self.checks)

    @property
    def reasons(self) -> list[str]:
        return [
            f"{check.name}: {check.status} - {check.detail}"
            for check in self.checks
            if check.status != "satisfied"
        ]


def evaluate_mandatory(
    request: PreferenceRequest,
    candidate: HospitalCandidate,
) -> MandatoryEvaluation:
    """Evaluate every mandatory constraint, treating unknown as not satisfied."""
    constraints = request.mandatory
    dataset = candidate.dataset
    contact = first_contact(dataset)
    infrastructure = first_infrastructure(dataset)
    checks: list[MandatoryCheck] = []

    for name, expected, actual in (
        ("required_city", constraints.required_city, contact.city if contact else None),
        ("required_state", constraints.required_state, contact.state if contact else None),
        (
            "required_postal_code",
            constraints.required_postal_code,
            contact.postal_code if contact else None,
        ),
    ):
        if expected:
            status, detail = _text_status(actual, expected)
            checks.append(MandatoryCheck(name=name, status=status, detail=detail))

    if constraints.max_distance_km is not None:
        checks.append(_distance_check(request, candidate, constraints.max_distance_km))

    if constraints.required_departments:
        records: list[EntityRecord] = [
            *dataset.departments,
            *dataset.specialities,
            *associated_doctors(dataset),
        ]
        check = _term_check(
            "required_departments",
            constraints.required_departments,
            records,
            _DEPARTMENT_FIELDS,
            absence_supported(dataset, "Departments", "Specialities"),
        )
        if check is not None:
            checks.append(check)

    if constraints.required_specialities:
        records = [*dataset.specialities, *dataset.departments, *associated_doctors(dataset)]
        check = _term_check(
            "required_specialities",
            constraints.required_specialities,
            records,
            _DEPARTMENT_FIELDS,
            absence_supported(dataset, "Specialities", "Departments"),
        )
        if check is not None:
            checks.append(check)

    if constraints.required_treatments:
        records = [*dataset.treatments, *dataset.procedures]
        check = _term_check(
            "required_treatments",
            constraints.required_treatments,
            records,
            _TREATMENT_FIELDS,
            absence_supported(dataset, "Treatments / Conditions", "Procedures"),
        )
        if check is not None:
            checks.append(check)

    if constraints.minimum_total_beds is not None:
        checks.append(
            _minimum_check(
                "minimum_total_beds",
                constraints.minimum_total_beds,
                infrastructure.total_beds if infrastructure else None,
            )
        )

    if constraints.minimum_icu_beds is not None:
        checks.append(
            _minimum_check(
                "minimum_icu_beds",
                constraints.minimum_icu_beds,
                infrastructure.icu_beds if infrastructure else None,
            )
        )

    if constraints.require_emergency_services:
        if has_emergency_evidence(dataset):
            checks.append(
                MandatoryCheck(
                    name="require_emergency_services",
                    status="satisfied",
                    detail="Explicit emergency evidence was extracted.",
                )
            )
        else:
            checks.append(
                MandatoryCheck(
                    name="require_emergency_services",
                    status="unknown",
                    detail="No emergency evidence was extracted; unknown is not satisfied.",
                )
            )

    if constraints.required_accreditations:
        check = _term_check(
            "required_accreditations",
            constraints.required_accreditations,
            list(dataset.accreditations),
            _ACCREDITATION_FIELDS,
            absence_supported(dataset, "Accreditations"),
        )
        if check is not None:
            checks.append(check)

    if constraints.required_insurance_providers:
        checks.append(
            MandatoryCheck(
                name="required_insurance_providers",
                status="unknown",
                detail=(
                    "Insurance/TPA network data is not extracted, so this requirement "
                    "cannot be verified."
                ),
            )
        )

    if constraints.max_affordability_cost is not None:
        checks.append(
            MandatoryCheck(
                name="max_affordability_cost",
                status="unknown",
                detail=(
                    "Pricing/tariff data is not extracted, so this affordability "
                    "requirement cannot be verified."
                ),
            )
        )

    return MandatoryEvaluation(checks=tuple(checks))


def _text_status(actual: str | None, expected: str) -> tuple[MandatoryStatus, str]:
    if not actual:
        return "unknown", f"Candidate has no extracted value; requested {expected!r}."
    if normalize_text(actual) == normalize_text(expected):
        return "satisfied", f"Matched requested value {expected!r}."
    return "violated", f"Candidate value {actual!r} does not match {expected!r}."


def _distance_check(
    request: PreferenceRequest,
    candidate: HospitalCandidate,
    maximum_km: float,
) -> MandatoryCheck:
    location = request.location
    if location is None or location.latitude is None or location.longitude is None:
        return MandatoryCheck(
            name="max_distance_km",
            status="unknown",
            detail="No origin coordinate was supplied.",
        )
    contact = first_contact(candidate.dataset)
    if contact is None or contact.latitude is None or contact.longitude is None:
        return MandatoryCheck(
            name="max_distance_km",
            status="unknown",
            detail="Candidate has no extracted coordinates.",
        )
    distance = haversine_km(
        location.latitude,
        location.longitude,
        contact.latitude,
        contact.longitude,
    )
    status: MandatoryStatus = "satisfied" if distance <= maximum_km else "violated"
    return MandatoryCheck(
        name="max_distance_km",
        status=status,
        detail=f"Great-circle distance {distance:.1f} km against {maximum_km:.1f} km.",
    )


def _minimum_check(name: str, minimum: int, actual: int | None) -> MandatoryCheck:
    if actual is None:
        return MandatoryCheck(
            name=name,
            status="unknown",
            detail=f"No extracted value; minimum is {minimum}.",
        )
    if actual < minimum:
        return MandatoryCheck(
            name=name,
            status="violated",
            detail=f"Extracted value {actual} is below the minimum {minimum}.",
        )
    return MandatoryCheck(
        name=name,
        status="satisfied",
        detail=f"Extracted value {actual} meets the minimum {minimum}.",
    )


def _term_check(
    name: str,
    terms: Sequence[str],
    records: Sequence[EntityRecord],
    fields: Sequence[str],
    supported_absence: bool,
) -> MandatoryCheck | None:
    if not terms:
        return None
    statuses: list[tuple[str, MandatoryStatus]] = []
    for term in terms:
        if first_matching_record(records, term, fields) is not None:
            statuses.append((term, "satisfied"))
        elif supported_absence:
            statuses.append((term, "violated"))
        else:
            statuses.append((term, "unknown"))

    if any(status == "violated" for _, status in statuses):
        overall: MandatoryStatus = "violated"
    elif any(status == "unknown" for _, status in statuses):
        overall = "unknown"
    else:
        overall = "satisfied"

    detail = "; ".join(f"{term}: {status}" for term, status in statuses)
    return MandatoryCheck(name=name, status=overall, detail=detail)
