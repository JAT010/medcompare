"""Separately executable verification worker.

Runs outside the FastAPI process: claims due jobs (``SELECT ... FOR UPDATE
SKIP LOCKED`` on PostgreSQL), crawls the facility website with SSRF guards,
verifies against the triggering hospital snapshot, persists results and opens
reconciliation cases for inconclusive/contradicted outcomes.

Usage:
    python -m hospital_extractor.worker [--once] [--interval SECONDS] [--limit N]

Each sweep also processes due monitoring schedules (skip with ``--no-monitoring``).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from hospital_extractor.crawler.verify_crawl import crawl_website_for_verification
from hospital_extractor.jobs.queue import claim_due_jobs, fail_job, sweep_stale_leases
from hospital_extractor.monitoring.service import MonitoringService
from hospital_extractor.persistence.session import session_scope
from hospital_extractor.verification.service import VerificationService

__all__ = ["main"]


def _worker_id() -> str:
    return f"worker-{uuid.uuid4().hex[:8]}"


async def process_due_jobs(
    *,
    worker_id: str,
    limit: int,
    lease_seconds: int,
    backoff_seconds: int,
    max_pages: int,
    output_dir: str,
    crawl_timeout_seconds: float,
    run_monitoring: bool = True,
) -> int:
    """Claim and process up to *limit* due jobs; return the count processed."""
    processed = 0
    with session_scope() as session:
        swept = sweep_stale_leases(session)
        if run_monitoring:
            executions = await MonitoringService(session).process_due()
            enqueued = [r for r in executions if r.execution.job_id is not None]
            processed += len(enqueued)
        jobs = claim_due_jobs(
            session, worker_id=worker_id, limit=limit, lease_seconds=lease_seconds
        )
        for job in jobs:
            try:
                website = await crawl_website_for_verification(
                    session,
                    facility_id=job.facility_id,
                    target_url=job.target_ref,
                    max_pages=max_pages,
                    output_dir=output_dir,
                    crawl_timeout_seconds=crawl_timeout_seconds,
                )
            except Exception as exc:
                fail_job(
                    session,
                    job,
                    error_code="crawl_error",
                    error_message=f"{type(exc).__name__} during website crawl",
                    retryable=True,
                    backoff_seconds=backoff_seconds,
                )
                processed += 1
                continue

            if website.status == "failed":
                retryable = bool(website.warnings) or website.pages_fetched == 0
                fail_job(
                    session,
                    job,
                    error_code="crawl_failed",
                    error_message="; ".join(website.warnings)[:1000] or "website crawl failed",
                    retryable=retryable,
                    backoff_seconds=backoff_seconds,
                )
                processed += 1
                continue

            try:
                VerificationService(session).run(job, website=website, compared_by="worker")
            except Exception as exc:
                fail_job(
                    session,
                    job,
                    error_code="verification_error",
                    error_message=f"{type(exc).__name__} during verification",
                    retryable=True,
                    backoff_seconds=backoff_seconds,
                )
            processed += 1
        return processed + len(swept)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MedCompare verification worker")
    parser.add_argument("--once", action="store_true", help="process one sweep and exit")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between sweeps")
    parser.add_argument("--limit", type=int, default=1, help="max jobs per sweep")
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--backoff-seconds", type=int, default=300)
    parser.add_argument("--max-pages", type=int, default=15)
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--crawl-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--worker-id", default=None)
    parser.add_argument(
        "--no-monitoring",
        action="store_true",
        help="do not run due monitoring schedules during this worker's sweeps",
    )
    args = parser.parse_args(argv)

    worker_id = args.worker_id or _worker_id()

    async def run() -> int:
        run_monitoring = not args.no_monitoring
        if args.once:
            await process_due_jobs(
                worker_id=worker_id,
                limit=args.limit,
                lease_seconds=args.lease_seconds,
                backoff_seconds=args.backoff_seconds,
                max_pages=args.max_pages,
                output_dir=args.output_dir,
                crawl_timeout_seconds=args.crawl_timeout_seconds,
                run_monitoring=run_monitoring,
            )
            return 0
        while True:
            await process_due_jobs(
                worker_id=worker_id,
                limit=args.limit,
                lease_seconds=args.lease_seconds,
                backoff_seconds=args.backoff_seconds,
                max_pages=args.max_pages,
                output_dir=args.output_dir,
                crawl_timeout_seconds=args.crawl_timeout_seconds,
                run_monitoring=run_monitoring,
            )
            await asyncio.sleep(args.interval)

    try:
        return asyncio.run(run())
    except KeyboardInterrupt:  # pragma: no cover
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
