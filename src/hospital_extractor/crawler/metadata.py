"""Per-page metadata captured during a crawl.

Everything the extraction stage might later need is preserved here, even if
it is not consumed yet: requested/final URL, status, content type, document
signals (title, meta description, canonical URL), crawl position (depth,
parent, discovery source), classification and where the raw HTML lives on
disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bs4 import BeautifulSoup

from hospital_extractor.crawler.classify import PageClassification
from hospital_extractor.crawler.crawler import FetchedPage
from hospital_extractor.utils.urls import resolve_url

__all__ = ["PageMetadata", "build_metadata"]


@dataclass(frozen=True)
class PageMetadata:
    """Metadata for one fetched page."""

    requested_url: str
    final_url: str
    status_code: int
    content_type: str | None
    title: str | None
    meta_description: str | None
    canonical_url: str | None
    depth: int
    parent_url: str | None
    discovery_source: str | None
    page_category: str | None
    classification_confidence: float | None
    fetch_timestamp: datetime
    raw_content_reference: Path | None


def build_metadata(
    page: FetchedPage,
    *,
    depth: int,
    parent_url: str | None,
    discovery_source: str | None,
    classification: PageClassification | None,
    raw_content_reference: Path | None,
    fetch_timestamp: datetime | None = None,
) -> PageMetadata:
    """Build :class:`PageMetadata` from a fetched page.

    Document signals (title, meta description, canonical URL) are parsed
    from the raw HTML. ``fetch_timestamp`` defaults to now (UTC).
    """
    title, meta_description, canonical_url = _document_signals(page.html, page.final_url)
    return PageMetadata(
        requested_url=page.url,
        final_url=page.final_url,
        status_code=page.status_code,
        content_type=page.content_type,
        title=title,
        meta_description=meta_description,
        canonical_url=canonical_url,
        depth=depth,
        parent_url=parent_url,
        discovery_source=discovery_source,
        page_category=classification.category if classification else None,
        classification_confidence=classification.confidence if classification else None,
        fetch_timestamp=fetch_timestamp or datetime.now(UTC),
        raw_content_reference=raw_content_reference,
    )


def _document_signals(html: bytes, base_url: str) -> tuple[str | None, str | None, str | None]:
    soup = BeautifulSoup(html, "lxml")
    title = _clean(soup.title.string) if soup.title else None

    description: str | None = None
    meta = soup.find("meta", attrs={"name": "description"})
    if meta is not None:
        value = meta.get("content")
        if isinstance(value, str):
            description = _clean(value)

    canonical: str | None = None
    canonical_link = soup.find("link", rel="canonical")
    if canonical_link is not None:
        href = canonical_link.get("href")
        if isinstance(href, str):
            canonical = resolve_url(base_url, href)
    return title, description, canonical


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None
