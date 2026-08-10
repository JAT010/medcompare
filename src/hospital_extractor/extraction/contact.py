"""Location & contact extraction.

JSON-LD ``Hospital``/``LocalBusiness`` address and geo objects are the
strongest source; ``tel:``/``mailto:`` links and visible contact sections fill
the rest. Contact categories are only assigned from explicit labels
(``contactType`` / "appointment" / "emergency") — never invented.
"""

from __future__ import annotations

import re
from typing import Any

from hospital_extractor.extraction.helpers import clean, in_navigation, provenance, text_of
from hospital_extractor.extraction.page_analyzer import AnalyzedPage
from hospital_extractor.models.contact import Contact

__all__ = ["extract_contact"]

_CONTACT_JSONLD_TYPES = (
    "Hospital",
    "MedicalOrganization",
    "LocalBusiness",
    "MedicalClinic",
    "Organization",
    "HealthClub",
)

_EMERGENCY_LABELS = ("emergency", "ambulance", "24x7 emergency", "24/7 emergency")
_APPOINTMENT_LABELS = (
    "appointment",
    "book appointment",
    "book an appointment",
    "consultation booking",
    "opd booking",
    "schedule an appointment",
)
_PHONE_RE = re.compile(r"[+()\d][\d\s().-]{7,}\d")


def extract_contact(page: AnalyzedPage) -> Contact | None:
    """Extract one contact record from *page* (or ``None``)."""
    blocks = _contact_blocks(page)
    data: dict[str, Any] = {}
    _from_jsonld(blocks, data)
    _from_html(page, data)
    if not any(value is not None for value in data.values()):
        return None
    return Contact(
        **data,
        **provenance(page.url, title=page.title, method="jsonld" if blocks else "semantic_html",
                      strength="high" if blocks else "medium"),
    )


def _contact_blocks(page: AnalyzedPage) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block in page.jsonld.blocks:
        if set(block.types).intersection(_CONTACT_JSONLD_TYPES):
            blocks.append(block.data)
        elif (
            isinstance(block.data.get("address"), dict)
            and "streetAddress" in block.data["address"]
        ):
            blocks.append(block.data)
    return blocks


def _from_jsonld(blocks: list[dict[str, Any]], data: dict[str, Any]) -> None:
    for block in blocks:
        address = block.get("address")
        if isinstance(address, dict):
            data.setdefault("address", clean(address.get("streetAddress")))
            data.setdefault("city", clean(address.get("addressLocality")))
            data.setdefault("state", clean(address.get("addressRegion")))
            data.setdefault("country", clean(address.get("addressCountry")))
            data.setdefault("postal_code", clean(str(address.get("postalCode"))))
        if isinstance(address, str):
            data.setdefault("address", clean(address))
        geo = block.get("geo")
        if isinstance(geo, dict):
            data.setdefault("latitude", _float(geo.get("latitude")))
            data.setdefault("longitude", _float(geo.get("longitude")))
        telephone = block.get("telephone")
        if isinstance(telephone, str) and clean(telephone):
            data.setdefault("phone", clean(telephone))
        elif isinstance(telephone, list):
            for number in telephone:
                if isinstance(number, str) and clean(number):
                    data.setdefault("phone", clean(number))
        _contact_points(block.get("contactPoint"), data)
        for key in ("hasMap", "map", "maps"):
            value = block.get(key)
            if isinstance(value, str) and clean(value) and "maps" in value:
                data.setdefault("maps_url", clean(value))


def _contact_points(contact_points: object, data: dict[str, Any]) -> None:
    if isinstance(contact_points, dict):
        contact_points = [contact_points]
    if not isinstance(contact_points, list):
        return
    for point in contact_points:
        if not isinstance(point, dict):
            continue
        contact_type = str(point.get("contactType") or "").lower()
        telephone = point.get("telephone")
        number = clean(telephone) if isinstance(telephone, str) else None
        if not number:
            continue
        if "emergency" in contact_type or "ambulance" in contact_type:
            data.setdefault("emergency_phone", number)
        elif (
            "appointment" in contact_type
            or "booking" in contact_type
            or "reservation" in contact_type
        ):
            data.setdefault("appointment_phone", number)
        else:
            data.setdefault("phone", number)


def _from_html(page: AnalyzedPage, data: dict[str, Any]) -> None:
    if data.get("phone") is None:
        numbers = [n for n in page.tel_numbers if not _looks_like_year(n)]
        if numbers:
            data.setdefault("phone", numbers[0])
    if data.get("emergency_phone") is None:
        number = _labelled_phone(page, _EMERGENCY_LABELS)
        if number:
            data.setdefault("emergency_phone", number)
    if data.get("appointment_phone") is None:
        number = _labelled_phone(page, _APPOINTMENT_LABELS)
        if number:
            data.setdefault("appointment_phone", number)
    if data.get("email") is None and page.emails:
        data.setdefault("email", page.emails[0])
    if data.get("maps_url") is None:
        for anchor in page.soup.find_all("a", href=True):
            href = anchor["href"]
            if isinstance(href, str) and (
                "google.com/maps" in href or "maps.google" in href or "goo.gl/maps" in href
            ):
                data.setdefault("maps_url", href)
                break
    if data.get("address") is None:
        address = _visible_address(page) or _address_link_address(page) or _address_element(page)
        if address:
            data.setdefault("address", address)


def _visible_address(page: AnalyzedPage) -> str | None:
    for section in page.sections:
        if "address" in section.heading.lower() or "location" in section.heading.lower():
            return clean(section.text)
    return None


def _address_link_address(page: AnalyzedPage) -> str | None:
    for link in page.address_links:
        value = clean(link[len("address:"):])
        if value:
            return value
    return None


def _address_element(page: AnalyzedPage) -> str | None:
    for node in page.soup.find_all("address"):
        if in_navigation(node):
            continue
        value = clean(text_of(node))
        if value:
            return value
    return None


def _labelled_phone(page: AnalyzedPage, labels: tuple[str, ...]) -> str | None:
    for section in page.sections:
        if not any(label in section.heading.lower() for label in labels):
            continue
        match = _PHONE_RE.search(section.text)
        if match:
            return clean(match.group(0))
    for item in page.faq_items:
        if not any(label in item.question.lower() for label in labels):
            continue
        match = _PHONE_RE.search(item.answer)
        if match:
            return clean(match.group(0))
    return None


def _looks_like_year(value: str) -> bool:
    digits = "".join(ch for ch in value if ch.isdigit())
    return len(digits) == 4 and value.isdigit()


def _float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
