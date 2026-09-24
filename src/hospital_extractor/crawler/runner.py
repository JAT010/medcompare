"""End-to-end crawl orchestration for one hospital website.

Binds every stage of the discovery/crawl phase into a single callable flow:

``probe (with UA fallback) → robots → sitemap discovery → targeted candidate
selection → polite crawl (priority-ordered, content-verified) → classification
→ raw storage → report``

The runner never fabricates data and never bypasses robots, authentication or
bot protection. A failed page is recorded and the crawl continues; only a
hard access block or exhausted limits stop the run.

Candidate selection is targeted: every discovered URL is scored against the
25-field intake vocabulary, low-value sections (blogs, news, careers,
appointment flows, privacy/terms...) are excluded, sibling branches of the
same hospital chain are dropped, and the crawl queue is budget-truncated to
the highest-relevance pages. The extraction engine decides the final field
values; this phase only decides which pages to fetch.
"""

from __future__ import annotations

import heapq
import logging
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin

from hospital_extractor.crawler.candidates import (
    SOURCE_INTERNAL_LINK,
    SOURCE_SITEMAP,
    CandidatePage,
    SkipRecord,
)
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
    discover_pagination_candidates,
    discover_sitemap_candidates,
    extract_internal_links,
    scope_rank,
)
from hospital_extractor.crawler.metadata import PageMetadata, build_metadata
from hospital_extractor.crawler.relevance import SITEMAP_BONUS, RelevanceScorer, select_candidates
from hospital_extractor.crawler.report import CrawlReport
from hospital_extractor.crawler.url_filter import (
    DEFAULT_BLOCKED_QUERY_PARAMS,
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
    url_filter = UrlFilter(
        context.target_url,
        robots=robots_policy,
        block_query_params=DEFAULT_BLOCKED_QUERY_PARAMS,
    )

    discovery = await discover_sitemap_candidates(
        _resource_fetcher(crawler),
        context.target_url,
        robots_sitemaps=robots_policy.sitemaps,
    )
    for url in discovery.sitemap_urls:
        log.info("sitemap_loaded", extra={"sitemap_url": url})

    classifier = PageClassifier()
    scorer = RelevanceScorer(
        context.target_url,
        target_identity=context.target_hospital_identity,
    )
    selection = select_candidates(
        discovery.candidates,
        target_url=context.target_url,
        target_identity=context.target_hospital_identity,
        budget=context.crawl_limits.max_pages,
        min_score=context.crawl_limits.enqueue_min_score,
        scorer=scorer,
        url_filter=url_filter,
        classifier=classifier,
        include_low_value=include_low_value,
        log=log,
    )

    pages, crawled_skipped, failed = await _crawl(
        crawler,
        selection.selected,
        context=context,
        raw_store=raw_store,
        run_id=run_key,
        url_filter=url_filter,
        classifier=classifier,
        scorer=scorer,
        include_low_value=include_low_value,
        log=log,
    )
    await crawler.aclose()

    skipped = [*selection.skipped, *crawled_skipped]
    log.info(
        "crawl_finished",
        extra={
            "discovered": len(selection.discovered_urls),
            "candidates": len(selection.candidates),
            "selected": len(selection.selected),
            "fetched": len(pages),
            "skipped": len(skipped),
            "failed": len(failed),
        },
    )
    return CrawlReport(
        context=context,
        probe=probe,
        robots_url=urljoin(context.target_url, "/robots.txt"),
        robots_policy=robots_policy,
        sitemap_urls=discovery.sitemap_urls,
        discovered_urls=selection.discovered_urls,
        candidates=selection.candidates,
        selected_urls=selection.selected,
        selection_scores=selection.scores,
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


async def _crawl(
    crawler: Crawler,
    candidates: list[CandidatePage],
    *,
    context: CrawlContext,
    raw_store: RawStore,
    run_id: str,
    url_filter: UrlFilter,
    classifier: PageClassifier,
    scorer: RelevanceScorer,
    include_low_value: bool,
    log: logging.Logger,
) -> tuple[list[PageMetadata], list[SkipRecord], list[str]]:
    """Crawl candidates in relevance order, verifying content after each fetch.

    The queue is a max-priority heap ordered by selection score, then hospital
    scope, then URL. Pages whose fetched content turns out to be a
    non-evidence page (e.g. a blog post whose URL did not look like one) are
    skipped and their children are never enqueued, which prevents recursive
    crawling of irrelevant sections.
    """
    max_depth = context.crawl_limits.max_depth
    min_score = context.crawl_limits.enqueue_min_score
    target_url = context.target_url

    heap: list[tuple[float, int, str, int, CandidatePage]] = []
    counter = 0
    enqueued: set[str] = set()

    def _push(candidate: CandidatePage) -> None:
        nonlocal counter
        heapq.heappush(
            heap,
            (
                -(candidate.selection_score or 0.0),
                scope_rank(candidate.url, context.target_hospital_identity, None),
                candidate.url,
                counter,
                candidate,
            ),
        )
        enqueued.add(candidate.url)
        counter += 1

    for candidate in candidates:
        _push(candidate)

    fetched_urls: set[str] = set()
    pages: list[PageMetadata] = []
    skipped: list[SkipRecord] = []
    failed: list[str] = []
    processed = 0
    # Category diversity cap: no single page category may consume more than
    # 60% of the crawl budget to prevent one section from starving others.
    _MAX_CATEGORY_RATIO = 0.6
    max_per_cat = max(1, int(context.crawl_limits.max_pages * _MAX_CATEGORY_RATIO))
    cat_fetched: dict[str, int] = {}

    while heap:
        if processed >= context.crawl_limits.max_pages:
            break
        _, _, url, _, candidate = heapq.heappop(heap)
        if candidate.depth > max_depth:
            skipped.append(SkipRecord(url, "max depth exceeded"))
            continue
        if url in fetched_urls:
            continue
        # Category diversity cap: skip if this category already has enough pages
        cat = candidate.preliminary_category or "unknown"
        if cat_fetched.get(cat, 0) >= max_per_cat:
            skipped.append(SkipRecord(url, f"category cap exceeded ({cat})"))
            continue

        try:
            if raw_store.exists(run_id, url):
                page = raw_store.load(run_id, url)
            else:
                page = await crawler.fetch(url)
        except FetchError:
            failed.append(url)
            log.info("page_failed", extra={"url": url, "reason": "fetch error"})
            continue
        except MaxPagesError:
            break
        except CrawlStoppedError:
            break

        if not page.is_html or page.status_code >= 400:
            skipped.append(SkipRecord(url, f"non-HTML or status {page.status_code}"))
            log.info(
                "candidate_skipped",
                extra={
                    "url": url,
                    "status": page.status_code,
                    "content_type": page.content_type,
                },
            )
            continue

        html = page.html.decode("utf-8", errors="replace")
        verification = scorer.score_content(html, url)
        if verification.excluded:
            reason = verification.exclusion_reason or "content excluded"
            skipped.append(SkipRecord(url, f"content: {reason}"))
            log.info(
                "page_content_excluded",
                extra={"url": url, "reason": reason},
            )
            continue

        fetched_urls.add(url)
        processed += 1
        cat_fetched[cat] = cat_fetched.get(cat, 0) + 1
        classification = classifier.from_content(html, url)
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
                "url": url,
                "status": page.status_code,
                "category": classification.category,
            },
        )
        log.info(
            "page_classified",
            extra={
                "url": url,
                "category": classification.category,
                "confidence": classification.confidence,
            },
        )

        # Pagination discovery: detect Livewire-style paginated listings and
        # enqueue remaining pages so the crawler doesn't stop at page 1.
        if candidate.depth < max_depth:
            pagination_candidates = discover_pagination_candidates(
                html, context.target_url, url_filter=url_filter,
            )
            for pc in pagination_candidates:
                if pc.url not in enqueued and pc.url not in fetched_urls:
                    pc_preliminary = classifier.from_url(pc.url)
                    pc_relevance = scorer.score_url(pc.url)
                    excluded = pc_relevance.excluded
                    low_value = is_low_value_category(pc_preliminary.category)
                    if not excluded and not low_value:
                        pc_score = pc_relevance.score
                        if pc.source_of_discovery == SOURCE_SITEMAP:
                            pc_score = (pc_score or 0.0) + SITEMAP_BONUS
                        scored_pc = CandidatePage(
                            url=pc.url,
                            source_of_discovery=pc.source_of_discovery,
                            depth=pc.depth,
                            selection_score=pc_score,
                        )
                        _push(scored_pc)
                        log.info(
                            "pagination_discovered",
                            extra={"parent_url": url, "page_url": pc.url},
                        )

        if candidate.depth >= max_depth:
            continue
        links = extract_internal_links(html, context.target_url, url_filter=url_filter)
        added = 0
        for link in links:
            if added >= MAX_INTERNAL_LINKS_PER_PAGE or len(enqueued) >= MAX_QUEUE_SIZE:
                break
            if link in enqueued or link in fetched_urls:
                continue
            preliminary = classifier.from_url(link)
            relevance = scorer.score_url(link)
            if relevance.excluded:
                skipped.append(SkipRecord(link, relevance.exclusion_reason or "excluded"))
                continue
            if is_low_value_category(preliminary.category) and not include_low_value:
                skipped.append(SkipRecord(link, f"low-value category {preliminary.category}"))
                continue
            if link != target_url and (relevance.score or 0.0) < min_score:
                skipped.append(SkipRecord(link, "below minimum relevance threshold"))
                continue
            _push(
                CandidatePage(
                    url=link,
                    source_of_discovery=SOURCE_INTERNAL_LINK,
                    parent_url=url,
                    depth=candidate.depth + 1,
                    preliminary_category=preliminary.category,
                    selection_score=relevance.score,
                )
            )
            added += 1
            log.info(
                "candidate_discovered",
                extra={"url": link, "parent": url, "score": relevance.score},
            )

    return pages, skipped, failed
