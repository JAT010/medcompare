"""Program extraction.

Programs (health check-up plans, corporate wellness, international care) are
extracted from FAQ answers and labelled sections using the schema mapper's
vocabulary.
"""

from __future__ import annotations

from hospital_extractor.extraction.helpers import provenance
from hospital_extractor.extraction.page_analyzer import AnalyzedPage
from hospital_extractor.models.program import Program
from hospital_extractor.schema.mapper import route_faq_question

__all__ = ["extract_programs"]

_PROGRAM_CATEGORY_HINTS = (
    ("health check-up", ("health check", "check-up", "checkup", "preventive", "full body")),
    ("wellness", ("wellness", "corporate", "employee")),
    ("international", ("international patient", "foreign", "medical visa")),
)


def extract_programs(page: AnalyzedPage) -> list[Program]:
    records: list[Program] = []
    records.extend(_from_faq(page))
    records.extend(_from_sections(page))
    return records


def _from_faq(page: AnalyzedPage) -> list[Program]:
    records: list[Program] = []
    for item in page.faq_items:
        if route_faq_question(item.question) != "programs":
            continue
        records.append(
            Program(
                name=item.question,
                category=_category(item.question, item.answer),
                description=item.answer,
                **provenance(page.url, title=page.title, method="faq", strength="medium",
                              context=item.answer[:300]),
            )
        )
    return records


def _from_sections(page: AnalyzedPage) -> list[Program]:
    records: list[Program] = []
    for section in page.sections:
        if route_faq_question(section.heading) != "programs":
            continue
        records.append(
            Program(
                name=section.heading,
                category=_category(section.heading, section.text),
                description=section.text,
                **provenance(page.url, title=page.title, method="section", strength="medium",
                              context=section.text[:300]),
            )
        )
    return records


def _category(*parts: str) -> str | None:
    text = " ".join(part.lower() for part in parts if part)
    for category, hints in _PROGRAM_CATEGORY_HINTS:
        if any(hint in text for hint in hints):
            return category
    return None
