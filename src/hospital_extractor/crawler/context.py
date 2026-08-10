"""Crawl/extraction context for a single hospital website.

The input URL is the authority for the hospital being extracted. The context
carries everything the crawl needs to stay scoped to one hospital and one
site: the normalized target URL, the domain boundary, the hospital identity
(slug) when derivable, and the configured crawl limits.

The crawler must never merge two distinct hospitals (e.g. the two Saket
hospitals). ``target_hospital_identity`` is derived from the input URL only;
later extraction stages must still verify that any entity found on the site
is actually affiliated with this hospital.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from hospital_extractor.crawler.crawler import (
    DEFAULT_DELAY_SECONDS,
    DEFAULT_MAX_PAGES,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
)
from hospital_extractor.crawler.discovery import hospital_slug_from_url
from hospital_extractor.utils.urls import validate_url

__all__ = ["CrawlContext", "CrawlLimits", "build_context"]

DEFAULT_FALLBACK_USER_AGENTS = (
    "Mozilla/5.0 (compatible; UniversalHospitalExtractor/0.1; "
    "+https://github.com/kartik00052/hospital-website-extractor)",
    "curl/8.9.1",
)

DEFAULT_MAX_DEPTH = 2
DEFAULT_MAX_RESPONSE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class CrawlLimits:
    """Safe, configurable limits that keep a crawl bounded."""

    max_pages: int = DEFAULT_MAX_PAGES
    max_depth: int = DEFAULT_MAX_DEPTH
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_RETRIES
    delay: float = DEFAULT_DELAY_SECONDS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES


@dataclass(frozen=True)
class CrawlContext:
    """Everything a single-hospital crawl needs to know about its target."""

    target_url: str
    target_domain: str
    target_hospital_identity: str | None
    normalized_target_path: str
    user_agents: tuple[str, ...]
    crawl_limits: CrawlLimits

    @property
    def user_agent(self) -> str:
        """The primary (first) user-agent."""
        return self.user_agents[0]


def build_context(
    url: str,
    *,
    limits: CrawlLimits | None = None,
    user_agent: str | None = None,
    fallback_user_agents: tuple[str, ...] = DEFAULT_FALLBACK_USER_AGENTS,
) -> CrawlContext:
    """Validate *url* and build a :class:`CrawlContext`.

    ``user_agent`` (when given) becomes the primary UA; ``fallback_user_agents``
    are tried in order if the pre-crawl probe reports the target inaccessible
    with the primary UA.
    """
    target_url = validate_url(url)
    parts = urlsplit(target_url)
    agents = (user_agent or DEFAULT_USER_AGENT, *fallback_user_agents)
    return CrawlContext(
        target_url=target_url,
        target_domain=parts.hostname or "",
        target_hospital_identity=hospital_slug_from_url(target_url),
        normalized_target_path=parts.path,
        user_agents=agents,
        crawl_limits=limits or CrawlLimits(),
    )
