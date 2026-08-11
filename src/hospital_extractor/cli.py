"""Command-line interface for the hospital website extractor.

``hospital-extractor URL`` runs the discovery/crawl phase for one hospital
website and prints a summary of what was found. The crawled raw pages are
stored under ``data/raw/<run>/``. With ``--export`` the crawl is followed by
the full extraction pipeline: dataset -> intake -> JSON + master CSV/XLSX
output (the final 25-column master intake) under ``data/export/<run>/``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from hospital_extractor.config import load_config
from hospital_extractor.crawler.context import CrawlLimits, build_context
from hospital_extractor.crawler.report import CrawlReport, format_summary
from hospital_extractor.crawler.runner import CrawlAbortedError, run_crawl
from hospital_extractor.export import (
    dataset_basename,
    export_master_csv,
    export_master_excel,
)
from hospital_extractor.extraction import CrawlStats, analyze_pages, build_dataset
from hospital_extractor.extraction.intake import project_intake
from hospital_extractor.schema.discovery import format_dataset_summary
from hospital_extractor.storage import write_dataset
from hospital_extractor.utils.logging import configure_logging
from hospital_extractor.utils.urls import InvalidURLError

__all__ = ["main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hospital-extractor",
        description="Extract intelligence from a hospital website.",
    )
    parser.add_argument("url", help="Hospital website URL, e.g. https://www.maxhealthcare.in")
    parser.add_argument(
        "--max-pages", type=int, default=CrawlLimits().max_pages, help="Page fetch budget"
    )
    parser.add_argument(
        "--max-depth", type=int, default=CrawlLimits().max_depth, help="Maximum link depth"
    )
    parser.add_argument(
        "--timeout", type=float, default=CrawlLimits().timeout, help="Per-request timeout (s)"
    )
    parser.add_argument(
        "--delay", type=float, default=CrawlLimits().delay, help="Delay between requests (s)"
    )
    parser.add_argument(
        "--max-retries", type=int, default=CrawlLimits().max_retries, help="Retries per request"
    )
    parser.add_argument("--output-dir", default="data", help="Output directory (default: data)")
    parser.add_argument("--log-level", default="INFO", help="Log level (default: INFO)")
    parser.add_argument("--user-agent", default=None, help="Override the primary user-agent")
    parser.add_argument(
        "--include-low-value",
        action="store_true",
        help="Also crawl low-value categories (blog, career, news, booking)",
    )
    parser.add_argument(
        "--min-relevance",
        type=float,
        default=CrawlLimits().enqueue_min_score,
        help="Minimum relevance score for pages discovered during the crawl",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Run extraction and export the master intake JSON/CSV/XLSX after the crawl",
    )
    return parser


async def _run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)

    limits = CrawlLimits(
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        timeout=args.timeout,
        max_retries=args.max_retries,
        delay=args.delay,
        enqueue_min_score=args.min_relevance,
    )
    try:
        context = build_context(args.url, limits=limits, user_agent=args.user_agent)
    except InvalidURLError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        report = await run_crawl(
            context,
            output_dir=args.output_dir,
            include_low_value=args.include_low_value,
        )
    except CrawlAbortedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(format_summary(report))

    if args.export:
        _extract_and_export(report, context.target_url, args.output_dir)
    return 0


def _extract_and_export(report: CrawlReport, target_url: str, output_dir: str) -> None:
    """Extract a dataset from the crawled pages and write all exports."""
    pages: list[tuple[str, str]] = []
    for page in report.pages:
        reference = page.raw_content_reference
        if reference is None:
            continue
        html = reference.read_bytes().decode("utf-8", errors="replace")
        pages.append((page.requested_url, html))
    crawl_stats = CrawlStats(
        pages_discovered=report.discovered,
        pages_fetched=report.fetched,
        pages_failed=report.failed,
        pages_skipped=report.skipped,
    )
    config = load_config()
    dataset = build_dataset(pages, target_url=target_url, crawl_stats=crawl_stats, config=config)

    export_root = Path(output_dir) / "export"
    basename = dataset_basename(dataset)
    json_path = write_dataset(dataset, export_root / f"{basename}.json")

    analyzed = analyze_pages(pages)
    intake = project_intake(dataset, pages=analyzed, target_url=target_url)
    master_dir = export_root / f"{basename}_master"
    master_csv_path, master_quality_path = export_master_csv(
        intake,
        master_dir,
        boolean_unknown_policy=config.boolean_unknown_policy,
        dataset=dataset,
    )
    master_xlsx_path = export_master_excel(
        intake,
        master_dir,
        boolean_unknown_policy=config.boolean_unknown_policy,
        dataset=dataset,
    )

    print()
    print(format_dataset_summary(dataset))
    print()
    print("Exported:")
    print(f"  {json_path}")
    print(f"  {master_csv_path}  (25-column master CSV)")
    print(f"  {master_xlsx_path}  (master intake XLSX)")
    print(f"  {master_quality_path}  (quality / provenance report)")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (sync wrapper around the async crawl)."""
    return asyncio.run(_run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
