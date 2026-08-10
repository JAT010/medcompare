"""Crawl report and human-readable summary.

Aggregates what a crawl run produced: access probe outcome, robots/sitemap
sources, candidate counts, per-category tallies and representative examples.
The summary is what the CLI prints at the end of the discovery phase.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from hospital_extractor.crawler.candidates import CandidatePage
from hospital_extractor.crawler.context import CrawlContext
from hospital_extractor.crawler.crawler import ProbeOutcome, ProbeResult
from hospital_extractor.crawler.metadata import PageMetadata
from hospital_extractor.crawler.url_filter import RobotsPolicy

__all__ = ["CrawlReport", "format_summary"]

_MAX_EXAMPLES_PER_CATEGORY = 3


@dataclass
class CrawlReport:
    """Everything the CLI needs to summarize a crawl/discovery run."""

    context: CrawlContext
    probe: ProbeResult
    robots_url: str
    robots_policy: RobotsPolicy | None
    sitemap_urls: tuple[str, ...]
    candidates: list[CandidatePage]
    pages: list[PageMetadata]
    skipped_urls: list[str]
    failed_urls: list[str]
    raw_dir: Path

    @property
    def discovered(self) -> int:
        return len(self.candidates)

    @property
    def fetched(self) -> int:
        return len(self.pages)

    @property
    def skipped(self) -> int:
        return len(self.skipped_urls)

    @property
    def failed(self) -> int:
        return len(self.failed_urls)

    def category_counts(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for page in self.pages:
            category = page.page_category or "unknown"
            counts[category] += 1
        return dict(sorted(counts.items()))

    def examples_by_category(self) -> dict[str, list[str]]:
        examples: dict[str, list[str]] = {}
        for page in self.pages:
            category = page.page_category or "unknown"
            bucket = examples.setdefault(category, [])
            if len(bucket) < _MAX_EXAMPLES_PER_CATEGORY:
                bucket.append(page.requested_url)
        return examples


def format_summary(report: CrawlReport) -> str:
    """Render the CLI summary block for a crawl report."""
    ctx = report.context
    lines: list[str] = [
        "Universal Hospital Extractor",
        "",
        "Target:",
        f"  URL:       {ctx.target_url}",
        f"  Domain:    {ctx.target_domain}",
        f"  Hospital:  {ctx.target_hospital_identity or '(not derivable from URL)'}",
        f"  User-Agent:{' ' + ctx.user_agent if ctx.user_agent else ''}",
        "",
        "Access:",
        f"  robots.txt: {_describe_outcome(report.probe.robots)}",
        f"  homepage:   {_describe_outcome(report.probe.homepage)}",
        "",
        f"Sitemaps loaded ({len(report.sitemap_urls)}):",
        *[f"  {url}" for url in report.sitemap_urls],
        "",
        f"Pages discovered: {report.discovered}",
        f"Pages fetched:    {report.fetched}",
        f"Pages skipped:    {report.skipped}",
        f"Pages failed:     {report.failed}",
        "",
        "Categories:",
    ]
    category_counts = report.category_counts()
    if category_counts:
        for category, count in category_counts.items():
            lines.append(f"  {category}: {count}")
        lines.append("")
        lines.append("Representative examples:")
        for category, urls in report.examples_by_category().items():
            lines.append(f"  {category}:")
            for url in urls:
                lines.append(f"    {url}")
    else:
        lines.append("  (no pages classified)")

    lines.extend(
        [
            "",
            "Raw pages:",
            f"  {report.raw_dir}",
            "",
            "Skipped reasons:",
            *[f"  {url}" for url in report.skipped_urls[:_MAX_EXAMPLES_PER_CATEGORY]],
        ]
    )
    if report.failed_urls:
        lines.extend(
            [
                "",
                "Failed pages:",
                *[f"  {url}" for url in report.failed_urls[:_MAX_EXAMPLES_PER_CATEGORY]],
            ]
        )
    return "\n".join(lines)


def _describe_outcome(outcome: ProbeOutcome) -> str:
    if outcome.status_code is None:
        return "unreachable"
    return f"{outcome.status_code} {outcome.content_type or 'unknown content type'}"
