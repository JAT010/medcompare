"""Hospital-level overview and infrastructure/capacity extraction.

The hospital profile page is the anchor. JSON-LD (``Hospital``,
``MedicalOrganization``, ``LocalBusiness``) is the strongest signal; About Us
text supplies published capacity figures and the established year. Numbers are
only parsed when an explicit pattern matches ("650+ bedded" -> 650); nothing
is inferred.
"""

from __future__ import annotations

import re
from typing import Any

from hospital_extractor.extraction.helpers import (
    blocks_of,
    clean,
    first_block_value,
    first_text,
    provenance,
    text_of,
)
from hospital_extractor.extraction.page_analyzer import AnalyzedPage
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.infrastructure import Infrastructure
from hospital_extractor.processing.normalize import parse_leading_int, parse_year
from hospital_extractor.utils.text import dedupe_sequential
from hospital_extractor.utils.urls import resolve_url

__all__ = ["extract_hospital_overview", "extract_infrastructure", "find_about_text"]

_HOSPITAL_TYPES = (
    "Hospital",
    "MedicalOrganization",
    "LocalBusiness",
    "MedicalClinic",
    "HealthClub",
    "Organization",
)

_ABOUT_HEADING_RE = re.compile(r"about( us)?|overview|introduction", re.I)
_TYPE_HINTS = ("super-speciality", "multi-speciality", "tertiary care", "quaternary", "speciality")
_PARENT_HINTS = (r"part of (.+)", r"a unit of (.+)", r"parent organization[:\s]+(.+)")

_CAPACITY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("critical_care_beds", r"(\d[\d,]*)\+?\s*(?:critical care|critical-care)\s*beds?"),
    ("icu_beds", r"(\d[\d,]*)\+?\s*(?:icu|intensive care)\s*(?:beds?|units?)"),
    ("operation_theatres", r"(\d[\d,]*)\+?\s*(?:modular\s+)?operation\s+theatres?"),
    ("cath_labs", r"(\d[\d,]*)\+?\s*cath\s*labs?"),
    ("patient_rooms", r"(\d[\d,]*)\+?\s*(?:patient\s+)?rooms?"),
    ("total_beds", r"(\d[\d,]*)\+?\s*(?:bedded|beds?)\b"),
    ("total_beds", r"(?:bed|bedded)\s+capacity\s+(?:of|is|=|:)?\s*(\d[\d,]*)\+?"),
    ("total_beds", r"capacity\s+(?:of|is|=|:)?\s*(\d[\d,]*)\+?\s*(?:bedded|beds?)\b"),
)
_BARE_CAPACITY_RE = re.compile(r"\bcapacity\s+(?:of|is|=|:)?\s*(\d[\d,]*)\b", re.I)
_BARE_CAPACITY_GUARD_RE = re.compile(
    r"(icu|intensive|critical|cath|patient|diagnostic|mri|scan|parking|laborator)", re.I
)
_OTHER_CAPACITY_UNITS = ("bed", "beds", "unit", "units", "room", "rooms", "theatre", "theatres",
                         "lab", "labs", "suite", "suites", "ward", "wards", "floor", "floors",
                         "centre", "center", "seats", "scan", "scans", "machine", "machines")
_OTHER_CAPACITY_RE = re.compile(r"(\d[\d,]*)\+?\s*([a-z][a-z ]{1,40}?)\b")

_COUNT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("doctor_count", r"(\d[\d,]*)\+?\s*(?:specialists?|doctors?|consultants?)\b"),
    ("staff_count", r"(\d[\d,]*)\+?\s*staff\b"),
    ("speciality_count", r"(\d[\d,]*)\+?\s*(?:specialit\w*|specialties)\b"),
    ("department_count", r"(\d[\d,]*)\+?\s*departments?\b"),
)
_ESTABLISHED_HINTS = (
    r"est\.?\s+(\d{4})",
    r"established\s+in\s+(\d{4})",
    r"since\s+(\d{4})",
    r"founded\s+in\s+(\d{4})",
)


