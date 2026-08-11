"""End-to-end crawl orchestration for one hospital website.

Binds every stage of the discovery/crawl phase into a single callable flow:

``probe (with UA fallback) → robots → sitemap discovery → candidate scoping →
polite crawl → classification → raw storage → report``

The runner never fabricates data and never bypasses robots, authentication or
bot protection. A failed page is recorded and the crawl continues; only a
hard access block or exhausted limits stop the run.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin

from hospital_extractor.crawler.candidates import SOURCE_INTERNAL_LINK, CandidatePage
from hospital_extractor.crawler.classify import PageClassifier, is_low_value_category
from hospital_extractor.crawler.context import CrawlContext
from hospital_extractor.crawler.crawler import (
    Crawler,
    CrawlError,
    CrawlStoppedError,
    FetchError,
    MaxPagesError,
    ProbeResult,
)
from hospital_extractor.crawler.discovery import (
    SitemapFetcher,
    discover_sitemap_candidates,
    extract_internal_links,
    scope_rank,
)
from hospital_extractor.crawler.metadata import PageMetadata, build_metadata
from hospital_extractor.crawler.report import CrawlReport
from hospital_extractor.crawler.url_filter import (
    RobotsPolicy,
    UrlFilter,
    parse_robots_txt,
)
from hospital_extractor.storage.raw import RawStore
from hospital_extractor.utils.logging import get_logger

__all__ = ["CrawlAbortedError", "TargetInaccessibleError", "run_crawl"]

DEFAULT_OUTPUT_DIR = "data"
MAX_INTERNAL_LINKS_PER_PAGE = 50
MAX_QUEUE_SIZE = 20_000


class CrawlAbortedError(RuntimeError):
    """The crawl could not proceed (access blocked, unusable target)."""


class TargetInaccessibleError(CrawlAbortedError):
    """The target could not be reached with any configured user-agent."""


async def run_crawl(
    context: CrawlContext,
    *,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
    run_id: str | None = None,
    include_low_value: bool = False,
    logger: logging.Logger | None = None,
) -> CrawlReport:
    """Run the full discovery/crawl phase and return a :class:`CrawlReport`."""
    log = logger or get_logger("hospital_extractor.crawler.runner")
    output_path = Path(output_dir)
    run_key = run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    raw_store = RawStore(output_path / "raw")

    log.info("crawl_started", extra={"target_url": context.target_url})

    crawler, probe, selected_ua = await _probe_with_fallback(context, log=log)
    log.info(
        "access_probe",
        extra={"robots_status": probe.robots_status, "homepage_status": probe.homepage_status},
    )
    if not probe.reachable:
        await crawler.aclose()
        raise TargetInaccessibleError(
            f"target {context.target_url!r} is not reachable; "
            f"robots={probe.robots_status!r} homepage={probe.homepage_status!r}"
        )

    robots_policy = await _load_robots(crawler, context, selected_ua, log=log)
    url_filter = UrlFilter(context.target_url, robots=robots_policy)

    discovery = await discover_sitemap_candidates(
        _resource_fetcher(crawler),
        context.target_url,
        robots_sitemaps=robots_policy.sitemaps,
    )
    for url in discovery.sitemap_urls:
        log.info("sitemap_loaded", extra={"sitemap_url": url})

    classifier = PageClassifier()
    candidates = _scope_candidates(
        discovery.candidates,
        context,
        url_filter=url_filter,
        classifier=classifier,
        include_low_value=include_low_value,
        log=log,
    )

    pages, skipped, failed = await _crawl(
        crawler,
        candidates,
        context=context,
        raw_store=raw_store,
        run_id=run_key,
        url_filter=url_filter,
        classifier=classifier,
        include_low_value=include_low_value,
        log=log,
    )
    await crawler.aclose()

    log.info("crawl_finished", extra={"fetched": len(pages), "failed": len(failed)})
    return CrawlReport(
        context=context,
        probe=probe,
        robots_url=urljoin(context.target_url, "/robots.txt"),
        robots_policy=robots_policy,
        sitemap_urls=discovery.sitemap_urls,
        candidates=candidates,
        pages=pages,
        skipped_urls=skipped,
        failed_urls=failed,
        raw_dir=raw_store.root / run_key,
    )


def _resource_fetcher(crawler: Crawler) -> SitemapFetcher:
    async def fetcher(url: str) -> bytes | None:
        try:
            result = await crawler.fetch_resource(url)
        except CrawlError:
            return None
        if result.status_code != 200 or not result.content:
            return None
        return result.content

    return fetcher


async def _probe_with_fallback(
    context: CrawlContext, *, log: logging.Logger
) -> tuple[Crawler, ProbeResult, str]:
    last_probe: ProbeResult | None = None
    for ua in context.user_agents:
        crawler = Crawler(
            user_agent=ua,
            timeout=context.crawl_limits.timeout,
            retries=context.crawl_limits.max_retries,
            delay=context.crawl_limits.delay,
            max_pages=context.crawl_limits.max_pages,
            max_response_bytes=context.crawl_limits.max_response_bytes,
        )
        probe = await crawler.probe(context.target_url)
        last_probe = probe
        log.info(
            "access_probe_ua",
            extra={"user_agent": ua, "homepage_status": probe.homepage_status},
        )
        if probe.reachable:
            return crawler, probe, ua
        await crawler.aclose()
    assert last_probe is not None
    raise TargetInaccessibleError(
        f"target {context.target_url!r} not reachable with any user-agent "
        f"(homepage status {last_probe.homepage_status!r})"
    )


async def _load_robots(
    crawler: Crawler, context: CrawlContext, user_agent: str, *, log: logging.Logger
) -> RobotsPolicy:
    robots_url = urljoin(context.target_url, "/robots.txt")
    result = await crawler.fetch_resource(robots_url)
    if result.status_code != 200:
        log.info(
            "robots_loaded",
            extra={"robots_url": robots_url, "status": result.status_code, "policy": "permissive"},
        )
        return RobotsPolicy(user_agent=user_agent, rules=[], sitemaps=())
    policy = parse_robots_txt(result.content.decode("utf-8", errors="replace"), user_agent)
    log.info(
        "robots_loaded",
        extra={
            "robots_url": robots_url,
            "rules": len(policy.rules),
            "sitemaps": list(policy.sitemaps),
        },
    )
    return policy


def _scope_candidates(
    candidates: list[CandidatePage],
    context: CrawlContext,
    *,
    url_filter: UrlFilter,
    classifier: PageClassifier,
    include_low_value: bool,
    log: logging.Logger,
) -> list[CandidatePage]:
    """Filter candidates and order them by hospital scope."""
    eligible: list[CandidatePage] = []
    for candidate in candidates:
        decision = url_filter.decision(candidate.url)
        if not decision.allowed:
            log.info("candidate_skipped", extra={"url": candidate.url, "reason": decision.reason})
            continue
        preliminary = classifier.from_url(candidate.url)
        candidate = CandidatePage(
            url=candidate.url,
            source_of_discovery=candidate.source_of_discovery,
            sitemap_name=candidate.sitemap_name,
            parent_url=candidate.parent_url,
            depth=candidate.depth,
            preliminary_category=preliminary.category,
        )
        if is_low_value_category(preliminary.category) and not include_low_value:
            log.info(
                "candidate_skipped",
                extra={
                    "url": candidate.url,
                    "reason": f"low-value category {preliminary.category}",
                },
            )
            continue
        log.info("candidate_discovered", extra={"url": candidate.url})
        eligible.append(candidate)

    slug = context.target_hospital_identity
    eligible.sort(key=lambda c: (scope_rank(c.url, slug, None), c.url))
    return eligible


async def _crawl(
    crawler: Crawler,
    candidates: list[CandidatePage],
    *,
    context: CrawlContext,
    raw_store: RawStore,
    run_id: str,
    url_filter: UrlFilter,
    classifier: PageClassifier,
    include_low_value: bool,
    log: logging.Logger,
) -> tuple[list[PageMetadata], list[str], list[str]]:
    queue: deque[CandidatePage] = deque(candidates)
    enqueued: set[str] = {candidate.url for candidate in candidates}
    fetched_urls: set[str] = set()
    pages: list[PageMetadata] = []
    skipped: list[str] = []
    failed: list[str] = []
    max_depth = context.crawl_limits.max_depth
    processed = 0

    while queue:
        if processed >= context.crawl_limits.max_pages:
            break
        candidate = queue.popleft()
        if candidate.depth > max_depth:
            skipped.append(candidate.url)
            continue
        if candidate.url in fetched_urls:
            continue

        try:
            if raw_store.exists(run_id, candidate.url):
                page = raw_store.load(run_id, candidate.url)
            else:
                page = await crawler.fetch(candidate.url)
        except FetchError:
            failed.append(candidate.url)
            log.info("page_failed", extra={"url": candidate.url, "reason": "fetch error"})
            continue
        except MaxPagesError:
            break
        except CrawlStoppedError:
            break

        if not page.is_html or page.status_code >= 400:
            skipped.append(candidate.url)
            log.info(
                "candidate_skipped",
                extra={
                    "url": candidate.url,
                    "status": page.status_code,
                    "content_type": page.content_type,
                },
            )
            continue

        fetched_urls.add(candidate.url)
        processed += 1
        classification = classifier.from_content(
            page.html.decode("utf-8", errors="replace"), candidate.url
        )
        raw_path = raw_store.save(run_id, page)
        metadata = build_metadata(
            page,
            depth=candidate.depth,
            parent_url=candidate.parent_url,
            discovery_source=candidate.source_of_discovery,
            classification=classification,
            raw_content_reference=raw_path,
        )
        pages.append(metadata)
        log.info(
            "page_fetched",
            extra={
                "url": candidate.url,
                "status": page.status_code,
                "category": classification.category,
            },
        )
        log.info(
            "page_classified",
            extra={
                "url": candidate.url,
                "category": classification.category,
                "confidence": classification.confidence,
            },
        )

        if candidate.depth >= max_depth:
            continue
        links = extract_internal_links(
            page.html.decode("utf-8", errors="replace"),
            context.target_url,
            url_filter=url_filter,
        )
        added = 0
        for link in links:
            if added >= MAX_INTERNAL_LINKS_PER_PAGE or len(enqueued) >= MAX_QUEUE_SIZE:
                break
            if link in enqueued or link in fetched_urls:
                continue
            preliminary = classifier.from_url(link)
            if is_low_value_category(preliminary.category) and not include_low_value:
                continue
            enqueued.add(link)
            queue.append(
                CandidatePage(
                    url=link,
                    source_of_discovery=SOURCE_INTERNAL_LINK,
                    parent_url=candidate.url,
                    depth=candidate.depth + 1,
                    preliminary_category=preliminary.category,
                )
            )
            added += 1
            log.info("candidate_discovered", extra={"url": link, "parent": candidate.url})

    return pages, skipped, failed
