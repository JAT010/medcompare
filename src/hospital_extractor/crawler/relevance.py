"""Targeted crawl relevance scoring.

Ranks discovered URLs by how much evidence they can plausibly contribute to
the 25 intake fields. Scoring is deterministic and keyword-based; it only
selects candidate pages. The extraction engine decides the final field values.

Signals, strongest first:

- URL path tokens (``/total-knee-replacement/``)
- the first path segment (structural section)
- page title
- h1/h2 headings
- meta description

Low-value pages (blogs, news, careers, events, press, login/booking flows,
privacy/terms, marketing) are excluded before ranking. Sibling-branch URLs
(``/hospital-network/<other-slug>``) are excluded when they name a different
hospital than the target identity. The supplied hospital URL is always the
primary identity anchor and is never excluded.

``select_candidates`` applies the eligibility filter, the low-value category
filter and the page budget, then returns the ranked, budget-truncated crawl
queue plus diagnostics (selection scores and skip reasons).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from hospital_extractor.crawler.candidates import (
    SOURCE_SEED,
    SOURCE_SITEMAP,
    CandidatePage,
    SkipRecord,
)
from hospital_extractor.crawler.classify import PageClassifier, is_low_value_category
from hospital_extractor.crawler.discovery import scope_rank
from hospital_extractor.crawler.url_filter import UrlFilter

__all__ = [
    "CandidateSelection",
    "FieldGroup",
    "FieldMatch",
    "GROUPS",
    "PageRelevance",
    "RelevanceScorer",
    "SEED_SCORE",
    "page_key",
    "select_candidates",
]

# The supplied hospital URL is the primary identity anchor; it always outranks
# every discovered page.
SEED_SCORE = 10.0
SITEMAP_BONUS = 3.0  # bonus for URLs discovered via sitemap

_URL_WEIGHT = 2.0
_SECTION_WEIGHT = 1.5
_TITLE_WEIGHT = 3.0
_HEADING_WEIGHT = 2.0
_META_WEIGHT = 1.0
_HUB_BONUS = 0.5  # shallow hub pages (1-2 segments) are preferred over deep slugs

# Words that mark a URL segment as a low-value / non-evidence section. A
# segment is split on ``-`` and each word is compared exactly, so
# ``preventive-care`` never trips the ``event`` exclusion.
_EXCLUDE_WORDS = frozenset(
    {
        "blog", "blogs", "news", "newsroom", "press", "media",
        "career", "careers", "job", "jobs", "vacancy", "vacancies", "recruit",
        "recruitment", "hiring",
        "event", "events", "webinar", "seminar",
        "article", "articles", "publication", "publications", "magazine",
        "journal",
        "login", "sign", "signin", "signup", "register", "registration",
        "book", "booking", "appointment", "appointments", "schedule",
        "scheduler",
        "privacy", "terms", "disclaimer", "cookie", "cookies", "sitemap",
        "gallery", "photo", "photos", "video", "videos", "testimonial",
        "testimonials", "review", "reviews", "feedback", "survey",
        "newsletter",
        "insurance", "tpa", "cashless", "checkup", "wellness", "international",
        "download", "brochure",
    }
)

# Narrow exclusion set for page titles: only whole phrases that clearly mark a
# transactional or non-evidence page. Headings are never used for exclusion (a
# doctor page may carry a "Book an Appointment" CTA heading).
_TITLE_EXCLUSION_RE = re.compile(
    r"\b(blog(s)?|news|press\s*release(s)?|career(s)?|job(s)?|vacanc\w*|"
    r"event(s)?|webinar|privacy\s*policy|terms\s*of\s*(use|service)|"
    r"login|sign\s*in|book\s+(an?\s+)?appointment|"
    r"schedule\s+(an?\s+)?appointment)\b",
    re.I,
)

# Sections whose second segment names a specific hospital branch.
_BRANCH_SECTIONS = frozenset({"hospital-network", "hospitals", "branches"})


@dataclass(frozen=True)
class FieldGroup:
    """One high-priority evidence group and the intake fields it can feed."""

    key: str
    field_keys: tuple[str, ...]
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class FieldMatch:
    """One keyword match contributing to a relevance score."""

    group: str
    field_keys: tuple[str, ...]
    keyword: str
    source: str
    weight: float


@dataclass(frozen=True)
class PageRelevance:
    """Relevance verdict for one URL (or fetched page)."""

    url: str
    score: float
    excluded: bool = False
    exclusion_reason: str | None = None
    matches: tuple[FieldMatch, ...] = ()


@dataclass(frozen=True)
class CandidateSelection:
    """Outcome of targeted candidate selection for one crawl."""

    candidates: list[CandidatePage]
    selected: list[CandidatePage]
    skipped: list[SkipRecord]
    scores: dict[str, float]
    discovered_urls: list[str]


GROUPS: tuple[FieldGroup, ...] = (
    FieldGroup(
        "identity",
        ("hospital_name", "established_year", "logo_image_url"),
        (
            "about", "about-us", "about-the-hospital", "profile", "overview",
            "introduction", "who-we-are", "history", "milestones",
            "our-hospital",
        ),
    ),
    FieldGroup(
        "contact",
        ("pin_code", "city", "area_locality"),
        (
            "contact", "contact-us", "reach-us", "location", "locations",
            "locate", "visit-us", "directions", "find-us", "address",
        ),
    ),
    FieldGroup(
        "capacity",
        ("total_beds", "icu_beds"),
        (
            "bed", "beds", "bed-capacity", "bed-strength", "capacity",
            "infrastructure", "rooms", "wards", "inpatient", "in-patient",
        ),
    ),
    FieldGroup(
        "icu",
        ("icu", "icu_beds"),
        (
            "icu", "intensive-care", "intensive-care-unit", "critical-care",
            "critical-care-unit", "nursing",
        ),
    ),
    FieldGroup(
        "doctors",
        ("doctor_count",),
        (
            "doctor", "doctors", "physician", "physicians", "find-a-doctor",
            "find-doctor", "our-team", "specialist", "specialists",
            "consultant", "consultants",
        ),
    ),
    FieldGroup(
        "departments",
        ("cardiac_sciences_enabled", "orthopaedics_joint_replacement_enabled"),
        ("department", "departments", "dept"),
    ),
    FieldGroup(
        "specialities",
        ("cardiac_sciences_enabled", "orthopaedics_joint_replacement_enabled"),
        (
            "specialit", "specialt", "specialties", "specialities",
            "institute", "institutes", "centre", "centres", "center",
        ),
    ),
    FieldGroup(
        "technology",
        ("da_vinci_robotic_surgery", "advanced_laparoscopy_mis",
         "mri_3_tesla_or_higher"),
        (
            "technology", "technologies", "robotic", "robotics",
            "robotic-surgery", "da-vinci", "mri", "3-tesla", "mammograph",
            "ct-scan", "imaging", "radiology", "laparoscop",
            "minimal-invasive", "minimally-invasive", "mis", "endoscopy",
        ),
    ),
    FieldGroup(
        "accreditation",
        ("nabh_status", "jci_accredited"),
        (
            "accreditation", "accreditations", "nabh", "jci",
            "joint-commission", "iso", "certification", "certifications",
            "nabb", "quality-award",
        ),
    ),
    FieldGroup(
        "cardiac",
        (
            "cardiac_sciences_enabled", "bypass_surgery_cabg",
            "angioplasty_stenting", "heart_valve_replacement",
            "heart_valve_repair", "pacemaker_icd_implantation",
            "heart_transplant",
        ),
        (
            "cardiac", "cardiology", "cardi", "cardiovascular", "heart",
            "heart-care", "cath-lab", "cathlab", "cabg", "bypass",
            "bypass-surgery", "coronary", "angioplasty", "angio", "stent",
            "stenting", "valve", "valve-replacement", "valve-repair",
            "pacemaker", "icd", "defibrillator", "heart-transplant",
            "transplant",
        ),
    ),
    FieldGroup(
        "orthopaedics",
        (
            "orthopaedics_joint_replacement_enabled",
            "total_knee_replacement", "total_hip_replacement",
        ),
        (
            "orthop", "ortho", "orthopaedics", "orthopedics", "joint",
            "joint-replacement", "knee", "knee-replacement", "hip",
            "hip-replacement", "arthroplasty", "bone-joint",
        ),
    ),
)


class RelevanceScorer:
    """Deterministic relevance scorer for targeted crawling.

    ``target_url`` is the supplied hospital URL (the primary identity anchor);
    ``target_identity`` is the hospital slug used to reject sibling branches.
    """

    def __init__(self, target_url: str, *, target_identity: str | None = None) -> None:
        self._target = target_url
        self._identity = target_identity
        self._title_regexes = {group.key: _word_regex(group.keywords) for group in GROUPS}

    def score_url(self, url: str) -> PageRelevance:
        """Score *url* from its path tokens alone (pre-fetch ranking)."""
        if url == self._target or _is_root(url):
            return PageRelevance(url=url, score=SEED_SCORE)
        reason = _url_exclusion(url)
        if reason:
            return PageRelevance(url=url, score=0.0, excluded=True, exclusion_reason=reason)
        if _is_unrelated_branch(url, self._identity):
            return PageRelevance(
                url=url, score=0.0, excluded=True, exclusion_reason="unrelated hospital branch"
            )
        score, matches = _score_url_tokens(url)
        return PageRelevance(url=url, score=score, matches=matches)

    def score_content(self, html: str, url: str) -> PageRelevance:
        """Score a fetched page using URL, title, headings and meta description.

        A page whose title is a non-evidence page (blog, booking flow, ...) is
        excluded even when its URL looked harmless.
        """
        if url == self._target:
            return PageRelevance(url=url, score=SEED_SCORE)
        reason = _url_exclusion(url)
        if reason:
            return PageRelevance(url=url, score=0.0, excluded=True, exclusion_reason=reason)
        soup = BeautifulSoup(html, "lxml")
        title = _clean_text(soup.title.string) if soup.title else ""
        if title and _TITLE_EXCLUSION_RE.search(title):
            return PageRelevance(
                url=url, score=0.0, excluded=True, exclusion_reason=f"title excluded: {title!r}"
            )
        score, matches = _score_url_tokens(url)
        if title:
            score, matches = _add_text_signals(
                title, _TITLE_WEIGHT, "title", self._title_regexes, score, matches
            )
        for node in soup.find_all(["h1", "h2"])[:6]:
            heading = _clean_text(node.get_text(" ", strip=True))
            if not heading:
                continue
            score, matches = _add_text_signals(
                heading, _HEADING_WEIGHT, "heading", self._title_regexes, score, matches
            )
        meta = _meta_description(soup)
        if meta:
            score, matches = _add_text_signals(
                meta, _META_WEIGHT, "meta", self._title_regexes, score, matches
            )
        return PageRelevance(url=url, score=score, matches=matches)


def select_candidates(
    candidates: list[CandidatePage],
    *,
    target_url: str,
    target_identity: str | None,
    budget: int,
    min_score: float = 0.0,
    scorer: RelevanceScorer,
    url_filter: UrlFilter,
    classifier: PageClassifier,
    include_low_value: bool = False,
    log: logging.Logger | None = None,
) -> CandidateSelection:
    """Filter, score, rank and budget-truncate discovered candidates.

    The supplied hospital URL is always prepended as the identity seed and
    never excluded. Candidates are ranked by relevance score (descending),
    then hospital scope, then URL. The top ``budget`` are selected for the
    crawl queue; everything else is recorded as skipped.
    """
    log = log or logging.getLogger("hospital_extractor.crawler.relevance")
    PageKey = tuple[str, str, str]
    ordered = [CandidatePage(url=target_url, source_of_discovery=SOURCE_SEED, depth=0)]
    seen_candidates: set[PageKey] = {page_key(target_url)}
    for candidate in candidates:
        key = page_key(candidate.url)
        if key in seen_candidates:
            continue
        seen_candidates.add(key)
        ordered.append(candidate)

    eligible: list[CandidatePage] = []
    skipped: list[SkipRecord] = []
    scores: dict[str, float] = {}
    discovered: list[str] = []
    seen_pages: set[PageKey] = set()

    for candidate in ordered:
        url = candidate.url
        discovered.append(url)
        decision = url_filter.decision(url)
        if not decision.allowed:
            log.info("candidate_skipped", extra={"url": url, "reason": decision.reason})
            skipped.append(SkipRecord(url, decision.reason))
            continue
        key = page_key(url)
        if key in seen_pages:
            log.info("candidate_skipped", extra={"url": url, "reason": "query-string duplicate"})
            skipped.append(SkipRecord(url, "duplicate page (query-string variant)"))
            continue
        preliminary = classifier.from_url(url)
        relevance = scorer.score_url(url)
        if url == target_url and relevance.excluded:
            relevance = PageRelevance(url=url, score=SEED_SCORE)
        if relevance.excluded:
            log.info("candidate_skipped", extra={"url": url, "reason": relevance.exclusion_reason})
            skipped.append(SkipRecord(url, relevance.exclusion_reason or "excluded"))
            continue
        if url != target_url and relevance.score < min_score:
            log.info(
                "candidate_skipped",
                extra={"url": url, "reason": "below minimum relevance threshold"},
            )
            skipped.append(SkipRecord(url, "below minimum relevance threshold"))
            continue
        if is_low_value_category(preliminary.category) and not include_low_value:
            log.info(
                "candidate_skipped",
                extra={"url": url, "reason": f"low-value category {preliminary.category}"},
            )
            skipped.append(SkipRecord(url, f"low-value category {preliminary.category}"))
            continue
        seen_pages.add(key)
        boosted_score = relevance.score
        if candidate.source_of_discovery == SOURCE_SITEMAP:
            boosted_score += SITEMAP_BONUS
        scored = CandidatePage(
            url=url,
            source_of_discovery=candidate.source_of_discovery,
            sitemap_name=candidate.sitemap_name,
            parent_url=candidate.parent_url,
            depth=candidate.depth,
            preliminary_category=preliminary.category,
            selection_score=boosted_score,
        )
        eligible.append(scored)
        scores[url] = boosted_score
        log.info("candidate_selected", extra={"url": url, "score": relevance.score})

    eligible.sort(
        key=lambda c: (
            -(c.selection_score or 0.0),
            scope_rank(c.url, target_identity, None),
            c.url,
        )
    )
    budget = max(0, budget)

    # Category diversity cap: no single page category may consume more than
    # 60% of the budget.  This prevents one overwhelming section (e.g.
    # doctor-listing pages with thousands of city variants) from starving
    # other evidence types (procedures, treatments, specialities).
    _MAX_CATEGORY_RATIO = 0.6
    max_per_cat = max(1, int(budget * _MAX_CATEGORY_RATIO)) if budget else 0
    cat_counts: dict[str, int] = {}
    selected: list[CandidatePage] = []
    deferred: list[CandidatePage] = []
    for candidate in eligible:
        cat = candidate.preliminary_category or "unknown"
        if cat_counts.get(cat, 0) < max_per_cat:
            selected.append(candidate)
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
        else:
            deferred.append(candidate)
    # Fill remaining budget from deferred (diversity overflow)
    for candidate in deferred:
        if len(selected) >= budget:
            break
        selected.append(candidate)

    selected_urls = {c.url for c in selected}
    for candidate in eligible:
        if candidate.url not in selected_urls:
            skipped.append(SkipRecord(candidate.url, "page budget exceeded"))

    return CandidateSelection(
        candidates=eligible,
        selected=selected,
        skipped=skipped,
        scores=scores,
        discovered_urls=discovered,
    )


# --- keyword matching -------------------------------------------------------


def _score_url_tokens(url: str) -> tuple[float, tuple[FieldMatch, ...]]:
    parts = urlsplit(url)
    segments = [segment.lower() for segment in parts.path.split("/") if segment]
    score = 0.0
    matches: list[FieldMatch] = []
    if segments:
        first = segments[0]
        for group in GROUPS:
            keyword = _first_keyword(group.keywords, first)
            if keyword:
                matches.append(
                    FieldMatch(group.key, group.field_keys, keyword, "section", _SECTION_WEIGHT)
                )
                score += _SECTION_WEIGHT
    for segment in segments:
        for group in GROUPS:
            keyword = _first_keyword(group.keywords, segment)
            if keyword:
                matches.append(
                    FieldMatch(group.key, group.field_keys, keyword, "url", _URL_WEIGHT)
                )
                score += _URL_WEIGHT
    if 1 <= len(segments) <= 2:
        score += _HUB_BONUS
    return score, tuple(matches)


def _add_text_signals(
    text: str,
    weight: float,
    source: str,
    title_regexes: dict[str, re.Pattern[str]],
    score: float,
    matches: tuple[FieldMatch, ...],
) -> tuple[float, tuple[FieldMatch, ...]]:
    existing = {(match.group, match.source) for match in matches}
    result = list(matches)
    for group in GROUPS:
        if (group.key, source) in existing:
            continue
        pattern = title_regexes[group.key]
        match = pattern.search(text)
        if not match:
            continue
        result.append(
            FieldMatch(group.key, group.field_keys, match.group(0).lower(), source, weight)
        )
        score += weight
    return score, tuple(result)


def _first_keyword(keywords: tuple[str, ...], token: str) -> str | None:
    for keyword in keywords:
        if keyword in token:
            return keyword
    return None


def _word_regex(keywords: tuple[str, ...]) -> re.Pattern[str]:
    body = "|".join(re.escape(keyword) for keyword in keywords)
    return re.compile(rf"\b(?:{body})\b", re.I)


# --- exclusion --------------------------------------------------------------


def _url_exclusion(url: str) -> str | None:
    path = urlsplit(url).path.lower()
    for segment in path.split("/"):
        if not segment:
            continue
        for word in segment.split("-"):
            if word in _EXCLUDE_WORDS:
                return f"excluded section keyword {word!r}"
    return None


def _is_unrelated_branch(url: str, target_identity: str | None) -> bool:
    """True when *url* names a sibling branch of the same chain.

    ``/hospital-network/dwarka/`` is unrelated when the target identity is
    ``saket``; ``/hospital-network/saket/...`` is kept.
    """
    if not target_identity:
        return False
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    for index, segment in enumerate(segments):
        if segment not in _BRANCH_SECTIONS:
            continue
        if index + 1 < len(segments) and segments[index + 1] != target_identity:
            return True
    return False


# --- helpers ----------------------------------------------------------------


def page_key(url: str) -> tuple[str, str, str]:
    """Identity key that ignores query parameters (query-string explosion guard).

    Two URLs with the same scheme/host/path are the same page even when one
    carries tracking parameters.
    """
    parts = urlsplit(url)
    return (parts.scheme.lower(), parts.netloc.lower(), parts.path or "/")


def _is_root(url: str) -> bool:
    return urlsplit(url).path in ("", "/")


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.split())


def _meta_description(soup: BeautifulSoup) -> str:
    meta = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if meta is None:
        return ""
    content = meta.get("content")
    return _clean_text(content if isinstance(content, str) else None)