def extract_hospital_overview(
    page: AnalyzedPage, *, website: str | None = None
) -> HospitalOverview | None:
    """Extract the hospital overview record from an anchor page."""
    jsonld_blocks = _rank_blocks(page, blocks_of(page.jsonld, *_HOSPITAL_TYPES))
    about = find_about_text(page)

    name = _preferred_name(page, jsonld_blocks)
    if not name:
        return None

    description = _preferred_description(page, jsonld_blocks, about)
    hospital_type = _hospital_type(about)
    parent = _parent_organization(about, jsonld_blocks)
    established = _established_year(about)
    status = _status(page)
    website = _website(page, jsonld_blocks, website)
    logo_url = _logo_url(page, jsonld_blocks)

    data = {
        "name": name,
        "description": description,
        "hospital_type": hospital_type,
        "parent_organization": parent,
        "established_year": established,
        "status": status,
        "website": website,
        "logo_url": logo_url,
    }
    data.update(_counts(about))
    record = HospitalOverview(
        **data,
        **provenance(page.url, title=page.title, method="jsonld" if jsonld_blocks else "heading",
                      strength="high" if jsonld_blocks else "medium", context=about),
    )
    return record if (record.name or record.website) else None


def extract_infrastructure(page: AnalyzedPage) -> Infrastructure | None:
    """Extract published capacity/infrastructure figures from *page*."""
    about = find_about_text(page)
    if not about:
        return None
    remaining = about
    values: dict[str, int | None] = {
        "total_beds": None,
        "icu_beds": None,
        "operation_theatres": None,
        "cath_labs": None,
        "critical_care_beds": None,
        "patient_rooms": None,
    }
    for field, pattern in _CAPACITY_PATTERNS:
        match = re.search(pattern, remaining, re.I)
        if match:
            if values[field] is None:
                values[field] = parse_leading_int(match.group(1))
            remaining = remaining[: match.start()] + " " + remaining[match.end():]
    if values["total_beds"] is None:
        values["total_beds"] = _bare_capacity(about)
    other = _other_capacity(remaining)
    if not any(values.values()) and not other:
        return None
    return Infrastructure(
        total_beds=values["total_beds"],
        icu_beds=values["icu_beds"],
        operation_theatres=values["operation_theatres"],
        cath_labs=values["cath_labs"],
        critical_care_beds=values["critical_care_beds"],
        patient_rooms=values["patient_rooms"],
        other_capacity=other,
        **provenance(page.url, title=page.title, method="text", strength="medium", context=about),
    )


def find_about_text(page: AnalyzedPage) -> str | None:
    """Best About Us text on *page*: matching section, then first paragraph."""
    for section in page.sections:
        if _ABOUT_HEADING_RE.search(section.heading):
            return section.text
    for paragraph in page.soup.find_all("p"):
        text = clean(text_of(paragraph))
        if text and len(text) > 40:
            return text
    description = first_block_value(blocks_of(page.jsonld, *_HOSPITAL_TYPES), "description")
    return description


def _anchor_name(page: AnalyzedPage) -> str | None:
    for level, value in page.headings:
        if level == "h1":
            return value
    return None


def _preferred_name(page: AnalyzedPage, jsonld_blocks: list[dict[str, Any]]) -> str | None:
    """Best hospital name for the anchor page.

    The page-level JSON-LD block (URL matching the page, or a concrete
    ``Hospital``/``MedicalClinic``/``LocalBusiness`` type) names the specific
    hospital and wins.  When only a network-level ``MedicalOrganization`` or
    ``Organization`` block exists (its ``url`` is the site root), the block
    name is the parent brand ("Apollo Hospitals"), not this hospital — so the
    page's own H1 is preferred when it looks like a hospital name and is
    shorter than 60 characters.  Otherwise the JSON-LD name wins.

    A raw H1 that does not look like a hospital name is never used, even as
    a last resort.  Doctor listing pages ("Best Cardiologist in Noida"),
    procedure pages ("Liver Transplant in India"), and similar content must
    not leak through as hospital identity.
    """
    for block in jsonld_blocks:
        if _is_page_level(block, page):
            value = first_text(block, "name")
            if value:
                return value
    anchor = _anchor_name(page)
    if anchor and _looks_like_hospital_name(anchor) and len(anchor) < 60:
        return anchor
    jsonld_name = first_block_value(jsonld_blocks, "name")
    if jsonld_name:
        return jsonld_name
    title_name = _name_from_title(page.title)
    if title_name:
        return title_name
    return None


