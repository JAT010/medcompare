"""Doctor extraction — shallow, hospital-scoped.

Sources, strongest first: ``Physician`` JSON-LD, doctor cards, doctor profile
pages. Only name, speciality, department, designation, experience, gender
(when published) and profile URL are extracted. Education, publications and
external profiles are out of scope.

Critical rule: appearing on the website does not make a doctor part of the
target hospital. Every doctor is tagged with a hospital association state
(``confirmed`` / ``probable`` / ``uncertain`` / ``not_associated``); the
dataset builder applies the configured inclusion threshold.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from bs4 import Tag

from hospital_extractor.extraction.helpers import clean, provenance, text_of
from hospital_extractor.extraction.listing import is_boilerplate
from hospital_extractor.extraction.page_analyzer import AnalyzedPage
from hospital_extractor.models.doctor import Doctor

__all__ = ["HospitalContext", "extract_doctors"]


@dataclass(frozen=True)
class HospitalContext:
    """Identity of the target hospital, used to judge doctor association."""

    target_url: str
    slug: str | None = None
    name: str | None = None
    city: str | None = None
    location_text: str | None = None


_EXPERIENCE_RE = re.compile(
    r"(?:experience\s*[:]?\s*|over\s+)(\d[\d,.]*\s*\+?\s*(?:years?|yrs?))"
    r"|(\d[\d,.]*\s*\+?\s*(?:years|yrs)\s+(?:of\s+)?experience)",
    re.I,
)
_GENDER_HINTS = ("male", "female")
_LISTING_IN_RE = re.compile(r"-in-([a-z0-9-]+)$")
_NAME_CLASS_RE = re.compile(r"(name|title|h\d)", re.I)
_CARD_CLASS_RE = re.compile(r"(card|doctor|specialist|physician|profile|team)", re.I)
_DEPARTMENT_CLASS_RE = re.compile(r"(department|dept)\b", re.I)
_LOCATION_CLASS_RE = re.compile(r"\b(location|city|address)\b", re.I)


def extract_doctors(page: AnalyzedPage, context: HospitalContext) -> list[Doctor]:
    """Extract every doctor record found on *page*."""
    records = [*_doctors_from_jsonld(page, context), *_doctors_from_cards(page, context)]
    return _dedupe_profiles(records)


def _doctors_from_jsonld(page: AnalyzedPage, context: HospitalContext) -> list[Doctor]:
    records: list[Doctor] = []
    for block in page.jsonld.blocks:
        if "Physician" not in block.types and "Person" not in block.types:
            continue
        data = block.data
        name = clean(data.get("name"))
        if not name:
            continue
        profile_url = _jsonld_url(data.get("url"), page.url)
        association = _association_from_jsonld(data, context)
        records.append(
            Doctor(
                name=name,
                speciality=_join_list(data.get("medicalSpecialty")),
                designation=clean(data.get("jobTitle")),
                gender=_gender(_join_list(data.get("gender"))),
                profile_url=profile_url,
                hospital_association=association,
                **provenance(page.url, title=page.title, method="jsonld", strength="high",
                              confidence=0.9 if association == "confirmed" else None),
            )
        )
    return records


def _is_doctor_profile_path(segments: list[str]) -> bool:
    """Return True when *segments* represent a doctor profile URL.

    Accepts two patterns:
    - ``/doctor/<slug>`` (2 segments, first is ``doctor``)
    - ``/<…>/doctor/<slug>`` (any prefix ending with ``/doctor/<slug>``)
    The ``slug`` must not contain ``-in-`` (which marks city-scoped listing
    pages, not individual profiles).
    """
    if not segments or "-in-" in segments[-1]:
        return False
    if len(segments) >= 2 and segments[-2] == "doctor":
        return True
    return False


def _doctors_from_cards(page: AnalyzedPage, context: HospitalContext) -> list[Doctor]:
    cards: list[Tag] = []
    seen_anchors: set[str] = set()
    for anchor in page.soup.find_all("a", href=True):
        href = anchor["href"]
        if not isinstance(href, str):
            continue
        path = urlsplit(href).path
        segments = [segment for segment in path.split("/") if segment]
        if not _is_doctor_profile_path(segments):
            continue
        if href in seen_anchors:
            continue
        seen_anchors.add(href)
        card = _card_for(anchor)
        if card is not None:
            cards.append(card)

    records: list[Doctor] = []
    for card in cards:
        record = _doctor_from_card(card, page, context)
        if record is not None:
            records.append(record)
    return records


def _doctor_from_card(card: Tag, page: AnalyzedPage, context: HospitalContext) -> Doctor | None:
    name = _card_name(card)
    if not name:
        return None
    link = _profile_anchor(card)
    profile_url = _normalize_link(link, page.url)
    designation = _card_designation(card)
    experience = _card_experience(card)
    speciality = _card_speciality(card, designation, experience)
    gender = _card_gender(card)
    association = _association_from_page(page, context)
    return Doctor(
        name=name,
        speciality=speciality,
        department=_card_department(card),
        designation=designation,
        experience=experience,
        gender=gender,
        location=_card_location(card),
        profile_url=profile_url,
        hospital_association=association,
        **provenance(
            page.url,
            title=page.title,
            method="card",
            strength="medium",
            context=text_of(card)[:300],
        ),
    )


def _card_for(anchor: Tag) -> Tag | None:
    node: Tag | None = anchor
    for _ in range(5):
        if node is None:
            return None
        parent = node.parent
        if parent is None or parent.name == "html":
            return None
        node = parent
        classes = " ".join(c for c in node.get("class") or [])
        if _CARD_CLASS_RE.search(classes):
            return node
        if node.name in ("section", "article", "li"):
            return node
    return None


def _card_name(card: Tag) -> str | None:
    for node in card.find_all(["h3", "h4", "h5", "strong"]):
        value = clean(text_of(node))
        if value and len(value) < 80:
            return value
    for node in card.find_all(class_=re.compile(r"(^|\s)(doctor-name|doc-name|name)", re.I)):
        value = clean(text_of(node))
        if value:
            return value
    return None


def _profile_anchor(card: Tag) -> str | None:
    for anchor in card.find_all("a", href=True):
        href = anchor["href"]
        if isinstance(href, str) and re.search(r"/doctor/[a-z0-9-]+$", urlsplit(href).path):
            return href
    return None


def _card_designation(card: Tag) -> str | None:
    for node in card.find_all(class_=re.compile(r"designation|role", re.I)):
        value = clean(text_of(node))
        if value:
            return value
    return None


def _card_experience(card: Tag) -> str | None:
    text = text_of(card)
    match = _EXPERIENCE_RE.search(text)
    if match:
        value = clean(match.group(1) or match.group(2))
        if value and len(value) < 60:
            return value
    return None


def _card_department(card: Tag) -> str | None:
    for node in card.find_all(class_=_DEPARTMENT_CLASS_RE):
        value = clean(text_of(node))
        if value and not is_boilerplate(value) and len(value) < 120:
            return value
    return None


def _card_location(card: Tag) -> str | None:
    for node in card.find_all(class_=_LOCATION_CLASS_RE):
        value = clean(text_of(node))
        if value and not is_boilerplate(value) and len(value) < 120:
            return value
    return None


def _card_speciality(card: Tag, designation: str | None, experience: str | None) -> str | None:
    for node in card.find_all(class_=re.compile(r"specialit|specialt", re.I)):
        value = clean(text_of(node))
        if value:
            return value
    raw_lines = [clean(line) for line in card.get_text("\n", strip=True).splitlines()]
    lines: list[str] = []
    for line in raw_lines:
        if line:
            lines.append(line)
    for line in lines[1:4]:
        if line == designation or line == experience:
            continue
        if "," in line and not re.search(r"\d", line) and len(line) < 200:
            return line
    return None


def _card_gender(card: Tag) -> str | None:
    text = text_of(card).lower()
    for gender in _GENDER_HINTS:
        if re.search(rf"\b{gender}\b", text):
            return gender.capitalize()
    return None


def _association_from_page(page: AnalyzedPage, context: HospitalContext) -> str:
    if _same_domain(page.url, context.target_url):
        location = _listing_location(page.url)
        if context.slug and context.slug in page.url:
            return "probable"
        if location and context.location_text:
            if location in context.location_text:
                return "probable"
        if location and context.city:
            city = context.city.lower()
            if location in city or city.startswith(location):
                return "probable"
        return "probable"
    return "uncertain"


def _same_domain(url_a: str, url_b: str) -> bool:
    """True when both URLs share the same registered domain."""
    from urllib.parse import urlsplit

    domain_a = urlsplit(url_a).hostname or ""
    domain_b = urlsplit(url_b).hostname or ""
    domain_a = domain_a.lower().removeprefix("www.")
    domain_b = domain_b.lower().removeprefix("www.")
    return bool(domain_a and domain_b and domain_a == domain_b)


def _association_from_jsonld(data: dict[str, Any], context: HospitalContext) -> str:
    affiliations = _affiliations(data)
    if affiliations:
        if context.name:
            target = context.name.lower()
            for name in affiliations:
                candidate = name.lower()
                if target in candidate or candidate in target:
                    return "confirmed"
        return "not_associated"
    url = data.get("url")
    if isinstance(url, str) and context.target_url and _same_domain(url, context.target_url):
        return "probable"
    return "uncertain"


def _affiliations(data: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for key in ("worksFor", "hospitalAffiliation", "affiliation"):
        value = data.get(key)
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict):
                name = clean(item.get("name"))
                if name:
                    names.append(name)
                identifier = clean(item.get("@id"))
                if identifier:
                    names.append(identifier)
            elif isinstance(item, str):
                name = clean(item)
                if name:
                    names.append(name)
    return names


def _listing_location(url: str) -> str | None:
    path = urlsplit(url).path
    if "/doctor/" not in path:
        return None
    match = _LISTING_IN_RE.search(path)
    if match:
        return match.group(1).lower()
    return None


def _gender(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.lower()
    if lowered in ("male", "m"):
        return "Male"
    if lowered in ("female", "f"):
        return "Female"
    return None


def _jsonld_url(value: object, base_url: str) -> str | None:
    if isinstance(value, str) and clean(value):
        return _normalize_link(value, base_url)
    return None


def _normalize_link(href: str | None, base_url: str) -> str | None:
    from hospital_extractor.utils.urls import resolve_url

    return resolve_url(base_url, href) if href else None


def _join_list(value: object) -> str | None:
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, list):
        cleaned = [clean(item) for item in value if isinstance(item, str)]
        parts = [part for part in cleaned if part]
        return ", ".join(parts) if parts else None
    return None


def _dedupe_profiles(records: list[Doctor]) -> list[Doctor]:
    seen: set[str] = set()
    result: list[Doctor] = []
    for record in records:
        key = record.profile_url or record.name
        if key is not None and key not in seen:
            seen.add(key)
            result.append(record)
    return result
