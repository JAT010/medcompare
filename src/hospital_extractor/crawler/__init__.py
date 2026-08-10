"""Crawler: URL discovery, filtering, and polite fetching."""

from __future__ import annotations

from hospital_extractor.crawler.crawler import (
    Crawler,
    CrawlError,
    CrawlStoppedError,
    FetchedPage,
    FetchError,
    MaxPagesError,
    ProbeResult,
)
from hospital_extractor.crawler.discovery import (
    SitemapData,
    discover_seeds,
    extract_internal_links,
    hospital_slug_from_url,
    parse_robots_txt,
    parse_sitemap,
)
from hospital_extractor.crawler.url_filter import RobotsPolicy, UrlFilter

__all__ = [
    "CrawlError",
    "CrawlStoppedError",
    "Crawler",
    "FetchError",
    "FetchedPage",
    "MaxPagesError",
    "ProbeResult",
    "RobotsPolicy",
    "SitemapData",
    "UrlFilter",
    "discover_seeds",
    "extract_internal_links",
    "hospital_slug_from_url",
    "parse_robots_txt",
    "parse_sitemap",
]
