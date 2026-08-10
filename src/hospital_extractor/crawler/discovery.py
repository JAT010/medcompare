"""URL discovery: sitemaps, robots.txt, internal links and seed selection.

Primary discovery source is the sitemap index. This module parses sitemap
indexes and urlset documents into normalized URLs, extracts same-domain
internal links from fetched HTML, derives the hospital slug from the input
URL and ranks candidate seeds by hospital scope so a single-hospital run
prioritizes hospital-slug and city-scoped pages.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit
from xml.etree import ElementTree
from xml.etree.ElementTree import ParseError as XmlParseError

from bs4 import BeautifulSoup

from hospital_extractor.crawler.url_filter import UrlFilter, parse_robots_txt
from hospital_extractor.utils.urls import is_same_domain, normalize_url, resolve_url

__all__ = [
    "SitemapData",
    "discover_seeds",
    "extract_internal_links",
    "hospital_slug_from_url",
    "parse_robots_txt",
    "parse_sitemap",
]


@dataclass(frozen=True)
class SitemapData:
    """Result of parsing one sitemap document.

    A ``sitemapindex`` yields ``child_sitemaps`` and no page URLs; a
    ``urlset`` yields page ``urls`` and no children.
    """

    urls: tuple[str, ...] = ()
    child_sitemaps: tuple[str, ...] = ()


def parse_sitemap(content: str, base_url: str = "") -> SitemapData:
    """Parse sitemap XML *content* into normalized URLs and child sitemaps.

    Locs are resolved against *base_url* (when absolute URLs are present the
    base is ignored) and normalized for deduplication. Malformed XML yields
    an empty :class:`SitemapData`.
    """
    try:
        root = ElementTree.fromstring(content)
    except XmlParseError:
        return SitemapData()

    is_index = _local_name(root.tag) == "sitemapindex"
    urls: list[str] = []
    children: list[str] = []
    for loc in root.iter():
        if _local_name(loc.tag) != "loc" or not loc.text:
            continue
        try:
            resolved = resolve_url(base_url, loc.text) or normalize_url(loc.text)
        except ValueError:
            continue
        if is_index:
            children.append(resolved)
        else:
            urls.append(resolved)
    return SitemapData(urls=tuple(urls), child_sitemaps=tuple(children))


def extract_internal_links(
    html: str, base_url: str, *, url_filter: UrlFilter | None = None
) -> list[str]:
    """Return normalized same-domain links found in *html*.

    Skips fragment-only, ``javascript:``, ``mailto:`` and ``tel:`` hrefs.
    When *url_filter* is given it is applied to each candidate; otherwise a
    plain same-domain check is used. Results are deduplicated in document
    order.
    """
    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        resolved = resolve_url(base_url, href)
        if resolved is None:
            continue
        if url_filter is not None:
            if not url_filter.is_allowed(resolved):
                continue
        elif not is_same_domain(resolved, base_url):
            continue
        links.append(resolved)
    return dedupe_urls(links)


def dedupe_urls(urls: list[str]) -> list[str]:
    """Deduplicate already-normalized URLs, preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


def hospital_slug_from_url(url: str) -> str | None:
    """Derive the hospital slug from the input hospital URL.

    Handles the ``/hospital-network/<slug>`` pattern of the target site and
    falls back to the last path segment.
    """
    path = urlsplit(url).path
    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return None
    if segments[0] == "hospital-network" and len(segments) >= 2:
        return segments[1]
    return segments[-1]


def discover_seeds(
    base_url: str,
    sitemap_content: str,
    *,
    hospital_slug: str | None = None,
    location: str | None = None,
    url_filter: UrlFilter | None = None,
) -> list[str]:
    """Select crawl seed URLs from a sitemap document.

    URLs are filtered for eligibility (domain, fetchability, robots, explicit
    exclusions) then ordered by hospital scope: hospital profile pages first,
    location-scoped doctor listings second, everything else last.
    """
    parsed = parse_sitemap(sitemap_content, base_url)
    eligible = [url for url in parsed.urls if url_filter is None or url_filter.is_allowed(url)]
    eligible = dedupe_urls(eligible)
    return sorted(eligible, key=lambda url: (_seed_rank(url, hospital_slug, location), url))


def _seed_rank(url: str, hospital_slug: str | None, location: str | None) -> int:
    path = urlsplit(url).path
    if hospital_slug and f"/{hospital_slug}" in path:
        return 0
    if location and "/doctor/" in path and f"-in-{location}" in path:
        return 1
    return 2


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
