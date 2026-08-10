"""Insurance / payment extraction.

Insurance providers, TPAs and government schemes are extracted from FAQ
answers, insurance-labelled sections and cards. Flags (``tpa``, ``cashless``,
``government_scheme``) are set to "Yes" only when the published text says so;
``None`` means unknown, never ``False``.
"""

from __future__ import annotations

import re

from hospital_extractor.extraction.helpers import clean, provenance, text_of
from hospital_extractor.extraction.listing import card_name, is_boilerplate
from hospital_extractor.extraction.page_analyzer import AnalyzedPage
from hospital_extractor.models.insurance import InsuranceProvider
from hospital_extractor.schema.mapper import route_faq_question

__all__ = ["extract_insurance"]

_INSURANCE_HEADING_RE = re.compile(r"insurance|tpa|cashless|empanel|scheme|health plan", re.I)
_GOVERNMENT_HINTS = (
    "cghs",
    "e-cghs",
    "ecs",
    "ayushman",
    "pmjay",
    "pm jay",
    "eshram",
    "esic",
    "govt",
)
_CARD_CLASS_RE = re.compile(r"(insurance|tpa|cashless|provider)", re.I)


def extract_insurance(page: AnalyzedPage) -> list[InsuranceProvider]:
    records: list[InsuranceProvider] = []
    records.extend(_from_faq(page))
    records.extend(_from_sections(page))
    records.extend(_from_cards(page))
    return records


def _from_faq(page: AnalyzedPage) -> list[InsuranceProvider]:
    records: list[InsuranceProvider] = []
    for item in page.faq_items:
        if route_faq_question(item.question) != "insurance":
            continue
        records.append(
            InsuranceProvider(
                name=item.question,
                cashless=_flag(item.question, item.answer, keyword="cashless"),
                tpa=_flag(item.question, item.answer, keyword="tpa"),
                government_scheme=_government_flag(item.question, item.answer),
                description=item.answer,
                **provenance(page.url, title=page.title, method="faq", strength="medium",
                              context=item.answer[:300]),
            )
        )
    return records


def _from_sections(page: AnalyzedPage) -> list[InsuranceProvider]:
    records: list[InsuranceProvider] = []
    for section in page.sections:
        if not _INSURANCE_HEADING_RE.search(section.heading):
            continue
        for line in _split_names(section.text):
            records.append(
                InsuranceProvider(
                    name=line,
                    cashless=_flag(section.heading, line, keyword="cashless"),
                    tpa=_flag(section.heading, line, keyword="tpa"),
                    government_scheme=_government_flag(section.heading, line),
                    **provenance(page.url, title=page.title, method="section", strength="medium",
                                  context=section.text[:300]),
                )
            )
    return records


def _from_cards(page: AnalyzedPage) -> list[InsuranceProvider]:
    records: list[InsuranceProvider] = []
    for card in page.cards:
        classes = " ".join(c for c in card.get("class") or [])
        if not _CARD_CLASS_RE.search(classes):
            continue
        name = card_name(card)
        if not name:
            continue
        text = text_of(card)
        records.append(
            InsuranceProvider(
                name=name,
                cashless=_flag(name, text, keyword="cashless"),
                tpa=_flag(name, text, keyword="tpa"),
                government_scheme=_government_flag(name, text),
                **provenance(page.url, title=page.title, method="card", strength="medium",
                              context=text[:300]),
            )
        )
    return records


def _split_names(text: str) -> list[str]:
    names: list[str] = []
    for part in re.split(r"[\n,;•]", text):
        value = clean(part)
        if not value or is_boilerplate(value):
            continue
        if not (3 <= len(value) <= 80):
            continue
        if re.search(
            r"\b(tpa|cashless|covered|claim|benefit|network|scheme|empanel)\b", value, re.I
        ):
            continue
        names.append(value)
    return names


def _flag(*parts: str, keyword: str) -> str | None:
    return "Yes" if any(keyword in part.lower() for part in parts if part) else None


def _government_flag(*parts: str) -> str | None:
    text = " ".join(part.lower() for part in parts if part)
    return "Yes" if any(hint in text for hint in _GOVERNMENT_HINTS) else None