def _preferred_description(
    page: AnalyzedPage,
    jsonld_blocks: list[dict[str, Any]],
    about: str | None,
) -> str | None:
    """Best hospital description: page-level JSON-LD, then About text, then
    the network-level block description (last resort)."""
    for block in jsonld_blocks:
        if _is_page_level(block, page):
            value = first_text(block, "description")
            if value:
                return value
    if about:
        return about
    return first_block_value(jsonld_blocks, "description")


def _is_page_level(block: dict[str, Any], page: AnalyzedPage) -> bool:
    """True when a JSON-LD block describes this page's hospital, not the network.

    A block whose ``url`` matches the anchor page is page-level; a concrete
    hospital type (``Hospital``/``MedicalClinic``/``LocalBusiness``) is page
    level even without a URL, while a bare ``MedicalOrganization``/``Organization``
    is the network and not page-specific.
    """
    url = block.get("url")
    if isinstance(url, str) and _same_url(page.url, url):
        return True
    return block.get("@type") in ("Hospital", "MedicalClinic", "LocalBusiness")


_HOSPITAL_NAME_HINTS = (
    "hospital",
    "clinic",
    "medical",
    "health",
    "care",
    "centre",
    "center",
    "institute",
    "nursing",
    "specialit",
)


def _looks_like_hospital_name(value: str) -> bool:
    lowered = value.lower()
    return any(hint in lowered for hint in _HOSPITAL_NAME_HINTS)


_TITLE_SEP_RE = re.compile(r"\s*[|\-–—]\s*")


def _name_from_title(title: str | None) -> str | None:
    """Extract hospital brand name from a page title like 'Best Hospital | Apollo Hospitals'.

    Tries the last pipe/dash segment first (often the brand), then any
    segment that looks like a hospital name.
    """
    if not title:
        return None
    parts = _TITLE_SEP_RE.split(title)
    for part in reversed(parts):
        value = clean(part)
        if value and _looks_like_hospital_name(value) and len(value) < 80:
            return value
    return None


