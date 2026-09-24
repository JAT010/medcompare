"""Deterministic tests for targeted crawling: relevance scoring and selection."""

from __future__ import annotations

from hospital_extractor.crawler.candidates import (
    SOURCE_SITEMAP,
    CandidatePage,
)
from hospital_extractor.crawler.classify import PageClassifier
from hospital_extractor.crawler.context import build_context
from hospital_extractor.crawler.crawler import ProbeOutcome, ProbeResult
from hospital_extractor.crawler.relevance import (
    SEED_SCORE,
    RelevanceScorer,
    page_key,
    select_candidates,
)
from hospital_extractor.crawler.report import CrawlReport, format_summary
from hospital_extractor.crawler.url_filter import (
    DEFAULT_BLOCKED_QUERY_PARAMS,
    RobotsPolicy,
    UrlFilter,
)

BASE = "https://example.test"


def _scorer(target_url: str, identity: str | None = None) -> RelevanceScorer:
    return RelevanceScorer(target_url, target_identity=identity)


def _filter() -> UrlFilter:
    return UrlFilter(
        BASE,
        robots=RobotsPolicy(user_agent="test", rules=[]),
        block_query_params=DEFAULT_BLOCKED_QUERY_PARAMS,
    )


def _absolute(url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"{BASE}{url}"


def _select(
    urls: list[str],
    *,
    target_url: str = f"{BASE}/",
    identity: str | None = None,
    budget: int = 20,
    min_score: float = 2.0,
    include_low_value: bool = False,
):
    candidates = [
        CandidatePage(url=_absolute(url), source_of_discovery=SOURCE_SITEMAP) for url in urls
    ]
    return select_candidates(
        candidates,
        target_url=target_url,
        target_identity=identity,
        budget=budget,
        min_score=min_score,
        scorer=_scorer(target_url, identity),
        url_filter=_filter(),
        classifier=PageClassifier(),
        include_low_value=include_low_value,
    )


def test_seed_always_scored_highest():
    selection = _select(
        ["/about-us", "/contact-us"],
        min_score=0.0,
    )
    assert selection.selected[0].url == f"{BASE}/"
    assert selection.scores[f"{BASE}/"] == SEED_SCORE


def test_high_priority_urls_outrank_generic_pages():
    scorer = _scorer(f"{BASE}/")
    cardiac = scorer.score_url(f"{BASE}/cardiac-sciences/cabg-surgery")
    knee = scorer.score_url(f"{BASE}/total-knee-replacement")
    contact = scorer.score_url(f"{BASE}/contact-us")
    generic = scorer.score_url(f"{BASE}/unrelated-page")
    assert not cardiac.excluded
    assert not knee.excluded
    assert cardiac.score > contact.score > generic.score
    assert knee.score >= 2.0


def test_low_value_and_tracking_urls_excluded():
    selection = _select(
        [
            "/about-us",
            "/blog/top-10-tips",
            "/news",
            "/careers",
            "/events/webinar",
            "/appointment-booking",
            "/privacy-policy",
            "/gallery",
            "/about-us?page=2",
        ]
    )
    selected_urls = {candidate.url for candidate in selection.selected}
    assert selected_urls == {f"{BASE}/", f"{BASE}/about-us"}
    reasons = {record.reason for record in selection.skipped}
    assert "excluded section keyword 'blog'" in reasons
    assert "excluded section keyword 'news'" in reasons
    assert "excluded section keyword 'careers'" in reasons
    assert "excluded section keyword 'events'" in reasons
    assert "excluded section keyword 'appointment'" in reasons
    assert "excluded section keyword 'privacy'" in reasons
    assert "excluded section keyword 'gallery'" in reasons


def test_preventive_care_not_excluded_by_event_word():
    scorer = _scorer(f"{BASE}/")
    result = scorer.score_url(f"{BASE}/preventive-care")
    assert not result.excluded


def test_sibling_branch_excluded_but_target_branch_kept():
    target = f"{BASE}/hospital-network/saket"
    selection = _select(
        [
            f"{BASE}/hospital-network/dwarka/about-us",
            f"{BASE}/hospital-network/saket/contact-us",
        ],
        target_url=target,
        identity="saket",
        min_score=0.0,
    )
    selected_urls = {candidate.url for candidate in selection.selected}
    assert selected_urls == {
        target,
        f"{BASE}/hospital-network/saket/contact-us",
    }
    assert any(record.reason == "unrelated hospital branch" for record in selection.skipped)


def test_query_string_variants_deduplicated_by_page_key():
    assert page_key(f"{BASE}/doctor/?speciality=cardiology") == page_key(f"{BASE}/doctor/")
    assert page_key(f"{BASE}/?utm_source=newsletter") == page_key(f"{BASE}/")
    assert page_key(f"{BASE}/about-us") != page_key(f"{BASE}/about")


def test_tracking_query_params_blocked_by_filter():
    url_filter = _filter()
    assert not url_filter.is_allowed(f"{BASE}/contact?utm_source=newsletter")
    assert not url_filter.is_allowed(f"{BASE}/contact?fbclid=abc123")
    assert not url_filter.is_allowed(f"{BASE}/contact?gclid=abc123")
    assert url_filter.is_allowed(f"{BASE}/contact")


def test_budget_truncates_to_most_relevant():
    # Four pages across two categories; diversity cap lets each category have
    # up to 60% of the budget, so with budget=2 all four fit (max_per_cat=1
    # each).  Use a scenario where pages share a category to verify the cap.
    selection = _select(
        ["/cardiac-sciences/cabg-surgery", "/cardiac-sciences/angioplasty",
         "/orthopaedics/knee-replacement", "/orthopaedics/hip-replacement"],
        budget=2,
        min_score=0.0,
    )
    assert len(selection.selected) == 2
    assert any(record.reason == "page budget exceeded" for record in selection.skipped)


def test_min_relevance_threshold_skips_weak_pages():
    selection = _select(
        ["/contact-us", "/total-knee-replacement", "/some-generic-page"],
        min_score=2.0,
    )
    selected_urls = {candidate.url for candidate in selection.selected}
    assert f"{BASE}/some-generic-page" not in selected_urls
    assert any(record.reason == "below minimum relevance threshold" for record in selection.skipped)


def test_diagnostics_discovered_and_skip_reasons():
    selection = _select(["/about-us", "/blog/x", "/news"], min_score=0.0)
    assert f"{BASE}/about-us" in selection.discovered_urls
    assert f"{BASE}/blog/x" in selection.discovered_urls
    assert f"{BASE}/" in selection.scores


def test_content_exclusion_via_title():
    scorer = _scorer(f"{BASE}/")
    blog = scorer.score_content(
        "<html><head><title>News | City Hospital</title></head><body></body></html>",
        f"{BASE}/updates",
    )
    assert blog.excluded
    booking = scorer.score_content(
        "<html><head><title>Book an Appointment</title></head><body></body></html>",
        f"{BASE}/updates",
    )
    assert booking.excluded


def test_content_scoring_hits_intake_field_groups():
    scorer = _scorer(f"{BASE}/")
    html = (
        "<html><head><title>Total Knee Replacement | City Hospital</title></head>"
        "<body><h1>Total Knee Replacement</h1></body></html>"
    )
    result = scorer.score_content(html, f"{BASE}/joint-replacement")
    assert not result.excluded
    assert result.score > 0
    groups = {match.group for match in result.matches}
    assert "orthopaedics" in groups
    knee_matches = [
        match for match in result.matches if match.group == "orthopaedics"
    ]
    assert any("total_knee_replacement" in match.field_keys for match in knee_matches)


def test_report_exposes_targeted_crawl_diagnostics():
    probe = ProbeResult(
        robots=ProbeOutcome(
            url=f"{BASE}/robots.txt",
            status_code=200,
            final_url=None,
            content_type=None,
        ),
        homepage=ProbeOutcome(url=BASE, status_code=200, final_url=None, content_type=None),
    )
    report = CrawlReport(
        context=build_context(f"{BASE}/"),
        probe=probe,
        robots_url=f"{BASE}/robots.txt",
        robots_policy=RobotsPolicy(user_agent="test", rules=[]),
        sitemap_urls=(f"{BASE}/sitemap.xml",),
        discovered_urls=[f"{BASE}/", f"{BASE}/about-us"],
        candidates=[
            CandidatePage(url=f"{BASE}/about-us", selection_score=3.5),
        ],
        selected_urls=[CandidatePage(url=f"{BASE}/about-us", selection_score=3.5)],
        selection_scores={f"{BASE}/about-us": 3.5},
        pages=[],
        skipped_urls=[],
        failed_urls=[],
        raw_dir="data/raw/20200101T000000",
    )
    assert report.discovered == 2
    assert report.candidate_urls == [f"{BASE}/about-us"]
    assert report.crawled_urls == []
    assert report.skip_reasons() == {}
    summary = format_summary(report)
    assert "URLs discovered:" in summary
    assert "URLs selected:" in summary


# ---------------------------------------------------------------------------
# Pagination tests
# ---------------------------------------------------------------------------


def test_page_key_collapses_query_params():
    key1 = page_key(f"{BASE}/about-us?page=2")
    key2 = page_key(f"{BASE}/about-us?utm_source=google")
    key3 = page_key(f"{BASE}/about-us")
    assert key1 == key2 == key3


def test_sitemap_discovered_urls_receive_boost():
    selection = _select(
        [
            "/orthopaedics",
            "/doctor-listing",
            "/treatment/knee-replacement",
        ],
        budget=10,
        min_score=0.0,
    )
    sitemap_scores = {
        url: score
        for url, score in selection.scores.items()
        if url != f"{BASE}/"
    }
    assert len(sitemap_scores) > 0
    for url, score in sitemap_scores.items():
        assert score >= 3.0, f"{url} should have sitemap boost, got {score}"


def test_pagination_candidates_generated():
    from hospital_extractor.crawler.discovery import discover_pagination_candidates

    # Minimal Livewire wire:initial-data with next page URL
    html = (
        '<div wire:id="abc123" wire:initial-data='
        '"{&quot;effects&quot;:{&quot;dispatches&quot;:'
        '[{&quot;event&quot;:&quot;canonical-updated&quot;,'
        '&quot;data&quot;:{&quot;nextPageUrl&quot;:'
        '&quot;https://www.medanta.org/doctor-listing?page=2&quot;}}]},'
        '&quot;serverMemo&quot;:{&quot;data&quot;:'
        '{&quot;page&quot;:1,&quot;paginators&quot;:'
        '{&quot;page&quot;:1}}}}">'
        "</div>"
    )
    candidates = discover_pagination_candidates(
        html,
        "https://www.medanta.org",
        max_pagination_pages=5,
    )
    assert len(candidates) == 4  # pages 2, 3, 4, 5
    urls = [c.url for c in candidates]
    assert "https://www.medanta.org/doctor-listing?page=2" in urls
    assert "https://www.medanta.org/doctor-listing?page=5" in urls
    assert all(c.source_of_discovery == "sitemap" for c in candidates)


def test_pagination_candidates_empty_when_no_pagination():
    from hospital_extractor.crawler.discovery import discover_pagination_candidates

    html = '<html><body><div class="content">No pagination here</div></body></html>'
    candidates = discover_pagination_candidates(html, "https://example.com")
    assert candidates == []
