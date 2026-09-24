"""Crawler: URL discovery, filtering, and polite fetching."""

from __future__ import annotations

from hospital_extractor.crawler.candidates import CandidatePage, CandidateQueue
from hospital_extractor.crawler.classify import PageClassification, PageClassifier
from hospital_extractor.crawler.context import CrawlContext, CrawlLimits, build_context
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
    discover_sitemap_candidates,
    extract_internal_links,
    hospital_slug_from_url,
    parse_robots_txt,
    parse_sitemap,
    scope_rank,
)
from hospital_extractor.crawler.metadata import PageMetadata
from hospital_extractor.crawler.report import CrawlReport
from hospital_extractor.crawler.runner import (
    CrawlAbortedError,
    TargetInaccessibleError,
    run_crawl,
)
from hospital_extractor.crawler.url_filter import RobotsPolicy, UrlFilter

__all__ = [
    "CandidatePage",
    "CandidateQueue",
    "CrawlAbortedError",
    "CrawlContext",
    "CrawlError",
    "CrawlLimits",
    "CrawlReport",
    "CrawlStoppedError",
    "Crawler",
    "FetchError",
    "FetchedPage",
    "MaxPagesError",
    "PageClassification",
    "PageClassifier",
    "PageMetadata",
    "ProbeResult",
    "RobotsPolicy",
    "SitemapData",
    "TargetInaccessibleError",
    "UrlFilter",
    "build_context",
    "discover_seeds",
    "discover_sitemap_candidates",
    "extract_internal_links",
    "hospital_slug_from_url",
    "parse_robots_txt",
    "parse_sitemap",
    "run_crawl",
    "scope_rank",
]
