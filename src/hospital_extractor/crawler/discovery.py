"""URL discovery: sitemaps, robots.txt, internal links and seed selection.

Primary discovery source is the sitemap index. This module parses sitemap
indexes and urlset documents into normalized URLs, extracts same-domain
internal links from fetched HTML, derives the hospital slug from the input
URL and ranks candidate seeds by hospital scope so a single-hospital run
prioritizes hospital-slug and city-scoped pages.

Pagination discovery: when a fetched page is a server-rendered listing
(Livewire or similar) whose HTML embeds a ``wire:initial-data`` attribute
containing ``"page":N`` and a ``"nextPageUrl"``, this module generates
candidate URLs for pages 2 through a configurable limit so that paginated
listings (doctor listings, speciality directories) are fully crawled.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree
from xml.etree.ElementTree import ParseError as XmlParseError

from bs4 import BeautifulSoup

from hospital_extractor.crawler.candidates import SOURCE_SITEMAP, CandidatePage, dedupe_candidates
from hospital_extractor.crawler.url_filter import UrlFilter, parse_robots_txt
from hospital_extractor.utils.urls import is_same_domain, normalize_url, resolve_url

__all__ = [
    "SitemapData",
    "SitemapDiscoveryResult",
    "discover_pagination_candidates",
    "discover_seeds",
    "discover_sitemap_candidates",
    "extract_internal_links",
    "hospital_slug_from_url",
    "parse_robots_txt",
    "parse_sitemap",
    "scope_rank",
]

SitemapFetcher = Callable[[str], Awaitable[bytes | None]]

_DEFAULT_MAX_SITEMAPS = 50
_DEFAULT_MAX_SITEMAP_DEPTH = 3


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
    return sorted(eligible, key=lambda url: (scope_rank(url, hospital_slug, location), url))


def scope_rank(url: str, hospital_slug: str | None, location: str | None) -> int:
    """Order a URL by how directly it concerns the target hospital.

    Rank 0: pages under the hospital slug (profile, contact, awards...).
    Rank 1: location-scoped doctor listings (``/doctor/<speciality>-in-<location>``).
    Rank 2: everything else on the site.
    """
    path = urlsplit(url).path
    if hospital_slug and f"/{hospital_slug}" in path:
        return 0
    if location and "/doctor/" in path and f"-in-{location}" in path:
        return 1
    return 2


@dataclass(frozen=True)
class SitemapDiscoveryResult:
    """Candidate pages and the sitemap documents they came from."""

    candidates: list[CandidatePage]
    sitemap_urls: tuple[str, ...]


async def discover_sitemap_candidates(
    fetcher: SitemapFetcher,
    base_url: str,
    *,
    robots_sitemaps: tuple[str, ...] = (),
    use_default_sitemap_fallback: bool = True,
    max_sitemaps: int = _DEFAULT_MAX_SITEMAPS,
    max_sitemap_depth: int = _DEFAULT_MAX_SITEMAP_DEPTH,
) -> SitemapDiscoveryResult:
    """Walk the sitemap tree and return candidate pages.

    Starts from the sitemap URLs declared in ``robots.txt`` (``robots_sitemaps``);
    when none are declared, falls back to ``/sitemap.xml``. Sitemap indexes
    are followed to their children (bounded by ``max_sitemap_depth`` and
    ``max_sitemaps``), and every URL in a urlset becomes a
    :class:`CandidatePage` with ``source_of_discovery="sitemap"``.

    ``fetcher`` returns the raw bytes of a sitemap document or ``None`` when
    it could not be fetched (HTTP error, redirect loop, timeout). Malformed
    XML and duplicate URLs are handled without aborting discovery.
    """
    pending = list(robots_sitemaps)
    if use_default_sitemap_fallback and not pending:
        pending.append(urljoin(base_url, "/sitemap.xml"))

    queue: deque[tuple[str, int]] = deque((url, 0) for url in pending)
    visited: set[str] = set()
    candidates: list[CandidatePage] = []

    while queue and len(visited) < max_sitemaps:
        sitemap_url, depth = queue.popleft()
        if sitemap_url in visited or depth > max_sitemap_depth:
            continue
        visited.add(sitemap_url)

        content = await fetcher(sitemap_url)
        if content is None:
            continue
        data = parse_sitemap(content.decode("utf-8", errors="replace"), base_url)

        for child in data.child_sitemaps:
            if child not in visited:
                queue.append((child, depth + 1))
        for url in data.urls:
            candidates.append(
                CandidatePage(
                    url=url,
                    source_of_discovery=SOURCE_SITEMAP,
                    sitemap_name=sitemap_url,
                    depth=0,
                )
            )

    return SitemapDiscoveryResult(
        candidates=dedupe_candidates(candidates),
        sitemap_urls=tuple(sorted(visited)),
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ---------------------------------------------------------------------------
# Pagination discovery
# ---------------------------------------------------------------------------

_DEFAULT_MAX_PAGINATION_PAGES = 120
_PAGINATION_EVENT_RE = re.compile(
    r'"canonical-updated".*?"nextPageUrl"\s*:\s*"([^"]+)"',
    re.S,
)
_PAGINATION_PAGE_RE = re.compile(r'"page"\s*:\s*(\d+)')
_WIRE_INITIAL_DATA_RE = re.compile(r'wire:initial-data="([^"]+)"')


def discover_pagination_candidates(
    html: str,
    base_url: str,
    *,
    max_pagination_pages: int = _DEFAULT_MAX_PAGINATION_PAGES,
    url_filter: UrlFilter | None = None,
) -> list[CandidatePage]:
    """Generate candidate URLs for paginated listing pages.

    Inspects the HTML for a ``wire:initial-data`` attribute (Livewire v2)
    that embeds pagination state.  When a ``"nextPageUrl"`` is found in the
    ``canonical-updated`` dispatch, pages 2 through *max_pagination_pages*
    are generated by incrementing the ``page`` query parameter.  The base
    URL (page 1) is **not** included — it should already be in the crawl
    queue.

    Returns an empty list when the page has no pagination indicators.
    """
    match = _WIRE_INITIAL_DATA_RE.search(html)
    if not match:
        return []

    raw = _html_decode(match.group(1))
    next_url = _extract_next_page_url(raw)
    if not next_url:
        return []

    parsed = urlsplit(next_url)
    base_path = parsed.path or "/"
    base_page = _extract_current_page(raw) or 1

    candidates: list[CandidatePage] = []
    for page_num in range(base_page + 1, max_pagination_pages + 1):
        page_url = f"{parsed.scheme}://{parsed.netloc}{base_path}?page={page_num}"
        if url_filter is not None and not url_filter.is_allowed(page_url):
            break
        candidates.append(
            CandidatePage(
                url=page_url,
                source_of_discovery=SOURCE_SITEMAP,
                depth=0,
            )
        )
    return candidates


def _html_decode(value: str) -> str:
    """Decode HTML entities in a ``wire:initial-data`` attribute value."""
    return (
        value.replace("&quot;", '"')
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


def _extract_next_page_url(data: str) -> str | None:
    """Extract the ``nextPageUrl`` from decoded Livewire JSON."""
    match = _PAGINATION_EVENT_RE.search(data)
    if match:
        return match.group(1).replace("\\/", "/")
    return None


def _extract_current_page(data: str) -> int | None:
    """Extract the current ``page`` number from decoded Livewire JSON."""
    match = _PAGINATION_PAGE_RE.search(data)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None
