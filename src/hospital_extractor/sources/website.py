"""Website connector — the existing crawler, unchanged behind the P21 face.

The whole discovery/crawl → extraction pipeline is reused; this adapter simply
gives it the ``HospitalDataSource`` / connector contract so orchestration and
monitoring treat website, API and feed acquisition identically.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from hospital_extractor.config import ExtractionConfig, load_config
from hospital_extractor.crawler.context import CrawlLimits, build_context
from hospital_extractor.crawler.runner import CrawlAbortedError, run_crawl
from hospital_extractor.extraction import CrawlStats, build_dataset
from hospital_extractor.models.base import utc_now
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.sources.acquisition import (
    AcquisitionResult,
    ConnectorError,
    ConnectorErrorCode,
)
from hospital_extractor.sources.base import (
    HospitalDataSource,
    SourceFetchError,
    apply_source_provenance,
)
from hospital_extractor.sources.models import (
    FetchStatus,
    SourceIdentity,
    SourceMetadata,
    SourceType,
)
from hospital_extractor.utils.hashing import sha256_text
from hospital_extractor.utils.urls import InvalidURLError

__all__ = ["WebsiteDataSource"]

DEFAULT_OUTPUT_DIR = Path("data")


class WebsiteDataSource(HospitalDataSource):
    """Fetches a hospital by crawling its website (existing pipeline)."""

    source_type = SourceType.WEBSITE

    def __init__(
        self,
        identity: SourceIdentity,
        *,
        limits: CrawlLimits | None = None,
        include_low_value: bool = False,
        output_dir: Path | str = DEFAULT_OUTPUT_DIR,
        config: ExtractionConfig | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.identity_value = identity
        self.limits = limits
        self.include_low_value = include_low_value
        self.output_dir = Path(output_dir)
        self.config = config
        self.user_agent = user_agent

    def identity(self) -> SourceIdentity:
        return self.identity_value

    @property
    def authorization_status(self) -> str:
        """Public website crawl — no connector authorization required."""
        return "PUBLIC_CRAWL"

    async def acquire(self) -> AcquisitionResult:
        """Crawl the site and return acquisition metadata (bounded).

        The full page corpus stays inside the crawl report; only summary
        evidence and a deterministic content fingerprint leave this method.
        """
        source_identity = self.identity()
        try:
            context = build_context(
                source_identity.hospital_ref,
                limits=self.limits,
                user_agent=self.user_agent,
            )
        except InvalidURLError as exc:
            raise ConnectorError(
                str(exc),
                code=ConnectorErrorCode.INVALID_CONFIGURATION,
                retryable=False,
            ) from exc

        try:
            report = await run_crawl(
                context,
                output_dir=self.output_dir,
                include_low_value=self.include_low_value,
            )
        except CrawlAbortedError as exc:
            raise ConnectorError(
                str(exc),
                code=ConnectorErrorCode.NETWORK_ERROR,
                retryable=True,
            ) from exc

        pages: list[tuple[str, str]] = []
        for page in report.pages:
            reference = page.raw_content_reference
            if reference is None:
                continue
            html = reference.read_bytes().decode("utf-8", errors="replace")
            pages.append((page.requested_url, html))

        fingerprint = sha256_text(
            "\n".join(f"{url}\n{body}" for url, body in sorted(pages))
        )
        captured_at: datetime = utc_now()
        return AcquisitionResult(
            source_type=SourceType.WEBSITE,
            provider=context.target_domain,
            source_ref=context.target_url,
            requested_url=context.target_url,
            captured_at=captured_at,
            status_code=200,
            content_type="text/html",
            content_hash=fingerprint,
            payload=None,  # page corpus stays out of the shared result
            raw_size_bytes=sum(len(body.encode("utf-8")) for _, body in pages),
            evidence={
                "acquisition_method": "website_crawl",
                "pages_discovered": report.discovered,
                "pages_fetched": report.fetched,
                "pages_failed": report.failed,
                "pages_skipped": report.skipped,
                "domain": context.target_domain,
                "page_count": len(pages),
            },
        )

    async def fetch(self) -> HospitalDataset:
        source_identity = self.identity()
        try:
            context = build_context(
                source_identity.hospital_ref,
                limits=self.limits,
                user_agent=self.user_agent,
            )
        except InvalidURLError as exc:
            raise SourceFetchError(str(exc)) from exc

        try:
            report = await run_crawl(
                context,
                output_dir=self.output_dir,
                include_low_value=self.include_low_value,
            )
        except CrawlAbortedError as exc:
            raise SourceFetchError(str(exc)) from exc

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
        cfg = self.config or load_config()
        dataset = build_dataset(
            pages,
            target_url=context.target_url,
            crawl_stats=crawl_stats,
            config=cfg,
        )

        metadata = SourceMetadata(
            source_type=SourceType.WEBSITE,
            source_id=f"website:{context.target_domain}",
            provider=context.target_domain,
            base_url=context.target_url,
            endpoint=context.target_url,
            hospital_ref=context.target_url,
            hospital_name=source_identity.hospital_name,
            status=FetchStatus.SUCCESS,
            raw_payload_metadata={
                "pages_discovered": report.discovered,
                "pages_fetched": report.fetched,
                "pages_failed": report.failed,
                "pages_skipped": report.skipped,
                "domain": context.target_domain,
            },
        )
        apply_source_provenance(dataset, metadata)
        return dataset