def _rank_blocks(page: AnalyzedPage, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order JSON-LD blocks so the specific hospital wins over the network.

    A block whose ``url`` matches the anchor page is the page-level hospital;
    a concrete type (``Hospital``/``MedicalClinic``/``LocalBusiness``) beats a
    generic network-level ``MedicalOrganization``. Stable for equal scores.
    """

    def score(block: dict[str, Any]) -> tuple[int, int, int]:
        url = block.get("url")
        url_match = 0 if isinstance(url, str) and _same_url(page.url, url) else 1
        specific = 0 if block.get("@type") in ("Hospital", "MedicalClinic", "LocalBusiness") else 1
        has_name = 0 if "name" in block else 1
        return (url_match, specific, has_name)

    return sorted(blocks, key=score)


def _same_url(one: str, two: str) -> bool:
    from urllib.parse import urlsplit

    def key(url: str) -> tuple[str, str, str]:
        parts = urlsplit(url.strip())
        return (parts.hostname or "").lower(), parts.path.rstrip("/"), parts.query

    return key(one) == key(two)


def _hospital_type(about: str | None) -> str | None:
    if not about:
        return None
    lowered = about.lower()
    for hint in _TYPE_HINTS:
        if hint in lowered:
            return hint
    return None


def _parent_organization(about: str | None, jsonld_blocks: list[dict[str, Any]]) -> str | None:
    for block in jsonld_blocks:
        parent = block.get("parentOrganization")
        if isinstance(parent, dict):
            name = clean(text_of_name(parent.get("name")))
            if name:
                return name
        if isinstance(parent, str) and clean(parent):
            return clean(parent)
    if not about:
        return None
    for pattern in _PARENT_HINTS:
        match = re.search(pattern, about, re.I)
        if match:
            value = clean(match.group(1))
            if value and len(value) < 120:
                return value
    return None


def _established_year(about: str | None) -> int | None:
    if not about:
        return None
    for pattern in _ESTABLISHED_HINTS:
        match = re.search(pattern, about, re.I)
        if match:
            return parse_year(match.group(1))
    return None


def _status(page: AnalyzedPage) -> str | None:
    about = find_about_text(page) or ""
    text = (about + " " + page.visible_text[:2000]).lower()
    if re.search(r"24\s*[x/]7|round the clock|open all day", text):
        return "24/7"
    return None


def _website(
    page: AnalyzedPage, jsonld_blocks: list[dict[str, Any]], fallback: str | None
) -> str | None:
    for block in jsonld_blocks:
        url = block.get("url")
        if isinstance(url, str) and clean(url):
            return clean(url)
    for block in jsonld_blocks:
        same_as = block.get("sameAs")
        if isinstance(same_as, str) and clean(same_as):
            return clean(same_as)
    return fallback


def _logo_url(page: AnalyzedPage, jsonld_blocks: list[dict[str, Any]]) -> str | None:
    """Best logo URL for the hospital: the page-level hospital's JSON-LD logo.

    Only a hospital-specific block (``Hospital``/``MedicalClinic``/
    ``LocalBusiness``, or a block whose ``url`` matches the page) may supply the
    logo. A network/parent brand logo (``MedicalOrganization`` at the site
    root) is corporate chrome, not this hospital's logo, so when it is the only
    declared logo we deliberately do not fall back to the header brand image.
    URLs are resolved against the page and normalized; ``None`` when nothing
    usable is found.
    """
    page_level = [block for block in jsonld_blocks if _is_page_level(block, page)]
    for block in page_level:
        logo = block.get("logo")
        if isinstance(logo, str):
            resolved = resolve_url(page.url, logo)
            if resolved:
                return resolved
        if isinstance(logo, dict):
            for key in ("url", "contentUrl"):
                value = logo.get(key)
                if isinstance(value, str):
                    resolved = resolve_url(page.url, value)
                    if resolved:
                        return resolved
    if any(not _is_page_level(block, page) and block.get("logo") for block in jsonld_blocks):
        return None
    for link in page.soup.find_all("link", rel=True):
        rel = link.get("rel")
        rels = rel if isinstance(rel, list) else [rel]
        if any("icon" in str(r).lower() for r in rels):
            href = link.get("href")
            if isinstance(href, str):
                resolved = resolve_url(page.url, href)
                if resolved:
                    return resolved
    header = page.soup.find("header") or page.soup.find("nav")
    if header is not None:
        for img in header.find_all("img"):
            src = img.get("src")
            if isinstance(src, str):
                resolved = resolve_url(page.url, src)
                if resolved:
                    return resolved
    return None


def _counts(about: str | None) -> dict[str, int | None]:
    if not about:
        return {
            "doctor_count": None,
            "staff_count": None,
            "speciality_count": None,
            "department_count": None,
        }
    result: dict[str, int | None] = {}
    for field, pattern in _COUNT_PATTERNS:
        match = re.search(pattern, about, re.I)
        result[field] = parse_leading_int(match.group(1)) if match else None
    return result


def _bare_capacity(about: str) -> int | None:
    """Parse a bare "capacity of 650" only when the context supports beds.

    Without an explicit bed unit, "capacity" is ambiguous (parking, labs,
    OPD slots...). We only accept it when the same About text mentions beds and
    the surrounding window is free of other capacity-like nouns.
    """
    match = _BARE_CAPACITY_RE.search(about)
    if not match:
        return None
    if "bed" not in about.lower():
        return None
    window = about[max(0, match.start() - 120): match.end() + 120]
    if _BARE_CAPACITY_GUARD_RE.search(window):
        return None
    return parse_leading_int(match.group(1))


def _other_capacity(text: str) -> str | None:
    phrases: list[str] = []
    for match in _OTHER_CAPACITY_RE.finditer(text):
        unit = match.group(2).strip().rstrip(".,;")
        last_word = unit.rsplit(" ", 1)[-1]
        if last_word not in _OTHER_CAPACITY_UNITS:
            continue
        phrase = clean(f"{match.group(1).replace(',', '')} {unit}")
        if phrase and phrase not in phrases:
            phrases.append(phrase)
    if not phrases:
        return None
    return "; ".join(dedupe_sequential(phrases))


def text_of_name(value: object) -> str | None:
    if isinstance(value, str):
        return clean(value)
    return None
