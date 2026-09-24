"""Command-line interface for the hospital extractor.

Subcommands:

1. ``hospital-extractor URL`` — runs the discovery/crawl + extraction pipeline
   for a single hospital website (backward-compatible, unchanged behavior).

2. ``hospital-extractor compare URL1 URL2 [URL3] [URL4] --procedure "..."``
   — harvests multiple hospitals through any configured source and prints a
   comparison table.

3. ``hospital-extractor integration-demo`` — end-to-end source-agnostic demo
   that pulls the two demo hospitals from the local demo API (in-process),
   persists them into the hospital store and runs a comparison, printing
   timings.  Fully local — no internet, no server process.

4. ``hospital-extractor capture`` — explicit snapshot + change-detection for a
   hospital; schedules a verification job when an authoritative capture
   detects real changes.

5. ``hospital-extractor verify`` / ``verification-jobs`` / ``worker`` — the
   P19-B verification workflow.  ``verify`` schedules a job (idempotent),
   ``verification-jobs`` lists jobs + results, and ``worker`` is the explicit
   background capture loop that executes the verification captures.

6. ``hospital-extractor verification-demo`` — SIMULATED end-to-end demo of
   the verification + reconciliation orchestrator (Scenario A VERIFIED,
   Scenario B MISMATCH -> reviewed+resolved case, Scenario C UNVERIFIED ->
   reviewable case).

7. ``hospital-extractor reconciliation-cases`` / ``reconciliation`` /
   ``review`` / ``resolve`` / ``dismiss`` / ``reopen`` — the P19-C
   reconciliation workflow: list a hospital's cases, show one case's evidence
   and audit trail, and advance it through the small state machine.  These are
   purely database operations — no crawling, no writes back to any source.

8. ``hospital-extractor monitoring`` — P20 continuous monitoring: list / add /
   enable / disable / run scheduled capture policies, plus the explicit
   ``monitoring worker`` that claims due policies and delegates to the
   existing orchestration service.

9. ``hospital-extractor search`` — P22 structured search over stored datasets
   (deterministic field filters, free text) and side-by-side field comparison
   of 2–4 stored hospitals (no ranking, UNKNOWN never becomes NO).

10. ``hospital-extractor quality-report`` — P23 data-quality & observability
    report from stored rows only (pipeline / hospital / dataset scopes).
    Outputs JSON or Markdown. No overall score is ever computed.
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
from hospital_extractor.models.change import ChangeSetStatus
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.schema.discovery import format_dataset_summary
from hospital_extractor.sources.models import SourceType
from hospital_extractor.sources.pipeline import HarvestError, harvest
from hospital_extractor.storage import default_database_path, write_dataset
from hospital_extractor.utils.logging import configure_logging
from hospital_extractor.utils.urls import InvalidURLError

__all__ = ["main"]

SOURCE_CHOICES = SourceType.values()


# ---------------------------------------------------------------------------
# Existing single-URL commands (unchanged)
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hospital-extractor",
        description="Extract intelligence from a hospital website.",
    )
    parser.add_argument("url", help="Hospital website URL, e.g. https://www.maxhealthcare.in")
    parser.add_argument(
        "--source",
        choices=SOURCE_CHOICES,
        default="website",
        help=(
            "Data source: website (default), mock_api (local demo API), "
            "hospital_api (authorized API), json_feed (persisted dataset file)"
        ),
    )
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
    parser.add_argument(
        "--gov-export",
        action="store_true",
        help=(
            "Also write a SIMULATED government compliance submission package "
            "(always a local dry-run, never uploaded)"
        ),
    )
    return parser


async def _run_single(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)

    if SourceType(args.source) is not SourceType.WEBSITE:
        return await _run_single_from_source(args)

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
        _extract_and_export(
            report, context.target_url, args.output_dir, gov_export=args.gov_export
        )
    return 0


async def _run_single_from_source(args: argparse.Namespace) -> int:
    """Fetch a canonical dataset from a non-website source and export it."""
    try:
        harvested = await harvest(
            [args.url],
            source_type=args.source,
            base_url=getattr(args, "base_url", None),
            endpoint=getattr(args, "endpoint", None),
            provider=getattr(args, "provider", None),
        )
    except HarvestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    dataset = harvested[0].dataset

    export_root = Path(args.output_dir) / "export"
    basename = dataset_basename(dataset)
    json_path = write_dataset(dataset, export_root / f"{basename}.json")

    print()
    print(format_dataset_summary(dataset))
    print()
    print("Exported:")
    print(f"  {json_path}")
    if dataset.source is not None:
        print(f"  source: {dataset.source.source_type.value} ({dataset.source.provider})")
    if args.gov_export:
        _write_government_export(dataset, export_root)
    return 0


def _extract_and_export(
    report: CrawlReport, target_url: str, output_dir: str, gov_export: bool = False
) -> None:
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
    if gov_export:
        _write_government_export(dataset, export_root)


def _write_government_export(dataset: HospitalDataset, export_root: Path) -> None:
    """Write the SIMULATED government portal submission package (dry-run)."""
    from hospital_extractor.exporters.government_portal import GovernmentPortalExporter

    result = GovernmentPortalExporter().export(dataset, export_root / "government")
    print(f"  {result.destination}  (SIMULATED government submission, dry-run)")
    for artifact in result.artifacts:
        print(f"      {artifact.path.name}  sha256={artifact.sha256[:12]}")


# ---------------------------------------------------------------------------
# Compare subcommand
# ---------------------------------------------------------------------------


def build_compare_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hospital-extractor compare",
        description="Compare multiple hospitals for a target procedure.",
    )
    parser.add_argument(
        "urls",
        nargs="+",
        help="Hospital website URLs (2-4)",
    )
    parser.add_argument(
        "--source",
        choices=SOURCE_CHOICES,
        default="website",
        help=(
            "Data source for every hospital: website (default), mock_api "
            "(local demo API), hospital_api (authorized API), json_feed "
            "(persisted dataset files)"
        ),
    )
    parser.add_argument(
        "--procedure",
        required=True,
        help="Target procedure or department (e.g. 'Knee Replacement')",
    )
    parser.add_argument(
        "--category",
        choices=("procedure", "treatment", "speciality", "department"),
        default=None,
        help="Target category (default: auto-detected from the name)",
    )
    parser.add_argument(
        "--output-dir",
        default="data",
        help="Output directory for per-hospital exports (default: data)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output the full comparison result as JSON instead of a table",
    )
    parser.add_argument(
        "--json-file",
        default=None,
        help="Write the full comparison JSON to a file",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Log level (default: INFO)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=CrawlLimits().max_pages,
        help="Page fetch budget per hospital",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=CrawlLimits().max_depth,
        help="Maximum link depth per hospital",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=CrawlLimits().delay,
        help="Delay between requests (s)",
    )
    return parser


async def _run_compare(argv: list[str] | None = None) -> int:
    args = build_compare_parser().parse_args(argv)
    configure_logging(args.log_level)

    urls = args.urls
    if len(urls) < 2:
        print("error: at least 2 hospital URLs are required", file=sys.stderr)
        return 2
    if len(urls) > 4:
        print("error: at most 4 hospital URLs are supported", file=sys.stderr)
        return 2

    limits = CrawlLimits(
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        delay=args.delay,
    )

    print(f"Comparing {len(urls)} hospitals for: {args.procedure}\n")

    datasets: list[tuple[str, HospitalDataset]] = []
    for ref in urls:
        # Wording derives from source metadata (the WEBSITE adapter crawls
        # pages; every other adapter fetches a structured payload).
        is_crawler = SourceType(args.source) is SourceType.WEBSITE
        verb = "Crawling" if is_crawler else "Fetching"
        print(f"{verb} {ref} ...", end=" ", flush=True)
        try:
            harvested = await harvest(
                [ref],
                source_type=args.source,
                limits=limits,
                output_dir=args.output_dir,
                include_low_value=False,
                base_url=getattr(args, "base_url", None),
                endpoint=getattr(args, "endpoint", None),
                provider=getattr(args, "provider", None),
            )
        except HarvestError as exc:
            print(f"FAILED: {exc}", file=sys.stderr)
            return 1
        dataset = harvested[0].dataset
        datasets.append((ref, dataset))
        is_crawled = dataset.source is not None and dataset.source.source_type is SourceType.WEBSITE
        if is_crawled:
            print(f"OK ({dataset.quality.pages_crawled} pages)")
        else:
            name = dataset.hospital.name if dataset.hospital is not None else ref
            print(f"OK -> {name}")

    # Compare
    from hospital_extractor.comparison.service import compare_urls

    result = compare_urls(datasets, args.procedure, category=args.category)

    # Output
    if args.output_json:
        print(result.model_dump_json(indent=2))
    elif args.json_file:
        json_path = Path(args.json_file)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        print(f"\nComparison JSON written to: {json_path}")
        _print_table(result)
    else:
        _print_table(result)

    return 0


def _print_table(result: object) -> None:
    """Print a human-readable comparison table to stdout."""
    from hospital_extractor.comparison.models import ComparisonResult

    result = result  # already typed below

    # Type narrowing for IDEs
    assert isinstance(result, ComparisonResult)

    hospitals = result.hospitals
    if not hospitals:
        print("\nNo hospitals to compare.")
        return

    target = result.target
    print(f"\n{'='*70}")
    print(f"  Target: {target.name}")
    print(f"  Category: {target.category}")
    print(f"{'='*70}\n")

    # Column widths
    dim_width = max(len(d.label) for d in hospitals[0].dimensions) + 2
    hosp_width = max(
        max((len(h.hospital_name or h.hospital_id) for h in hospitals), default=20),
        20,
    ) + 2

    # Header
    header = f"{'Dimension':<{dim_width}}"
    for h in hospitals:
        name = h.hospital_name or h.hospital_id
        header += f"  {name:>{hosp_width}}"
    print(header)
    print("-" * len(header))

    # Rows
    for dim in hospitals[0].dimensions:
        row = f"{dim.label:<{dim_width}}"
        for h in hospitals:
            hdim = next(d for d in h.dimensions if d.dimension_key == dim.dimension_key)
            cell = hdim.decision.upper()
            if hdim.value is not None:
                if isinstance(hdim.value, list):
                    cell += f" ({len(hdim.value)})"
                elif isinstance(hdim.value, dict):
                    parts = [f"{v}" for v in hdim.value.values()]
                    cell += f" ({', '.join(parts)})"
                elif isinstance(hdim.value, (int, float)):
                    cell += f" ({hdim.value})"
                else:
                    cell += f" ({hdim.value})"
            row += f"  {cell:>{hosp_width}}"
        print(row)

    print()
    for h in hospitals:
        name = h.hospital_name or h.hospital_id
        pct = f"{h.evidence_coverage:.0%}"
        print(f"  {name}: {pct} evidence coverage")
    print()


# ---------------------------------------------------------------------------
# integration-demo subcommand
# ---------------------------------------------------------------------------


def build_integration_demo_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hospital-extractor integration-demo",
        description=(
            "End-to-end source-agnostic demo: pull both demo hospitals from "
            "the local demo API (in-process, no server, no internet), persist "
            "to the SQLite hospital store, load them back, run a comparison "
            "and print timings."
        ),
    )
    parser.add_argument(
        "--procedure",
        default="Knee Replacement",
        help="Target procedure or department (default: Knee Replacement)",
    )
    parser.add_argument(
        "--category",
        choices=("procedure", "treatment", "speciality", "department"),
        default=None,
        help="Target category (default: auto-detected from the name)",
    )
    parser.add_argument(
        "--db",
        default=str(default_database_path()),
        help="Hospital store path (default: data/medcompare.db)",
    )
    parser.add_argument(
        "--hospitals",
        nargs="+",
        default=["demo-northstar", "demo-solstice"],
        help="Demo hospital ids (default: demo-northstar demo-solstice)",
    )
    return parser


async def _run_integration_demo(argv: list[str] | None = None) -> int:
    import time as _time

    import httpx

    from hospital_extractor.comparison.service import compare_urls
    from hospital_extractor.mockapi.app import create_mock_app
    from hospital_extractor.sources.mock_api import MockHospitalAPIDataSource
    from hospital_extractor.storage import HospitalDatabase

    args = build_integration_demo_parser().parse_args(argv)
    db_path = Path(args.db)

    print("integration-demo (source-agnostic, fully local)")
    print("=" * 66)

    # 1. Harvest each demo hospital through the real mock-API adapter pointed
    #    at the in-process ASGI demo app — the exact same boundary a future
    #    authorized hospital API will use.
    datasets: list[tuple[str, HospitalDataset]] = []
    transport = httpx.ASGITransport(app=create_mock_app())
    client = httpx.AsyncClient(transport=transport, base_url="http://demo")
    try:
        for hospital_id in args.hospitals:
            start = _time.perf_counter()
            dataset = await MockHospitalAPIDataSource(
                hospital_id=hospital_id, client=client
            ).fetch()
            elapsed = _time.perf_counter() - start
            name = dataset.hospital.name if dataset.hospital is not None else hospital_id
            provider = dataset.source.provider if dataset.source is not None else None
            print(f"  [{elapsed:6.3f}s] mock_api fetch {hospital_id:<16} {name} ({provider})")
            datasets.append((hospital_id, dataset))
    finally:
        await client.aclose()

    # 2. Persist into the SQLite hospital store (profile layer).
    start = _time.perf_counter()
    with HospitalDatabase(db_path) as db:
        for _reference, dataset in datasets:
            db.save_dataset(dataset)
        summaries = db.list_datasets()
    persist_elapsed = _time.perf_counter() - start
    print(f"  [{persist_elapsed:6.3f}s] persisted {len(datasets)} datasets to {db_path}")

    # 3. Load the stored datasets back (cached profile read).
    start = _time.perf_counter()
    loaded: list[tuple[str, HospitalDataset]] = []
    with HospitalDatabase(db_path) as db:
        for summary in summaries:
            if summary.hospital_ref not in args.hospitals:
                continue
            stored = db.load_dataset(summary.dataset_id)
            if stored is not None:
                loaded.append((summary.hospital_ref, stored))
    load_elapsed = _time.perf_counter() - start
    print(f"  [{load_elapsed:6.3f}s] sqlite profile load ({len(loaded)} hospitals)")

    # 4. Compare the loaded canonical datasets (cached comparison).
    start = _time.perf_counter()
    result = compare_urls(loaded, args.procedure, category=args.category)
    compare_elapsed = _time.perf_counter() - start
    print(
        f"  [{compare_elapsed:6.3f}s] comparison for {args.procedure!r} "
        f"(category={result.target.category})"
    )

    # 5. Summary.
    print()
    for hospital in result.hospitals:
        name = hospital.hospital_name or hospital.hospital_id
        sources = ", ".join(hospital.source_types)
        print(f"  {name}: sources={sources}, evidence coverage={hospital.evidence_coverage:.0%}")
        for dim in hospital.dimensions:
            if dim.dimension_key == "procedure_available":
                verdict = "CONFLICT" if dim.conflicts else dim.decision.upper()
                print(f"      procedure_available: {verdict}")
    print()
    print("Demo complete. Browse profiles at /hospitals and compare via the web UI.")
    return 0


# ---------------------------------------------------------------------------
# Snapshot / change-detection subcommands (P19-A)
# ---------------------------------------------------------------------------


def build_capture_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hospital-extractor capture",
        description=(
            "Explicitly capture one hospital dataset as an immutable snapshot "
            "and detect changes against the previous snapshot of the same "
            "source.  Nothing is ever scheduled automatically."
        ),
    )
    parser.add_argument("ref", help="Hospital reference (URL, hospital id, or dataset JSON file)")
    parser.add_argument(
        "--source",
        choices=SOURCE_CHOICES,
        default="json_feed",
        help=(
            "Data source: website, mock_api, hospital_api, json_feed "
            "(default: json_feed — a previously persisted dataset file)"
        ),
    )
    parser.add_argument(
        "--db",
        default=str(default_database_path()),
        help="Hospital store path (default: data/medcompare.db)",
    )
    parser.add_argument(
        "--base-url", default=None, help="Adapter base URL (hospital_api / mock_api)"
    )
    parser.add_argument(
        "--endpoint", default=None, help="Custom endpoint template (hospital_api)"
    )
    parser.add_argument("--provider", default=None, help="Provider name (default: adapter id)")
    parser.add_argument("--log-level", default="WARNING", help="Log level (default: WARNING)")
    return parser


def build_history_parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument("hospital", help="hospital id (h_…), source ref, or hospital name")
    parser.add_argument(
        "--source",
        default=None,
        help="Narrow to one source type (e.g. website, mock_api, json_feed)",
    )
    parser.add_argument(
        "--db",
        default=str(default_database_path()),
        help="Hospital store path (default: data/medcompare.db)",
    )
    return parser


async def _run_capture(argv: list[str] | None = None) -> int:
    from hospital_extractor.models.verification import VerificationJob
    from hospital_extractor.storage import HospitalDatabase
    from hospital_extractor.verification.service import schedule_job_for_capture

    args = build_capture_parser().parse_args(argv)
    configure_logging(args.log_level)

    kwargs: dict[str, object] = {}
    if args.base_url:
        kwargs["base_url"] = args.base_url
    if args.endpoint:
        kwargs["endpoint"] = args.endpoint
    if args.provider:
        kwargs["provider"] = args.provider

    try:
        harvested = await harvest(
            [args.ref],
            source_type=args.source,
            **kwargs,  # type: ignore[arg-type]
        )
    except HarvestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    dataset = harvested[0].dataset

    print(f"Capturing {args.ref} ({args.source})")
    scheduled: VerificationJob | None = None
    with HospitalDatabase(args.db) as db:
        capture = db.save_snapshot(dataset)
        if (
            capture.change_set.status is ChangeSetStatus.CHANGES_DETECTED
            and SourceType(args.source) is not SourceType.WEBSITE
        ):
            scheduled = schedule_job_for_capture(db, capture)
    snapshot = capture.snapshot
    change_set = capture.change_set
    print(f"  snapshot:      {snapshot.snapshot_id}")
    print(f"  hospital:      {snapshot.hospital_id}")
    print(f"  captured_at:   {snapshot.captured_at.isoformat()}")
    print(f"  content_hash:  {snapshot.content_hash[:16]}…")
    print(f"  status:        {change_set.status.value}")
    if change_set.previous_snapshot_id:
        print(f"  previous:      {change_set.previous_snapshot_id}")
    if change_set.changes:
        print(
            f"  changes:       +{change_set.added} added, "
            f"-{change_set.removed} removed, "
            f"{change_set.modified} modified"
        )
        for change in change_set.changes[:20]:
            arrow = {
                "added": "+",
                "removed": "-",
                "modified": "~",
            }[change.change_type.value]
            print(f"    {arrow} {change.path}  {change.entity_name or ''}".rstrip())
        if len(change_set.changes) > 20:
            print(f"    … and {len(change_set.changes) - 20} more")
    if scheduled is not None:
        print(
            f"  verification job: {scheduled.verification_job_id}  "
            f"target {scheduled.target_source_type} ({scheduled.target_ref or '—'})"
        )
        print(
            "  next step: run `hospital-extractor worker` to perform the "
            "verification capture (background and explicit)"
        )
    return 0


def _run_snapshots(argv: list[str] | None = None) -> int:
    from hospital_extractor.storage import HospitalDatabase

    args = build_history_parser(
        "hospital-extractor snapshots",
        "List the snapshot history of one hospital.",
    ).parse_args(argv)
    with HospitalDatabase(args.db) as db:
        hospital_id = db.find_hospital_id(
            preferred=args.hospital,
            source_ref=args.hospital,
            hospital_name=args.hospital,
        )
        if hospital_id is None:
            print(f"error: unknown hospital {args.hospital!r}", file=sys.stderr)
            return 1
        snapshots = db.list_snapshots(hospital_id, source_type=args.source)
    print(f"Snapshots for {hospital_id}")
    print(f"  total: {len(snapshots)}")
    print()
    for snapshot in snapshots:
        print(
            f"  {snapshot.snapshot_id}  {snapshot.captured_at.isoformat()}  "
            f"{snapshot.source_type:<12} {snapshot.status:<8} "
            f"{snapshot.content_hash[:16]}  {snapshot.hospital_ref}"
        )
    return 0


def _run_changes(argv: list[str] | None = None) -> int:
    from hospital_extractor.storage import HospitalDatabase

    args = build_history_parser(
        "hospital-extractor changes",
        "Audit trail of change-detection runs for one hospital.",
    ).parse_args(argv)
    with HospitalDatabase(args.db) as db:
        hospital_id = db.find_hospital_id(
            preferred=args.hospital,
            source_ref=args.hospital,
            hospital_name=args.hospital,
        )
        if hospital_id is None:
            print(f"error: unknown hospital {args.hospital!r}", file=sys.stderr)
            return 1
        change_sets = db.list_change_sets(hospital_id, source_type=args.source)
    print(f"Change audit for {hospital_id}")
    print(f"  total change sets: {len(change_sets)}")
    print()
    for change_set in change_sets:
        print(
            f"  {change_set.change_set_id}  {change_set.detected_at.isoformat()}  "
            f"{change_set.status.value:<18} "
            f"added={change_set.added} removed={change_set.removed} "
            f"modified={change_set.modified}  snapshot={change_set.snapshot_id}"
        )
        for change in change_set.changes:
            arrow = {"added": "+", "removed": "-", "modified": "~"}[change.change_type.value]
            print(f"      {arrow} {change.path}  {change.entity_name or ''}".rstrip())
    return 0


def _run_hospitals(argv: list[str] | None = None) -> int:
    from hospital_extractor.storage import HospitalDatabase

    parser = argparse.ArgumentParser(
        prog="hospital-extractor hospitals",
        description="List canonical hospital identities in the registry.",
    )
    parser.add_argument(
        "--db",
        default=str(default_database_path()),
        help="Hospital store path (default: data/medcompare.db)",
    )
    args = parser.parse_args(argv)
    with HospitalDatabase(args.db) as db:
        hospitals = db.list_hospitals()
    print(f"Hospitals in registry ({len(hospitals)})")
    print()
    for hospital in hospitals:
        print(
            f"  {hospital.hospital_id}  {hospital.name or '(unnamed)':<40} "
            f"datasets={hospital.dataset_count} snapshots={hospital.snapshot_count} "
            f"sources={hospital.source_count}"
        )
    return 0


# ---------------------------------------------------------------------------
# Search / field comparison (P22)
# ---------------------------------------------------------------------------


def build_search_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hospital-extractor search",
        description=(
            "Structured search over stored hospital datasets, or a side-by-side "
            "field comparison of 2–4 stored hospitals (no ranking)."
        ),
    )
    parser.add_argument(
        "--text",
        default=None,
        help="Free-text match across hospital and entity names",
    )
    parser.add_argument(
        "--filter",
        action="append",
        nargs=3,
        metavar=("FIELD", "OP", "VALUE"),
        dest="filters",
        default=[],
        help=(
            "Field filter, e.g. --filter hospital.bed_count gte 500. "
            "Repeatable. OP: equals not_equals contains not_contains exists "
            "not_exists in not_in gt gte lt lte after before"
        ),
    )
    parser.add_argument(
        "--hospital",
        action="append",
        dest="hospital_ids",
        default=[],
        help="Restrict to a hospital id (repeatable)",
    )
    parser.add_argument(
        "--compare",
        nargs="+",
        dest="compare_ids",
        default=None,
        metavar="HOSPITAL_ID",
        help="Field-compare 2–4 stored hospital ids instead of searching",
    )
    parser.add_argument(
        "--fields",
        default=None,
        help="Comma-separated field paths for --compare (default: overview set)",
    )
    parser.add_argument("--limit", type=int, default=50, help="Max search results (1–500)")
    parser.add_argument("--offset", type=int, default=0, help="Search result offset")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Emit JSON instead of a human-readable table",
    )
    parser.add_argument(
        "--db",
        default=str(default_database_path()),
        help="Hospital store path (default: data/medcompare.db)",
    )
    return parser


def _run_search(argv: list[str] | None = None) -> int:
    from hospital_extractor.search import (
        ComparisonRequest,
        Filter,
        FilterOperator,
        HospitalNotFoundError,
        InvalidQueryError,
        Query,
        compare_stored_hospitals,
        search_datasets,
    )
    from hospital_extractor.storage import HospitalDatabase

    args = build_search_parser().parse_args(argv)

    try:
        with HospitalDatabase(args.db) as db:
            if args.compare_ids is not None:
                fields = (
                    [part.strip() for part in args.fields.split(",") if part.strip()]
                    if args.fields
                    else []
                )
                compare_result = compare_stored_hospitals(
                    db,
                    ComparisonRequest(hospital_ids=args.compare_ids, fields=fields),
                )
                if args.output_json:
                    print(compare_result.model_dump_json(indent=2))
                else:
                    _print_field_comparison(compare_result)
                return 0

            filters: list[Filter] = []
            for field, op, value in args.filters:
                operator = FilterOperator(op)
                coerced: object = value
                if operator in (FilterOperator.EXISTS, FilterOperator.NOT_EXISTS):
                    coerced = None
                elif op in (
                    FilterOperator.GT,
                    FilterOperator.GTE,
                    FilterOperator.LT,
                    FilterOperator.LTE,
                ):
                    try:
                        coerced = float(value) if "." in value else int(value)
                    except ValueError:
                        coerced = value
                filters.append(Filter(field=field, operator=operator, value=coerced))

            search_result = search_datasets(
                db,
                Query(
                    filters=filters,
                    hospital_ids=args.hospital_ids,
                    text=args.text,
                    limit=max(1, min(500, args.limit)),
                    offset=max(0, args.offset),
                ),
            )
    except (InvalidQueryError, HospitalNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.output_json:
        print(search_result.model_dump_json(indent=2))
    else:
        _print_search_result(search_result)
    return 0


def _print_search_result(result: object) -> None:
    from hospital_extractor.search import QueryResult

    assert isinstance(result, QueryResult)
    print(f"Matches: {result.total} (showing {len(result.matches)})")
    print()
    if not result.matches:
        print("  (no datasets matched)")
        return
    for match in result.matches:
        name = match.get("hospital_name") or match.get("hospital_ref") or "(unnamed)"
        print(f"  {name}")
        print(
            f"    dataset={match.get('dataset_id')}  "
            f"source={match.get('source_type')}  "
            f"provider={match.get('provider') or '—'}  "
            f"captured={match.get('captured_at')}"
        )
        for field in match.get("fields") or []:
            if field.get("unknown"):
                print(f"    {field.get('field')}: UNKNOWN")
            else:
                print(f"    {field.get('field')}: {field.get('value')}")
        print()


def _print_field_comparison(result: object) -> None:
    from hospital_extractor.search import ComparisonResult

    assert isinstance(result, ComparisonResult)
    print(result.note)
    print()
    headers = ["Field"] + [
        result.hospital_names.get(hid) or hid for hid in result.hospital_ids
    ]
    rows: list[list[str]] = []
    for field in result.fields:
        row = [field.label]
        for cell in field.cells:
            row.append("UNKNOWN" if cell.unknown else str(cell.value))
        rows.append(row)
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) if rows else len(headers[i])
        for i in range(len(headers))
    ]
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line)
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


# ---------------------------------------------------------------------------
# Verification subcommands (P19-B)
# ---------------------------------------------------------------------------


def build_verify_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hospital-extractor verify",
        description=(
            "Create a verification job for the hospital's most recent "
            "changes_detected change set (idempotent — running twice reuses "
            "the same job).  Nothing is executed here: the explicit `worker` "
            "command performs the verification capture."
        ),
    )
    parser.add_argument("hospital", help="hospital id (h_…), source ref, or hospital name")
    parser.add_argument(
        "--source",
        default="website",
        choices=SOURCE_CHOICES,
        help="Target source to verify against (default: website)",
    )
    parser.add_argument(
        "--db",
        default=str(default_database_path()),
        help="Hospital store path (default: data/medcompare.db)",
    )
    return parser


def _run_verify(argv: list[str] | None = None) -> int:
    from hospital_extractor.storage import HospitalDatabase
    from hospital_extractor.verification.service import create_manual_job

    args = build_verify_parser().parse_args(argv)
    with HospitalDatabase(args.db) as db:
        hospital_id = db.find_hospital_id(
            preferred=args.hospital,
            source_ref=args.hospital,
            hospital_name=args.hospital,
        )
        if hospital_id is None:
            print(f"error: unknown hospital {args.hospital!r}", file=sys.stderr)
            return 1
        job, reason = create_manual_job(db, hospital_id, target_source_type=args.source)
    if job is None:
        print(f"verify {hospital_id}: {reason}")
        return 1
    print(f"verification job: {job.verification_job_id}")
    print(f"  hospital:   {job.hospital_id}")
    print(f"  change set: {job.change_set_id}")
    print(f"  target:     {job.target_source_type} ({job.target_ref or '—'})")
    print(f"  status:     {job.status.value}")
    print()
    print("Next: run `hospital-extractor worker` to perform the verification capture.")
    return 0


def build_verification_jobs_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hospital-extractor verification-jobs",
        description="List verification jobs and their latest results for one hospital.",
    )
    parser.add_argument("hospital", help="hospital id (h_…), source ref, or hospital name")
    parser.add_argument(
        "--db",
        default=str(default_database_path()),
        help="Hospital store path (default: data/medcompare.db)",
    )
    return parser


def _run_verification_jobs(argv: list[str] | None = None) -> int:
    from hospital_extractor.storage import HospitalDatabase

    args = build_verification_jobs_parser().parse_args(argv)
    with HospitalDatabase(args.db) as db:
        hospital_id = db.find_hospital_id(
            preferred=args.hospital,
            source_ref=args.hospital,
            hospital_name=args.hospital,
        )
        if hospital_id is None:
            print(f"error: unknown hospital {args.hospital!r}", file=sys.stderr)
            return 1
        rows = [
            (job, db.get_verification_result(job.verification_job_id))
            for job in db.list_verification_jobs(hospital_id)
        ]
    print(f"Verification jobs for {hospital_id}")
    print(f"  total: {len(rows)}")
    print()
    for job, result in rows:
        print(
            f"  {job.verification_job_id}  {job.status.value:<10} "
            f"target={job.target_source_type:<10} attempts={job.attempt_count}"
        )
        print(
            f"      change_set={job.change_set_id}  "
            f"ref={job.target_ref or '—'}"
        )
        if job.last_error:
            print(f"      last_error={job.last_error}")
        if result is not None:
            counts = ", ".join(
                f"{name}={count}"
                for name, count in (
                    ("verified", result.verified_count),
                    ("mismatch", result.mismatch_count),
                    ("unverified", result.unverified_count),
                )
            )
            print(
                f"      result={result.status.value:<10} checked_at="
                f"{result.checked_at.isoformat()}  {counts}"
            )
    return 0


def build_worker_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hospital-extractor worker",
        description=(
            "The explicit verification worker.  It runs the verification "
            "captures for due jobs — crawling/querying the target sources — so "
            "it is never started by the web UI or API; you must run it "
            "deliberately."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process every currently-due job once, then exit",
    )
    parser.add_argument(
        "--db",
        default=str(default_database_path()),
        help="Hospital store path (default: data/medcompare.db)",
    )
    parser.add_argument(
        "--delay-minutes",
        type=int,
        default=None,
        help=(
            "Verification delay in minutes after a change is detected "
            "(default: 0; use a positive value in production)"
        ),
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help="Max failed attempts before a job is marked FAILED (default: 3)",
    )
    parser.add_argument(
        "--backoff-minutes",
        type=int,
        default=None,
        help="Retry backoff base in minutes (backoff = base x attempt) (default: 2)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Continuous-mode poll interval in seconds (default: 10)",
    )
    parser.add_argument("--log-level", default="WARNING", help="Log level (default: WARNING)")
    return parser


async def _run_worker(argv: list[str] | None = None) -> int:
    from hospital_extractor.ops.signals import install_worker_signal_handlers
    from hospital_extractor.storage import HospitalDatabase
    from hospital_extractor.verification.service import run_worker_cycle, run_worker_forever
    from hospital_extractor.verification.settings import verification_settings

    args = build_worker_parser().parse_args(argv)
    configure_logging(args.log_level)
    overrides: dict[str, object] = {}
    if args.delay_minutes is not None:
        overrides["delay_minutes"] = args.delay_minutes
    if args.max_attempts is not None:
        overrides["max_attempts"] = args.max_attempts
    if args.backoff_minutes is not None:
        overrides["backoff_minutes"] = args.backoff_minutes
    if args.interval is not None:
        overrides["worker_interval_seconds"] = args.interval
    settings = verification_settings(**overrides)

    if args.once:
        with HospitalDatabase(args.db) as db:
            processed = await run_worker_cycle(db, settings=settings)
        print(f"worker: processed {processed} due verification job(s)")
        return 0

    stop_event = asyncio.Event()
    install_worker_signal_handlers(
        stop_event, on_signal=lambda name: print(f"worker: received {name}, shutting down")
    )
    print(
        f"verification worker running (polling every {settings.worker_interval_seconds}s; "
        "SIGINT/SIGTERM to stop)"
    )
    with HospitalDatabase(args.db) as db:
        await run_worker_forever(db, settings=settings, stop_event=stop_event)
    print("verification worker stopped cleanly")
    return 0


def build_verification_demo_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hospital-extractor verification-demo",
        description=(
            "SIMULATED end-to-end verification + reconciliation demo.  "
            "Scenario A (VERIFIED): the demo API gains a procedure and the "
            "simulated website already lists it — no case.  Scenario B "
            "(MISMATCH): the demo API reports new bed counts while the "
            "simulated website is unchanged — a reconciliation case opens, "
            "is reviewed and resolved.  Scenario C (UNVERIFIED): the website "
            "cannot confirm a new procedure — an opt-in reviewable case is "
            "opened.  Fully local — no internet, no server, no real hospitals."
        ),
    )
    parser.add_argument(
        "--db",
        default=str(default_database_path()),
        help="Hospital store path (default: data/medcompare.db)",
    )
    parser.add_argument(
        "--hospitals",
        nargs="+",
        default=None,
        help="Demo hospital ids (default: demo-northstar demo-solstice)",
    )
    return parser


def _run_verification_demo(argv: list[str] | None = None) -> int:
    from hospital_extractor.verification.demo import run_verification_demo

    args = build_verification_demo_parser().parse_args(argv)
    print("verification-demo (SIMULATED, fully local)")
    print("=" * 66)
    results = run_verification_demo(args.db, hospital_ids=args.hospitals)
    print()
    for result in results:
        status = result.result_status or result.job_status
        print(
            f"  Scenario {result.label}: {result.hospital_id}  "
            f"-> {status}  (job {result.job_id})"
        )
        for path in result.checked_changes:
            print(f"      checked change: {path}")
        if result.reconciliation_case_id:
            print(
                f"      reconciliation: {result.reconciliation_case_id} "
                f"[{result.reconciliation_status}], trigger={result.reconciliation_trigger}, "
                f"resolution={result.reconciliation_resolution or '—'}, "
                f"events={result.reconciliation_events}"
            )
        else:
            print("      reconciliation: none (no review required)")
    print()
    print("All scenarios simulated. Verified against SIMULATED website datasets;")
    print("nothing crawled, no authorized endpoint, no private data.")
    return 0


# ---------------------------------------------------------------------------
# Reconciliation subcommands (P19-C)
# ---------------------------------------------------------------------------


def _reconciliation_db_parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument(
        "--db",
        default=str(default_database_path()),
        help="Hospital store path (default: data/medcompare.db)",
    )
    return parser


def _run_reconciliation_cases(argv: list[str] | None = None) -> int:
    from hospital_extractor.reconciliation.service import list_cases
    from hospital_extractor.storage import HospitalDatabase

    parser = _reconciliation_db_parser(
        "hospital-extractor reconciliation-cases",
        "List the reconciliation cases of one hospital.",
    )
    parser.add_argument("hospital", help="hospital id (h_…), source ref, or hospital name")
    parser.add_argument(
        "--status",
        default=None,
        help="Narrow to one status (open, under_review, resolved, reopened, dismissed)",
    )
    args = parser.parse_args(argv)
    with HospitalDatabase(args.db) as db:
        hospital_id = db.find_hospital_id(
            preferred=args.hospital,
            source_ref=args.hospital,
            hospital_name=args.hospital,
        )
        if hospital_id is None:
            print(f"error: unknown hospital {args.hospital!r}", file=sys.stderr)
            return 1
        cases = list_cases(db, hospital_id=hospital_id, status=args.status)
    print(f"Reconciliation cases for {hospital_id}")
    print(f"  total: {len(cases)}")
    print()
    for case in cases:
        print(
            f"  {case.reconciliation_case_id}  {case.status.value:<12} "
            f"trigger={case.trigger.value:<10} items={case.item_count}"
        )
        print(
            f"      change_set={case.change_set_id}  "
            f"result={case.verification_result_id}"
        )
        if case.assigned_to:
            print(f"      assigned_to={case.assigned_to}")
        if case.resolution:
            print(f"      resolution={case.resolution.value}: {case.resolution_reason}")
    return 0


def _run_reconciliation(argv: list[str] | None = None) -> int:
    from hospital_extractor.reconciliation.service import (
        build_evidence_package,
        get_case,
        list_events,
    )
    from hospital_extractor.storage import HospitalDatabase

    parser = _reconciliation_db_parser(
        "hospital-extractor reconciliation",
        "Show one reconciliation case with its evidence and audit trail.",
    )
    parser.add_argument("case_id", help="reconciliation case id (rec_…)")
    args = parser.parse_args(argv)
    with HospitalDatabase(args.db) as db:
        case = get_case(db, args.case_id)
        if case is None:
            print(f"error: unknown reconciliation case {args.case_id!r}", file=sys.stderr)
            return 1
        evidence = build_evidence_package(db, case)
        change_set = evidence.get("change_set")
        result = evidence.get("result")
        events = list_events(db, case.reconciliation_case_id)
    print(f"{case.reconciliation_case_id}  status={case.status.value}")
    print(f"  hospital:      {case.hospital_id}")
    print(f"  trigger:       {case.trigger.value}  (created_by={case.created_by})")
    print(f"  change_set:    {case.change_set_id}")
    print(f"  verification:  {case.verification_job_id} -> {case.verification_result_id}")
    print(
        f"  created:       {case.created_at.isoformat()}  updated: "
        f"{case.updated_at.isoformat()}"
    )
    if change_set is not None:
        print(f"  change set:    status={change_set.get('status')}")
        print(
            f"    changes detected on {change_set.get('detected_at')}"
        )
    if result is not None:
        print(
            f"  result:        {result.get('status')}  checked "
            f"{result.get('checked_at')}"
        )
    if case.assigned_to:
        print(f"  assigned_to:   {case.assigned_to}")
    if case.resolution:
        print(f"  resolution:    {case.resolution.value}")
        print(f"  reason:        {case.resolution_reason}")
        if case.resolved_at is not None:
            print(f"  resolved_at:   {case.resolved_at.isoformat()}")
    print()
    print("  Items:")
    for item in case.items:
        print(
            f"    {item.path}  {item.change_type.value:<9} "
            f"outcome={item.outcome.value:<10} "
            f"auth={item.authoritative_value!r} website={item.verification_value!r}"
        )
    print()
    print("  Audit trail:")
    for event in events:
        transition = (
            f"{event.from_status.value} -> {event.to_status.value}"
            if event.from_status and event.to_status
            else f"-> {event.to_status.value if event.to_status else '—'}"
        )
        print(
            f"    {event.created_at.isoformat()}  {event.action:<14} "
            f"actor={event.actor:<8} {transition}  {event.reason or ''}"
        )
    return 0


def _run_reconciliation_action(argv: list[str] | None, action: str) -> int:
    from hospital_extractor.reconciliation.service import (
        dismiss_case,
        reopen_case,
        resolve_case,
        start_review,
    )
    from hospital_extractor.storage import HospitalDatabase

    parser = _reconciliation_db_parser(
        f"hospital-extractor {action}",
        f"Advance a reconciliation case ({action}).",
    )
    parser.add_argument("case_id", help="reconciliation case id (rec_…)")
    if action == "resolve":
        parser.add_argument(
            "--resolution",
            required=True,
            choices=(
                "authoritative_source_confirmed",
                "website_confirmed",
                "source_disagreement",
                "insufficient_evidence",
                "false_positive",
            ),
            help="Recorded resolution type",
        )
    parser.add_argument("--reason", required=True, help="Why this action")
    parser.add_argument("--assigned-to", default=None, help="Reviewer (for review)")
    args = parser.parse_args(argv)

    with HospitalDatabase(args.db) as db:
        try:
            if action == "review":
                case = start_review(
                    db,
                    args.case_id,
                    actor="manual",
                    assigned_to=args.assigned_to,
                    reason=args.reason,
                )
            elif action == "resolve":
                case = resolve_case(
                    db,
                    args.case_id,
                    resolution=args.resolution,
                    reason=args.reason,
                    actor="manual",
                )
            elif action == "dismiss":
                case = dismiss_case(db, args.case_id, reason=args.reason, actor="manual")
            else:  # reopen
                case = reopen_case(db, args.case_id, reason=args.reason, actor="manual")
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    print(
        f"{case.reconciliation_case_id} -> {case.status.value} "
        f"via `{action}` (actor=manual)"
    )
    return 0


def _run_review(argv: list[str] | None = None) -> int:
    return _run_reconciliation_action(argv, "review")


def _run_resolve(argv: list[str] | None = None) -> int:
    return _run_reconciliation_action(argv, "resolve")


def _run_dismiss(argv: list[str] | None = None) -> int:
    return _run_reconciliation_action(argv, "dismiss")


def _run_reopen(argv: list[str] | None = None) -> int:
    return _run_reconciliation_action(argv, "reopen")


# ---------------------------------------------------------------------------
# Monitoring subcommands (P20)
# ---------------------------------------------------------------------------


def _monitoring_db_parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument(
        "--db",
        default=str(default_database_path()),
        help="Hospital store path (default: data/medcompare.db)",
    )
    return parser


def _print_policy(policy: object) -> None:
    from hospital_extractor.monitoring.models import MonitoringPolicy

    assert isinstance(policy, MonitoringPolicy)
    enabled = "yes" if policy.enabled else "no"
    print(
        f"  {policy.policy_id}  {policy.status.value:<8} "
        f"{policy.frequency.value:<8} enabled={enabled:<3} "
        f"hospital={policy.hospital_id}"
    )
    print(
        f"      source={policy.source_type} target={policy.target_ref or '—'} "
        f"next_run={policy.next_run_at.isoformat() if policy.next_run_at else '—'}"
    )
    if policy.last_success_at:
        print(f"      last_success={policy.last_success_at.isoformat()}")
    if policy.consecutive_failures:
        print(
            f"      failures={policy.consecutive_failures} "
            f"last_error={policy.last_error or '—'}"
        )


def _run_monitoring(argv: list[str] | None = None) -> int:
    from hospital_extractor.monitoring.models import ScheduleFrequency
    from hospital_extractor.monitoring.service import (
        create_policy,
        list_policies,
        set_policy_enabled,
    )
    from hospital_extractor.storage import HospitalDatabase

    parser = argparse.ArgumentParser(
        prog="hospital-extractor monitoring",
        description=(
            "Continuous monitoring (P20): list / add / enable / disable / run "
            "scheduled capture policies, or run the explicit monitoring worker."
        ),
    )
    sub = parser.add_subparsers(dest="action", required=True)

    p_list = sub.add_parser("list", help="List monitoring policies")
    p_list.add_argument("--hospital", default=None, help="Filter by hospital id")
    p_list.add_argument("--db", default=str(default_database_path()))

    p_add = sub.add_parser("add", help="Create or update a monitoring policy")
    p_add.add_argument("hospital", help="hospital id (h_…), source ref, or name")
    p_add.add_argument(
        "--frequency",
        required=True,
        choices=[f.value for f in ScheduleFrequency],
        help="Fixed UTC schedule",
    )
    p_add.add_argument("--source", default="website", choices=SOURCE_CHOICES)
    p_add.add_argument("--target-ref", default="", help="Target URL/ref to capture")
    p_add.add_argument("--db", default=str(default_database_path()))

    p_enable = sub.add_parser("enable", help="Enable a policy (resets FAILED)")
    p_enable.add_argument("policy_id")
    p_enable.add_argument("--db", default=str(default_database_path()))

    p_disable = sub.add_parser("disable", help="Disable a policy")
    p_disable.add_argument("policy_id")
    p_disable.add_argument("--db", default=str(default_database_path()))

    p_run = sub.add_parser("run", help="Trigger one policy immediately")
    p_run.add_argument("policy_id")
    p_run.add_argument("--db", default=str(default_database_path()))

    p_worker = sub.add_parser(
        "worker",
        help="Explicit monitoring worker (never started by the web UI/API)",
    )
    p_worker.add_argument("--once", action="store_true", help="One cycle then exit")
    p_worker.add_argument("--interval", type=float, default=None, help="Poll seconds")
    p_worker.add_argument("--db", default=str(default_database_path()))

    args = parser.parse_args(argv)

    if args.action == "list":
        with HospitalDatabase(args.db) as db:
            policies = list_policies(db, hospital_id=args.hospital)
        print(f"Monitoring policies ({len(policies)})")
        for policy in policies:
            _print_policy(policy)
        return 0

    if args.action == "add":
        with HospitalDatabase(args.db) as db:
            hospital_id = db.find_hospital_id(
                preferred=args.hospital,
                source_ref=args.hospital,
                hospital_name=args.hospital,
            )
            if hospital_id is None:
                print(f"error: unknown hospital {args.hospital!r}", file=sys.stderr)
                return 1
            policy = create_policy(
                db,
                hospital_id=hospital_id,
                frequency=args.frequency,
                source_type=args.source,
                target_ref=args.target_ref,
            )
        print(f"monitoring policy: {policy.policy_id}")
        _print_policy(policy)
        print()
        print("Next: run `hospital-extractor monitoring worker` to execute due policies.")
        return 0

    if args.action == "enable":
        with HospitalDatabase(args.db) as db:
            enabled_policy = set_policy_enabled(db, args.policy_id, enabled=True)
        if enabled_policy is None:
            print(f"error: unknown policy {args.policy_id!r}", file=sys.stderr)
            return 1
        print(f"enabled {enabled_policy.policy_id}")
        return 0

    if args.action == "disable":
        with HospitalDatabase(args.db) as db:
            disabled = set_policy_enabled(db, args.policy_id, enabled=False)
        if disabled is None:
            print(f"error: unknown policy {args.policy_id!r}", file=sys.stderr)
            return 1
        print(f"disabled {disabled.policy_id}")
        return 0

    if args.action == "run":
        from hospital_extractor.monitoring.service import run_policy_now

        async def _run_once() -> int:
            with HospitalDatabase(args.db) as db:
                policy = await run_policy_now(db, args.policy_id)
            if policy is None:
                print(f"error: unknown policy {args.policy_id!r}", file=sys.stderr)
                return 1
            print(f"ran {policy.policy_id}: status={policy.status.value}")
            if policy.last_orchestration_run_id:
                print(f"  orchestration_run={policy.last_orchestration_run_id}")
            if policy.last_change_set_status:
                print(f"  change_set_status={policy.last_change_set_status}")
            return 0

        return asyncio.run(_run_once())

    if args.action == "worker":
        from hospital_extractor.monitoring.service import (
            run_monitoring_cycle,
            run_monitoring_worker,
        )
        from hospital_extractor.monitoring.settings import monitoring_settings
        from hospital_extractor.ops.signals import install_worker_signal_handlers

        overrides: dict[str, object] = {}
        if args.interval is not None:
            overrides["worker_interval_seconds"] = args.interval
        settings = monitoring_settings(**overrides)

        if args.once:
            with HospitalDatabase(args.db) as db:
                processed = asyncio.run(run_monitoring_cycle(db, settings=settings))
            print(f"monitoring worker: processed {processed} due polic(y/ies)")
            return 0

        stop_event = asyncio.Event()
        install_worker_signal_handlers(
            stop_event,
            on_signal=lambda name: print(f"monitoring worker: received {name}, shutting down"),
        )
        print(
            f"monitoring worker running (polling every "
            f"{settings.worker_interval_seconds}s; SIGINT/SIGTERM to stop)"
        )

        async def _forever() -> None:
            with HospitalDatabase(args.db) as db:
                await run_monitoring_worker(db, settings=settings, stop_event=stop_event)

        try:
            asyncio.run(_forever())
            print("monitoring worker stopped cleanly")
        except KeyboardInterrupt:
            print("monitoring worker stopped")
        return 0

    parser.error(f"unknown action {args.action!r}")
    return 2


# ---------------------------------------------------------------------------
# Reporting subcommands (P19-D)
# ---------------------------------------------------------------------------


def _reporting_db_parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument(
        "--db",
        default=str(default_database_path()),
        help="Hospital store path (default: data/medcompare.db)",
    )
    return parser


def _run_report(argv: list[str] | None = None) -> int:
    from hospital_extractor.reconciliation.service import get_case
    from hospital_extractor.reporting.formatters import format_report
    from hospital_extractor.reporting.service import generate_report
    from hospital_extractor.storage import HospitalDatabase

    parser = _reporting_db_parser(
        "hospital-extractor report",
        "Generate a report for a reconciliation case.",
    )
    parser.add_argument("case_id", help="reconciliation case id (rec_…)")
    parser.add_argument(
        "--type",
        choices=("reconciliation_report", "verification_report", "change_report"),
        default="reconciliation_report",
        help="Report type (default: reconciliation_report)",
    )
    parser.add_argument(
        "--format",
        choices=("json", "html", "markdown"),
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument(
        "--generated-by",
        default="manual",
        help="Actor who generated the report (default: manual)",
    )
    args = parser.parse_args(argv)

    with HospitalDatabase(args.db) as db:
        case = get_case(db, args.case_id)
        if case is None:
            print(f"error: unknown reconciliation case {args.case_id!r}", file=sys.stderr)
            return 1
        from hospital_extractor.reporting.models import ReportType

        report = generate_report(
            db,
            case,
            report_type=ReportType(args.type),
            format=args.format,
            generated_by=args.generated_by,
        )
        db.save_report(report)
        print(f"Generated report: {report.report_id}")
        print(f"  case:          {case.reconciliation_case_id}")
        print(f"  hospital:      {case.hospital_id}")
        print(f"  type:          {report.report_type.value}")
        print(f"  format:        {report.format}")
        print(f"  generated_at:  {report.generated_at.isoformat()}")
        print()
        print(format_report(report, args.format))
    return 0


def _run_reports(argv: list[str] | None = None) -> int:
    from hospital_extractor.storage import HospitalDatabase

    parser = _reporting_db_parser(
        "hospital-extractor reports",
        "List reports for a hospital.",
    )
    parser.add_argument("hospital", help="hospital id (h_…), source ref, or hospital name")
    parser.add_argument(
        "--status",
        default=None,
        help="Narrow to one status (generated, ready, submitted, failed, cancelled)",
    )
    args = parser.parse_args(argv)

    with HospitalDatabase(args.db) as db:
        hospital_id = db.find_hospital_id(
            preferred=args.hospital,
            source_ref=args.hospital,
            hospital_name=args.hospital,
        )
        if hospital_id is None:
            print(f"error: unknown hospital {args.hospital!r}", file=sys.stderr)
            return 1
        reports = db.list_reports(hospital_id=hospital_id, status=args.status)
    print(f"Reports for {hospital_id}")
    print(f"  total: {len(reports)}")
    print()
    for report in reports:
        print(
            f"  {report.report_id}  {report.status.value:<10} "
            f"type={report.report_type.value:<20} "
            f"format={report.format:<10} "
            f"case={report.reconciliation_case_id or '—'}"
        )
    return 0


def _run_submit(argv: list[str] | None = None) -> int:
    import asyncio

    from hospital_extractor.reporting.government import MockGovernmentProvider
    from hospital_extractor.reporting.models import NotificationConfig, NotificationProviderType
    from hospital_extractor.reporting.providers import LocalProvider, MockProvider
    from hospital_extractor.storage import HospitalDatabase

    parser = _reporting_db_parser(
        "hospital-extractor submit",
        "Submit a report to an external provider.",
    )
    parser.add_argument("report_id", help="report id (rpt_…)")
    parser.add_argument(
        "--provider",
        choices=("local", "mock", "government_portal"),
        default="mock",
        help="Provider to submit to (default: mock)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and show what would be sent, but make no external calls",
    )
    args = parser.parse_args(argv)

    with HospitalDatabase(args.db) as db:
        report = db.get_report(args.report_id)
        if report is None:
            print(f"error: unknown report {args.report_id!r}", file=sys.stderr)
            return 1

        provider_type = NotificationProviderType(args.provider)
        provider: LocalProvider | MockProvider | MockGovernmentProvider
        if provider_type == NotificationProviderType.LOCAL:
            provider = LocalProvider()
        elif provider_type == NotificationProviderType.MOCK:
            provider = MockProvider()
        else:
            provider = MockGovernmentProvider()

        config = NotificationConfig(provider_type=provider_type, enabled=True)

        try:
            provider.validate_configuration(config)
        except Exception as exc:
            print(f"error: provider not configured: {exc}", file=sys.stderr)
            return 1

        submission = asyncio.run(provider.submit(report, config, dry_run=args.dry_run))
        db.save_submission(submission)

        print(f"Submission: {submission.submission_id}")
        print(f"  report:      {report.report_id}")
        print(f"  provider:    {submission.provider_type.value}")
        print(f"  status:      {submission.status.value}")
        if args.dry_run:
            print("  mode:        DRY RUN (no external mutation)")
        else:
            submitted_at = (
                submission.submitted_at.isoformat()
                if submission.submitted_at
                else "—"
            )
            print(f"  submitted:   {submitted_at}")
            print(f"  reference:   {submission.external_reference or '—'}")
        if submission.error:
            print(f"  error:       {submission.error}")
    return 0


def _run_submissions(argv: list[str] | None = None) -> int:
    from hospital_extractor.storage import HospitalDatabase

    parser = _reporting_db_parser(
        "hospital-extractor submissions",
        "List submissions for a report.",
    )
    parser.add_argument("report_id", help="report id (rpt_…)")
    args = parser.parse_args(argv)

    with HospitalDatabase(args.db) as db:
        report = db.get_report(args.report_id)
        if report is None:
            print(f"error: unknown report {args.report_id!r}", file=sys.stderr)
            return 1
        submissions = db.list_submissions(report_id=args.report_id)

    print(f"Submissions for report {report.report_id}")
    print(f"  total: {len(submissions)}")
    print()
    for sub in submissions:
        print(
            f"  {sub.submission_id}  {sub.status.value:<12} "
            f"provider={sub.provider_type.value:<20} "
            f"dry_run={'yes' if sub.dry_run else 'no'}"
        )
        if sub.external_reference:
            print(f"      ref: {sub.external_reference}")
        if sub.error:
            print(f"      error: {sub.error}")
    return 0


# ---------------------------------------------------------------------------
# Source / connector introspection (P21)
# ---------------------------------------------------------------------------


def _run_sources(argv: list[str] | None = None) -> int:
    from hospital_extractor.sources.registry import (
        get_default_registry,
        load_sources_config,
    )

    parser = argparse.ArgumentParser(
        prog="hospital-extractor sources",
        description="List configured sources and their authorization status.",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="List configured sources from config/sources.toml")
    sub.add_parser("definitions", help="List registered source type definitions")
    args = parser.parse_args(argv)

    registry = get_default_registry()
    if args.action == "definitions":
        print("Source type definitions")
        for definition in registry.definitions():
            auth = "requires_authorization" if definition.requires_authorization else "open"
            sim = "SIMULATED" if definition.simulated else "real"
            print(f"  {definition.source_type.value:<14} {sim:<10} {auth}")
        return 0

    configs = load_sources_config()
    print(f"Configured sources ({len(configs)})")
    if not configs:
        print("  (none — add entries to config/sources.toml)")
        return 0
    for config in configs:
        definition = registry.definition(config.source_type)
        if definition.requires_authorization and not config.authorized:
            status = "UNAUTHORIZED"
        elif definition.requires_authorization:
            status = "AUTHORIZED"
        elif definition.simulated:
            status = "SIMULATED / MOCK"
        else:
            status = "PUBLIC"
        sim = " (mock)" if definition.simulated else ""
        print(f"  {config.name or config.source_type:<24} type={config.source_type}{sim}")
        print(f"    ref={config.hospital_ref}  status={status}")
        if config.base_url:
            print(f"    base_url={config.base_url}")
        if config.credential_env:
            print(f"    credential_ref=env:{config.credential_env} (value never shown)")
        if config.allowed_hosts:
            print(f"    allowed_hosts={', '.join(config.allowed_hosts)}")
    return 0


def _run_connectors(argv: list[str] | None = None) -> int:
    from hospital_extractor.sources.registry import get_default_registry

    parser = argparse.ArgumentParser(
        prog="hospital-extractor connectors",
        description=(
            "List connector classes and optionally run a safe offline "
            "authorization/config check for a configured source."
        ),
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="List registered connectors")
    p_test = sub.add_parser(
        "test",
        help=(
            "Authorization/config check for a source type (offline by default; "
            "never fetches unless the source is authorized and --fetch is set)"
        ),
    )
    p_test.add_argument("source_type", choices=SOURCE_CHOICES)
    p_test.add_argument("--ref", default="", help="hospital_ref / target ref")
    p_test.add_argument(
        "--fetch",
        action="store_true",
        help="Perform acquisition (authorized only)",
    )
    args = parser.parse_args(argv)

    registry = get_default_registry()
    if args.action == "list":
        print("Connectors")
        for source_type in registry.source_types():
            try:
                connector = registry.connector_for(source_type)
                name = connector.__name__
            except KeyError:
                name = "(unresolved)"
            definition = registry.definition(source_type)
            status = (
                "UNAUTHORIZED"
                if definition.requires_authorization
                else ("SIMULATED / MOCK" if definition.simulated else "PUBLIC")
            )
            print(f"  {source_type:<14} -> {name:<28} {status}")
        return 0

    # connector test
    from hospital_extractor.sources.registry import SourceConfig

    definition = registry.definition(args.source_type)
    config = SourceConfig(
        source_type=args.source_type,
        hospital_ref=args.ref or "probe",
        authorized=not definition.requires_authorization,
    )
    policy = config.authorization_policy(
        requires_authorization=definition.requires_authorization
    )
    credential_label = (
        policy.credential_ref.describe() if policy.credential_ref else "none"
    )
    print(f"connector test: {args.source_type}")
    print(f"  connector:     {registry.connector_for(args.source_type).__name__}")
    print(f"  authorization: {policy.status}")
    print(f"  simulated:     {'yes' if definition.simulated else 'no'}")
    print(f"  credential:    {credential_label}")
    if definition.requires_authorization and not config.authorized:
        print("  result:        UNAUTHORIZED — fail closed (no external request)")
        return 1
    if not args.fetch:
        print("  result:        OK (offline check; pass --fetch to acquire)")
        return 0
    if definition.requires_authorization and policy.status != "AUTHORIZED":
        print("  result:        UNAUTHORIZED — refusing fetch")
        return 1

    # Explicit fetch of an authorized source (never arbitrary URLs — only the
    # configured source type + ref supplied by the operator).
    ref = args.ref
    if not ref:
        print("  result:        INVALID_CONFIGURATION — --ref required for fetch")
        return 2
    try:
        source = registry.build(
            SourceConfig(
                source_type=args.source_type,
                hospital_ref=ref,
                authorized=True,
                allowed_hosts=tuple(_default_hosts_for_ref(ref)),
                allow_private_hosts=args.source_type == "mock_api",
            )
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  result:        INVALID_CONFIGURATION — {exc}")
        return 2
    try:
        dataset = asyncio.run(source.fetch())
    except Exception as exc:  # noqa: BLE001
        print(f"  result:        FAILED — {exc}")
        return 1
    hospital = dataset.hospital
    acquired_name = (hospital.name if hospital and hospital.name else None) or "(unnamed)"
    print(f"  result:        OK — acquired {acquired_name!r}")
    return 0


def _default_hosts_for_ref(ref: str) -> list[str]:
    from urllib.parse import urlparse

    host = urlparse(ref).hostname
    return [host] if host else []


# ---------------------------------------------------------------------------
# Quality / observability reports (P23)
# ---------------------------------------------------------------------------


def _run_quality_report(argv: list[str] | None = None) -> int:
    from hospital_extractor.quality import (
        DatasetQualityReport,
        HospitalQualityNotFoundError,
        HospitalQualityReport,
        PipelineQualityReport,
        build_dataset_quality_report,
        build_hospital_quality_report,
        build_pipeline_quality_report,
        format_quality_json,
        format_quality_markdown,
    )
    from hospital_extractor.storage import HospitalDatabase

    parser = argparse.ArgumentParser(
        prog="hospital-extractor quality-report",
        description=(
            "Data-quality & observability report from stored rows only. "
            "No overall score is ever computed."
        ),
    )
    parser.add_argument(
        "--scope",
        choices=("pipeline", "hospital", "dataset"),
        default="pipeline",
        help="Report scope (default: pipeline)",
    )
    parser.add_argument(
        "--id",
        default=None,
        help="Hospital id (h_…) or dataset id, depending on --scope",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument(
        "--db",
        default=str(default_database_path()),
        help="Hospital store path (default: data/medcompare.db)",
    )
    args = parser.parse_args(argv)

    with HospitalDatabase(args.db) as db:
        report: (
            PipelineQualityReport | HospitalQualityReport | DatasetQualityReport
        )
        try:
            if args.scope == "pipeline":
                report = build_pipeline_quality_report(db)
            elif args.scope == "hospital":
                if not args.id:
                    print("error: --id is required for --scope hospital", file=sys.stderr)
                    return 1
                report = build_hospital_quality_report(db, args.id)
            else:
                if not args.id:
                    print("error: --id is required for --scope dataset", file=sys.stderr)
                    return 1
                summary = next(
                    (s for s in db.list_datasets() if s.dataset_id == args.id),
                    None,
                )
                if summary is None:
                    print(f"error: unknown dataset {args.id!r}", file=sys.stderr)
                    return 1
                report = build_dataset_quality_report(db, summary)
        except HospitalQualityNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    if args.format == "markdown":
        print(format_quality_markdown(report))
    else:
        print(format_quality_json(report))
    return 0


# ---------------------------------------------------------------------------
# Demo seed subcommand
# ---------------------------------------------------------------------------


def build_demo_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hospital-extractor demo",
        description=(
            "Demo environment tools. `demo seed` builds a clean deterministic "
            "demo SQLite database with fictional DEMO hospitals and users. "
            "Passwords come only from environment variables. `demo check` runs "
            "the offline YAML fixture validation demo."
        ),
    )
    sub = parser.add_subparsers(dest="action", required=True)
    p_seed = sub.add_parser("seed", help="Seed (or reset) the demo database")
    p_seed.add_argument(
        "--db",
        default="data/demo/medcompare-demo.db",
        help="Demo database path (default: data/demo/medcompare-demo.db)",
    )
    p_seed.add_argument(
        "--reset",
        action="store_true",
        help="Delete the existing demo DB file first (never touches data/medcompare.db)",
    )
    p_seed.add_argument(
        "--json",
        action="store_true",
        help="Print the seed summary as JSON",
    )
    p_check = sub.add_parser(
        "check",
        help="Run the offline YAML fixture validation demo (PASS/FAIL)",
    )
    p_check.add_argument(
        "--fixtures",
        default="tests/fixtures/medcompare_demo",
        help="Fixture directory (default: tests/fixtures/medcompare_demo)",
    )
    p_check.add_argument(
        "--json",
        action="store_true",
        help="Print the computed result as JSON",
    )
    return parser


def _run_demo(argv: list[str] | None = None) -> int:
    args = build_demo_parser().parse_args(argv)
    if args.action == "check":
        from pathlib import Path as _FixturePath

        from hospital_extractor.demo.yaml_demo import run_yaml_demo

        try:
            result = run_yaml_demo(_FixturePath(args.fixtures))
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if args.json:
            import json as _json

            print(
                _json.dumps(
                    {
                        "baseline_hospitals": result.baseline_count,
                        "current_hospitals": result.current_count,
                        "added": result.added,
                        "modified": result.modified,
                        "unchanged": result.unchanged,
                        "removed": result.removed,
                        "unknown_count": result.unknown_count,
                        "provenance_records": result.provenance_count,
                        "conflict_detected": result.conflict_detected,
                        "conflicts": result.conflict_descriptions,
                        "quality": result.quality,
                        "failures": result.failures,
                        "result": result.result_label,
                    },
                    indent=2,
                )
            )
        else:
            print(result.render())
        return 0 if result.passed else 1
    if args.action != "seed":
        print(f"error: unknown demo action {args.action!r}", file=sys.stderr)
        return 2
    from pathlib import Path as _Path

    from hospital_extractor.demo.seed import DEMO_DB_PATH, SeedError, seed_demo

    target = _Path(args.db)
    # Guard: never destroy the real development store by accident.
    if args.reset and target.resolve() == _Path("data/medcompare.db").resolve():
        print("error: refusing to reset the real development database", file=sys.stderr)
        return 1
    try:
        summary = seed_demo(target, reset=args.reset)
    except SeedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    import json as _json

    payload = summary.as_dict()
    if args.json:
        print(_json.dumps(payload, indent=2))
    else:
        print("demo seed complete")
        print(f"  db:              {payload['db_path']}")
        print(f"  hospitals:       {', '.join(summary.hospitals)}")
        print(f"  datasets:        {summary.datasets}")
        print(f"  snapshots:       {summary.snapshots}")
        print(f"  change_sets:     {summary.change_sets}")
        print(
            f"  verification:    {summary.verification_jobs} jobs / "
            f"{summary.verification_results} results"
        )
        print(f"  reconciliation:  {summary.reconciliation_cases} cases")
        print(f"  reports:         {summary.reports}")
        print(f"  monitoring:      {summary.monitoring_policies} policies")
        print(f"  audit_events:    {summary.audit_events}")
        print(f"  users:           {', '.join(summary.users)}")
        print(f"  default path:    {DEMO_DB_PATH}")
        print(f"  disclaimer:      {payload['disclaimer']}")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hospital-extractor serve",
        description=(
            "Start the API server after production-style startup verification. "
            "In MEDCOMPARE_AUTH_MODE=production, invalid configuration fails "
            "closed (no silent development-auth fallback)."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument(
        "--db",
        default=None,
        help="Hospital store path (default: data/medcompare.db via env/code)",
    )
    parser.add_argument("--log-level", default="INFO", help="Log level (default: INFO)")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Uvicorn workers (default: 1; keep 1 with SQLite)",
    )
    return parser


def _run_serve(argv: list[str] | None = None) -> int:
    args = build_serve_parser().parse_args(argv)
    configure_logging(args.log_level)

    from hospital_extractor.auth.settings import auth_settings
    from hospital_extractor.ops.startup import StartupConfigError, verify_startup

    settings = auth_settings()
    try:
        report = verify_startup(settings=settings, db_path=args.db, raise_on_error=True)
    except StartupConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("refusing to start with invalid configuration", file=sys.stderr)
        return 1
    if not report.ok and settings.mode.value == "development":
        failed = [c.name for c in report.checks if not c.ok]
        print(f"warning: startup checks failed ({', '.join(failed)})", file=sys.stderr)

    import uvicorn

    from hospital_extractor.api.app import create_app

    # create_app re-runs production verification; development uses defaults.
    if args.db:
        app = create_app()
        app.state.medcompare_db_path = Path(args.db)
    else:
        app = create_app()

    if args.workers > 1:
        # Uvicorn only fans out workers from an import string/factory.
        if args.db:
            import os

            os.environ.setdefault("MEDCOMPARE_DB_PATH", str(args.db))
        uvicorn.run(
            "hospital_extractor.api.app:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            log_level=args.log_level.lower(),
            workers=args.workers,
        )
    else:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level=args.log_level.lower(),
        )
    return 0


def build_verify_startup_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hospital-extractor verify-startup",
        description=(
            "Run production-style startup checks and print a JSON report "
            "(no secrets). Exit code 0 when ok, 1 when a required check fails."
        ),
    )
    parser.add_argument("--db", default=None, help="Hospital store path to probe")
    return parser


def build_benchmark_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hospital-extractor benchmark",
        description=(
            "Run the deterministic research benchmark (P26) and print JSON or "
            "Markdown. No composite overall score; raw counts always included."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write the report to this file path instead of stdout",
    )
    parser.add_argument(
        "--case",
        default=None,
        help="Run only one case id (or category), e.g. extraction",
    )
    return parser


def _run_benchmark(argv: list[str] | None = None) -> int:
    import sys
    from pathlib import Path as _Path

    args = build_benchmark_parser().parse_args(argv)
    from hospital_extractor.benchmark import (
        format_benchmark_json,
        format_benchmark_markdown,
        run_benchmarks,
    )

    run = run_benchmarks(case_filter=args.case)
    if args.format == "markdown":
        text = format_benchmark_markdown(run)
    else:
        text = format_benchmark_json(run)
    if args.output:
        _Path(args.output).write_text(text, encoding="utf-8")
    else:
        # Windows console may default to cp1252; never crash on arrows/typography.
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is not None:
            buffer.write(text.encode(encoding, errors="replace"))
            buffer.write(b"\n")
            buffer.flush()
        else:  # pragma: no cover
            print(text)
    summary = run.summary
    if summary is None:
        return 1
    # Exit 1 only on case failures/errors; observation cases never fail the run.
    if summary.cases_failed > 0 or summary.cases_error > 0:
        return 1
    if args.case and not run.cases:
        print(f"benchmark: unknown case {args.case!r}", file=sys.stderr)
        return 2
    return 0


def _run_verify_startup(argv: list[str] | None = None) -> int:
    import json

    args = build_verify_startup_parser().parse_args(argv)
    from hospital_extractor.auth.settings import auth_settings
    from hospital_extractor.ops.startup import StartupConfigError, verify_startup

    settings = auth_settings()
    try:
        report = verify_startup(settings=settings, db_path=args.db, raise_on_error=False)
    except StartupConfigError as exc:  # pragma: no cover - raise_on_error=False
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (sync wrapper around async commands)."""
    effective_argv = argv if argv is not None else sys.argv[1:]
    if effective_argv and effective_argv[0] == "compare":
        return asyncio.run(_run_compare(effective_argv[1:]))
    if effective_argv and effective_argv[0] == "integration-demo":
        return asyncio.run(_run_integration_demo(effective_argv[1:]))
    if effective_argv and effective_argv[0] == "capture":
        return asyncio.run(_run_capture(effective_argv[1:]))
    if effective_argv and effective_argv[0] == "snapshots":
        return _run_snapshots(effective_argv[1:])
    if effective_argv and effective_argv[0] == "changes":
        return _run_changes(effective_argv[1:])
    if effective_argv and effective_argv[0] == "hospitals":
        return _run_hospitals(effective_argv[1:])
    if effective_argv and effective_argv[0] == "search":
        return _run_search(effective_argv[1:])
    if effective_argv and effective_argv[0] == "verify":
        return _run_verify(effective_argv[1:])
    if effective_argv and effective_argv[0] == "verification-jobs":
        return _run_verification_jobs(effective_argv[1:])
    if effective_argv and effective_argv[0] == "worker":
        return asyncio.run(_run_worker(effective_argv[1:]))
    if effective_argv and effective_argv[0] == "verification-demo":
        return _run_verification_demo(effective_argv[1:])
    if effective_argv and effective_argv[0] == "reconciliation-cases":
        return _run_reconciliation_cases(effective_argv[1:])
    if effective_argv and effective_argv[0] == "reconciliation":
        return _run_reconciliation(effective_argv[1:])
    if effective_argv and effective_argv[0] == "review":
        return _run_review(effective_argv[1:])
    if effective_argv and effective_argv[0] == "resolve":
        return _run_resolve(effective_argv[1:])
    if effective_argv and effective_argv[0] == "dismiss":
        return _run_dismiss(effective_argv[1:])
    if effective_argv and effective_argv[0] == "reopen":
        return _run_reopen(effective_argv[1:])
    if effective_argv and effective_argv[0] == "monitoring":
        return _run_monitoring(effective_argv[1:])
    if effective_argv and effective_argv[0] == "report":
        return _run_report(effective_argv[1:])
    if effective_argv and effective_argv[0] == "reports":
        return _run_reports(effective_argv[1:])
    if effective_argv and effective_argv[0] == "submit":
        return _run_submit(effective_argv[1:])
    if effective_argv and effective_argv[0] == "submissions":
        return _run_submissions(effective_argv[1:])
    if effective_argv and effective_argv[0] == "sources":
        return _run_sources(effective_argv[1:])
    if effective_argv and effective_argv[0] == "connectors":
        return _run_connectors(effective_argv[1:])
    if effective_argv and effective_argv[0] == "quality-report":
        return _run_quality_report(effective_argv[1:])
    if effective_argv and effective_argv[0] == "serve":
        return _run_serve(effective_argv[1:])
    if effective_argv and effective_argv[0] == "verify-startup":
        return _run_verify_startup(effective_argv[1:])
    if effective_argv and effective_argv[0] == "benchmark":
        return _run_benchmark(effective_argv[1:])
    if effective_argv and effective_argv[0] == "demo":
        return _run_demo(effective_argv[1:])
    return asyncio.run(_run_single(argv))


if __name__ == "__main__":
    raise SystemExit(main())
