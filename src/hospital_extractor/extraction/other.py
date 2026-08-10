"""Other hospital information — controlled fallback.

FAQ question/answer pairs that match no canonical category land here as
``label``/``value`` facts. Only genuinely hospital-relevant pairs are kept;
navigation questions ("how do I book") route to their own categories and never
appear here.
"""

from __future__ import annotations

from hospital_extractor.extraction.helpers import provenance
from hospital_extractor.extraction.page_analyzer import AnalyzedPage
from hospital_extractor.models.other import OtherInformation
from hospital_extractor.schema.mapper import route_faq_question

__all__ = ["extract_other"]

_NAV_HINTS = ("download", "career", "feedback", "login", "sign in", "newsletter")


def extract_other(
    page: AnalyzedPage, *, min_answer_length: int = 20
) -> list[OtherInformation]:
    """Extract unmatched FAQ pairs as label/value facts."""
    records: list[OtherInformation] = []
    for item in page.faq_items:
        if route_faq_question(item.question) is not None:
            continue
        if len(item.answer) < min_answer_length:
            continue
        if any(hint in item.question.lower() for hint in _NAV_HINTS):
            continue
        records.append(
            OtherInformation(
                label=item.question,
                value=item.answer,
                **provenance(page.url, title=page.title, method="faq", strength="medium",
                              context=item.answer[:300]),
            )
        )
    return records
