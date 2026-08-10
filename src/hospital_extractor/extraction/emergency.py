"""Emergency service extraction.

Emergency/ambulance pages and FAQ answers. A service is only recorded when the
page actually publishes one (name and/or helpline); ``24/7`` availability and
contact numbers are copied as published, never reformatted.
"""

from __future__ import annotations

import re

from hospital_extractor.extraction.helpers import clean, provenance
from hospital_extractor.extraction.page_analyzer import AnalyzedPage
from hospital_extractor.models.emergency import EmergencyService
from hospital_extractor.schema.mapper import route_faq_question

__all__ = ["extract_emergency_services"]

_PHONE_RE = re.compile(r"[+()\d][\d\s().-]{7,}\d")
_AVAILABILITY_RE = re.compile(r"24\s*[x/]\s*7|round the clock", re.I)
_EMERGENCY_HEADING_RE = re.compile(r"emergency|ambulance|urgent|24\s*[x/]\s*7", re.I)


def extract_emergency_services(page: AnalyzedPage) -> list[EmergencyService]:
    records: list[EmergencyService] = []
    records.extend(_from_faq(page))
    records.extend(_from_sections(page))
    page_record = _from_page(page)
    if page_record is not None:
        records.append(page_record)
    return records


def _from_faq(page: AnalyzedPage) -> list[EmergencyService]:
    records: list[EmergencyService] = []
    for item in page.faq_items:
        if route_faq_question(item.question) != "emergency_services":
            continue
        records.append(
            EmergencyService(
                name=item.question,
                availability=_availability(item.answer),
                contact=_phone(item.answer) or _phone(page.visible_text[:2000]),
                description=item.answer,
                **provenance(page.url, title=page.title, method="faq", strength="medium",
                              context=item.answer[:300]),
            )
        )
    return records


def _from_sections(page: AnalyzedPage) -> list[EmergencyService]:
    records: list[EmergencyService] = []
    for section in page.sections:
        if not _EMERGENCY_HEADING_RE.search(section.heading):
            continue
        name = clean(section.heading)
        if not name or _is_page_title_repeat(page, name):
            continue
        records.append(
            EmergencyService(
                name=name,
                availability=_availability(section.text),
                contact=_phone(section.text),
                description=section.text,
                **provenance(page.url, title=page.title, method="section", strength="medium",
                              context=section.text[:300]),
            )
        )
    return records


def _from_page(page: AnalyzedPage) -> EmergencyService | None:
    name = _page_name(page)
    phone = _phone(page.visible_text[:4000])
    availability = _availability(page.visible_text[:4000])
    if not name and not phone:
        return None
    return EmergencyService(
        name=name,
        availability=availability,
        contact=phone,
        **provenance(page.url, title=page.title, method="text", strength="low"),
    )


def _page_name(page: AnalyzedPage) -> str | None:
    for level, value in page.headings:
        if level == "h1" and value and len(value) < 120:
            return value
    for section in page.sections:
        if _EMERGENCY_HEADING_RE.search(section.heading) and section.heading.lower() not in (
            "emergency",
            "24x7 emergency",
        ):
            return clean(section.heading)
    return None


def _is_page_title_repeat(page: AnalyzedPage, name: str) -> bool:
    return name == clean(page.title)


def _availability(text: str) -> str | None:
    if _AVAILABILITY_RE.search(text):
        return "24/7"
    return None


def _phone(text: str) -> str | None:
    match = _PHONE_RE.search(text)
    if not match:
        return None
    digits = "".join(ch for ch in match.group(0) if ch.isdigit())
    if len(digits) < 8:
        return None
    return clean(match.group(0))
