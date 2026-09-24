"""Universal hospital website intelligence extractor.

Phase 1 (this version): discover and crawl one hospital website, classify
its pages, and cache raw HTML locally. Extraction of structured entities
(canonical schema, dedup, export) is a later phase.
"""

from __future__ import annotations

from hospital_extractor.crawler import CrawlContext, Crawler, CrawlReport
from hospital_extractor.crawler.context import CrawlLimits, build_context
from hospital_extractor.crawler.runner import run_crawl
from hospital_extractor.extraction.jsonld import extract_jsonld
from hospital_extractor.storage.raw import RawStore

__all__ = [
    "CrawlContext",
    "CrawlLimits",
    "CrawlReport",
    "Crawler",
    "RawStore",
    "build_context",
    "extract_jsonld",
    "run_crawl",
]
