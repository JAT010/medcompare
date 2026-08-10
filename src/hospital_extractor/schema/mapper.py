"""Mapping rules from website vocabulary to canonical categories.

This is the single place that maps site terms (URL sections, headings, FAQ
questions) to canonical categories and fields. It deliberately contains no
hospital-specific knowledge: the vocabulary is generic hospital-website
language, and every mapping is conservative — an unknown term routes to
``other_information`` or nowhere, never to a guessed category.
"""

from __future__ import annotations

from hospital_extractor.schema.canonical import alias_to_category

__all__ = ["FAQ_ROUTES", "route_faq_question", "route_section_keyword"]

# FAQ question routing: keyword -> canonical category key. The first hit wins.
# Order matters: more specific terms come before generic ones.
FAQ_ROUTES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("cashless", "tpa", "insurance", "cghs", "e-cghs", "health insurance"), "insurance"),
    (("visiting hours", "visiting time", "visitor"), "visiting_information"),
    (("opd", "opd hours", "outpatient"), "visiting_information"),
    (("appointment", "book a", "book an"), "visiting_information"),
    (("admission",), "visiting_information"),
    (("discharge",), "visiting_information"),
    (("registration",), "visiting_information"),
    (("ambulance", "emergency"), "emergency_services"),
    (("international patient", "foreign patient"), "patient_services"),
    (("virtual consultation", "teleconsult", "video consultation"), "patient_services"),
    (("pharmacy", "chemist"), "facilities"),
    (("diagnostic", "lab", "laboratory", "radiology", "imaging", "mri", "ct scan"), "facilities"),
    (("operation theatre", "surgical", "surgery"), "infrastructure"),
    (("icu", "intensive care", "critical care", "bed"), "infrastructure"),
    (("rehab", "physiotherapy", "nursing", "home care"), "patient_services"),
    (("billing", "payment", "pay ", "fees"), "patient_services"),
    (("parking", "security", "cafeteria", "canteen", "restaurant"), "facilities"),
    (("blood bank",), "facilities"),
    (("health check", "check-up", "wellness", "preventive"), "programs"),
    (("accreditation", "nabet", "nabh", "jci"), "accreditations"),
    (("award", "recognised", "recognized"), "awards"),
)

# URL section/heading keyword -> canonical category. First hit wins.
_SECTION_ROUTES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("hospital-network", "about-us", "about", "overview"), "hospital"),
    (("contact-us", "contact", "reach-us", "locate"), "contacts"),
    (("our-specialities", "speciality", "specialties", "specialist"), "specialities"),
    (("department",), "departments"),
    (("doctor", "physician", "find-a-doctor", "our-team"), "doctors"),
    (("conditions-treatments", "treatment", "condition"), "treatments"),
    (("procedure", "surgery"), "procedures"),
    (("technology",), "technologies"),
    (("facilit",), "facilities"),
    (("service",), "patient_services"),
    (("emergency", "ambulance", "urgent"), "emergency_services"),
    (("insurance", "tpa", "cashless"), "insurance"),
    (("accreditation", "award"), "accreditations"),
    (("program", "health-check", "wellness"), "programs"),
    (("visiting", "timing", "visitors"), "visiting_information"),
    (("faq", "help", "query"), "other_information"),
)


def route_faq_question(question: str) -> str | None:
    """Return the canonical category key an FAQ question maps to.

    Returns ``None`` when the question matches nothing hospital-categorical;
    the caller may then route it to ``other_information`` or drop it.
    """
    lowered = question.lower()
    for keywords, category in FAQ_ROUTES:
        if any(keyword in lowered for keyword in keywords):
            return category
    return None


def route_section_keyword(keyword: str) -> str | None:
    """Map a URL path segment or heading keyword to a canonical category."""
    lowered = keyword.lower()
    for keywords, category in _SECTION_ROUTES:
        if any(candidate in lowered for candidate in keywords):
            return category
    return alias_to_category(lowered)
