"""Mapping rules from website vocabulary to canonical categories.

This is the single place that maps FAQ question / section-heading vocabulary
to canonical categories. It deliberately contains no hospital-specific
knowledge beyond the categories that feed the final 25-field intake: the
vocabulary is generic hospital-website language, and every mapping is
conservative — an unknown term routes nowhere, never to a guessed category.
"""

from __future__ import annotations

__all__ = ["FAQ_ROUTES", "route_faq_question"]

# FAQ question/section routing: keyword -> canonical category key. The first
# hit wins. Only the ``facilities`` category consumes routed FAQ content in
# the current pipeline, so only its keywords are listed.
FAQ_ROUTES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("pharmacy", "chemist"), "facilities"),
    (("diagnostic", "lab", "laboratory", "radiology", "imaging", "mri", "ct scan"), "facilities"),
    (("parking", "security", "cafeteria", "canteen", "restaurant"), "facilities"),
    (("blood bank",), "facilities"),
)


def route_faq_question(question: str) -> str | None:
    """Return the canonical category key an FAQ question maps to.

    Returns ``None`` when the question matches nothing hospital-categorical;
    the caller then drops it.
    """
    lowered = question.lower()
    for keywords, category in FAQ_ROUTES:
        if any(keyword in lowered for keyword in keywords):
            return category
    return None
