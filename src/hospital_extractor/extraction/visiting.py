"""Visiting / admission information extraction.

Visiting hours, OPD hours, and appointment/registration/admission/discharge
guidance are extracted from FAQ answers and labelled sections. Values are kept
as published free text; nothing is paraphrased or inferred.
"""

from __future__ import annotations

from hospital_extractor.extraction.helpers import clean, provenance
from hospital_extractor.extraction.page_analyzer import AnalyzedPage
from hospital_extractor.models.visiting import VisitingInformation

__all__ = ["extract_visiting"]

_FIELD_ROUTES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("visiting hours", "visiting time", "visitor"), "visiting_hours"),
    (("opd", "outpatient", "doctor timings", "consultation hours"), "opd_hours"),
    (("appointment", "book a", "book an", "consultation booking"), "appointment_information"),
    (("registration", "register"), "registration_information"),
    (("admission", "admit", "in-patient"), "admission_information"),
    (("discharge",), "discharge_information"),
)


def extract_visiting(page: AnalyzedPage) -> VisitingInformation | None:
    data: dict[str, str] = {}
    jsonld_used = _from_jsonld(page, data)
    _from_faq(page, data)
    _from_sections(page, data)
    if not any(value is not None for value in data.values()):
        return None
    return VisitingInformation(
        **data,
        **provenance(page.url, title=page.title, method="jsonld" if jsonld_used else "faq",
                      strength="high" if jsonld_used else "medium"),
    )


def _from_jsonld(page: AnalyzedPage, data: dict[str, str]) -> bool:
    used = False
    for block in page.jsonld.blocks:
        opening = block.data.get("openingHoursSpecification")
        if not opening:
            continue
        hours = _opening_hours(opening)
        if hours and "opd_hours" not in data:
            data["opd_hours"] = hours
            used = True
    return used


def _opening_hours(opening: object) -> str | None:
    if isinstance(opening, dict):
        opening = [opening]
    if not isinstance(opening, list):
        return None
    parts: list[str] = []
    for spec in opening:
        if not isinstance(spec, dict):
            continue
        days = spec.get("dayOfWeek")
        if isinstance(days, list):
            days = ", ".join(str(day) for day in days if day)
        elif isinstance(days, str):
            days = days
        else:
            days = None
        opens = spec.get("opens")
        closes = spec.get("closes")
        if days and opens and closes:
            parts.append(f"{days}: {opens} - {closes}")
    return clean("; ".join(parts)) or None


def _from_faq(page: AnalyzedPage, data: dict[str, str]) -> None:
    for item in page.faq_items:
        field = _route(item.question)
        if field and field not in data:
            data[field] = item.answer


def _from_sections(page: AnalyzedPage, data: dict[str, str]) -> None:
    for section in page.sections:
        field = _route(section.heading)
        if field and field not in data:
            data[field] = section.text


def _route(question: str) -> str | None:
    lowered = question.lower()
    for keywords, field in _FIELD_ROUTES:
        if any(keyword in lowered for keyword in keywords):
            return field
    return None
