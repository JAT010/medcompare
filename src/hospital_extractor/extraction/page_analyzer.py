"""Page analysis: prepare a fetched page for entity extraction.

One HTML parse per page, shared by every extractor. Produces the document
signals the extraction ladder needs: visible text, headings, sections, FAQ
items (from JSON-LD ``FAQPage`` and HTML accordions), breadcrumbs, telephone
links, email links and card containers.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

from hospital_extractor.extraction.helpers import clean, text_of
from hospital_extractor.extraction.jsonld import JsonLdResult, extract_jsonld

__all__ = ["AnalyzedPage", "FaqItem", "Section", "analyze_page"]

_MAX_VISIBLE_TEXT = 100_000
_MAX_SECTION_TEXT = 4000

_PHONE_RE = re.compile(r"[+()\d][\d\s().-]{7,}\d")
_FAQ_CLASS_HINT = re.compile(r"(faq|accordion|question|answer|q[_-]?a)")
_QUESTION_CLASS_HINT = re.compile(r"(question|q[_-]?title|title|heading|h\d)")
_ANSWER_CLASS_HINT = re.compile(r"(answer|content|panel|body|description|desc)")


@dataclass(frozen=True)
class FaqItem:
    """One question/answer pair, either from JSON-LD or HTML."""

    question: str
    answer: str


@dataclass(frozen=True)
class Section:
    """A heading followed by its body text."""

    heading: str
    text: str


@dataclass(frozen=True)
class AnalyzedPage:
    """Everything extractors need from one fetched page."""

    url: str
    title: str | None
    soup: BeautifulSoup
    jsonld: JsonLdResult
    visible_text: str
    headings: tuple[tuple[str, str], ...]
    sections: tuple[Section, ...]
    faq_items: tuple[FaqItem, ...]
    breadcrumbs: tuple[str, ...]
    tel_numbers: tuple[str, ...]
    emails: tuple[str, ...]
    cards: tuple[Tag, ...]


def analyze_page(html: str, url: str, title: str | None = None) -> AnalyzedPage:
    """Parse *html* once and derive all document signals for *url*."""
    soup = BeautifulSoup(html, "lxml")
    page_title = clean(title) or (clean(soup.title.string) if soup.title else None)
    jsonld = extract_jsonld(html)
    visible_text = _visible_text(soup)
    faq_items = (*_faq_from_jsonld(jsonld), *_faq_from_html(soup))
    return AnalyzedPage(
        url=url,
        title=page_title,
        soup=soup,
        jsonld=jsonld,
        visible_text=visible_text,
        headings=tuple(_headings(soup)),
        sections=tuple(_sections(soup)),
        faq_items=_dedupe_faq(faq_items),
        breadcrumbs=tuple(_breadcrumbs(soup, jsonld)),
        tel_numbers=tuple(_tel_numbers(soup, visible_text)),
        emails=tuple(_emails(soup)),
        cards=tuple(_cards(soup)),
    )


def _visible_text(soup: BeautifulSoup) -> str:
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())[:_MAX_VISIBLE_TEXT]


def _headings(soup: BeautifulSoup) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for node in soup.find_all(["h1", "h2", "h3"]):
        value = clean(text_of(node))
        if value:
            result.append((node.name, value))
    return result


def _sections(soup: BeautifulSoup) -> list[Section]:
    sections: list[Section] = []
    for heading in soup.find_all(["h2", "h3"]):
        heading_text = clean(text_of(heading))
        if not heading_text:
            continue
        body = _section_body(heading)
        if body:
            sections.append(Section(heading=heading_text, text=body[:_MAX_SECTION_TEXT]))
    return sections


def _section_body(heading: Tag) -> str:
    parts: list[str] = []
    for sibling in heading.find_next_siblings():
        if sibling.name in ("h1", "h2", "h3", "h4", "script", "style", "nav", "header"):
            break
        value = text_of(sibling)
        if value:
            parts.append(value)
    return clean(" ".join(parts)) or ""


def _faq_from_jsonld(jsonld: JsonLdResult) -> list[FaqItem]:
    items: list[FaqItem] = []
    for block in jsonld.blocks:
        if "FAQPage" not in block.types:
            continue
        main_entity = block.data.get("mainEntity")
        if isinstance(main_entity, dict):
            main_entity = [main_entity]
        if not isinstance(main_entity, list):
            continue
        for entity in main_entity:
            if not isinstance(entity, dict):
                continue
            question = clean(_text_of_entity(entity.get("name")))
            answer_node = entity.get("acceptedAnswer")
            answer = None
            if isinstance(answer_node, dict):
                answer = clean(_text_of_entity(answer_node.get("text")))
            if question and answer:
                items.append(FaqItem(question=question, answer=answer))
    return items


def _text_of_entity(value: object) -> str | None:
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and clean(item):
                return clean(item)
    return None


def _faq_from_html(soup: BeautifulSoup) -> list[FaqItem]:
    items: list[FaqItem] = []
    for container in soup.find_all(_is_faq_container):
        q_answers = _extract_qa_pairs(container)
        items.extend(q_answers)
    return items


def _is_faq_container(tag: Tag) -> bool:
    if tag.name not in ("div", "section", "dl", "article", "ul"):
        return False
    class_value = " ".join(c for c in tag.get("class") or [])
    return bool(_FAQ_CLASS_HINT.search(class_value.lower()))


def _extract_qa_pairs(container: Tag) -> list[FaqItem]:
    items: list[FaqItem] = []
    nodes = [
        node
        for node in container.find_all(
            ["h3", "h4", "h5", "dt", "div", "li", "button", "p"], recursive=True
        )
    ]
    for index, node in enumerate(nodes):
        question = _question_text(node)
        if not question:
            continue
        answer = _answer_for(node, nodes[index + 1 :])
        if answer:
            items.append(FaqItem(question=question, answer=answer))
    return items


def _question_text(node: Tag) -> str | None:
    classes = " ".join(c for c in node.get("class") or [])
    if node.name in ("h3", "h4", "h5", "dt"):
        return clean(text_of(node))
    if _QUESTION_CLASS_HINT.search(classes.lower()) and len(text_of(node)) < 300:
        return clean(text_of(node))
    return None


def _answer_for(node: Tag, following: list[Tag]) -> str | None:
    for candidate in following[:4]:
        classes = " ".join(c for c in candidate.get("class") or [])
        if not _ANSWER_CLASS_HINT.search(classes.lower()) and candidate.name not in (
            "dd",
            "p",
            "div",
        ):
            continue
        answer = clean(text_of(candidate))
        if answer and answer.lower() != "read more":
            return answer
        if candidate.name == "dd" or "answer" in classes.lower():
            return answer or None
    return None


def _dedupe_faq(items: Sequence[FaqItem]) -> tuple[FaqItem, ...]:
    seen: set[tuple[str, str]] = set()
    result: list[FaqItem] = []
    for item in items:
        key = (item.question.lower(), item.answer.lower())
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return tuple(result)


def _breadcrumbs(soup: BeautifulSoup, jsonld: JsonLdResult) -> list[str]:
    items: list[str] = []
    for block in jsonld.blocks:
        if "BreadcrumbList" not in block.types:
            continue
        elements = block.data.get("itemListElement")
        if not isinstance(elements, list):
            continue
        for element in elements:
            if not isinstance(element, dict):
                continue
            name = clean(_text_of_entity(element.get("name")))
            if name:
                items.append(name)
    if items:
        return items
    for container in soup.find_all(class_=re.compile(r"breadcrumb", re.I)):
        text = clean(text_of(container))
        if text:
            items.extend(part for part in re.split(r"\s*[>/]\s*", text) if part.strip())
        break
    return items


def _tel_numbers(soup: BeautifulSoup, visible_text: str) -> list[str]:
    numbers: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if isinstance(href, str) and href.lower().startswith("tel:"):
            raw = href[4:].strip()
            if raw:
                numbers.append(raw)
    for match in _PHONE_RE.findall(visible_text):
        digits = "".join(ch for ch in match if ch.isdigit())
        if len(digits) >= 8 and match not in numbers:
            numbers.append(match.strip())
    return _dedupe_ordered(numbers)


def _emails(soup: BeautifulSoup) -> list[str]:
    emails: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if isinstance(href, str) and href.lower().startswith("mailto:"):
            email = href[7:].strip().lower()
            if email and email not in emails:
                emails.append(email)
    return emails


def _cards(soup: BeautifulSoup) -> list[Tag]:
    cards: list[Tag] = []
    for node in soup.find_all(["div", "article", "li", "section"]):
        classes = " ".join(c for c in node.get("class") or [])
        if re.search(r"(^|\s)(card|doctor|specialist|physician|profile)", classes.lower()):
            cards.append(node)
    return cards[:200]


def _dedupe_ordered(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
