"""URL eligibility filtering for the crawler.

A URL is eligible for a crawl only when it is on the same domain as the
hospital, is fetchable HTML (not a binary asset), matches none of the
explicit path/query exclusions, and is allowed by the site's ``robots.txt``.

The robots.txt parser implements the common subset of RFC 9309: user-agent
group selection (exact match, then prefix match, then ``*``), ``Allow`` /
``Disallow`` prefix rules, ``*`` wildcards and trailing ``$`` anchors. The
longest matching rule wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from hospital_extractor.utils.urls import is_fetchable, is_same_domain


@dataclass(frozen=True)
class RobotsRule:
    """One ``Allow``/``Disallow`` rule extracted from ``robots.txt``."""

    pattern: str
    allow: bool

    def matches(self, target: str) -> bool:
        """Return True when *target* (path + query) matches this rule."""
        return _pattern_matches(self.pattern, target)


@dataclass(frozen=True)
class RobotsDecision:
    """Result of a robots.txt eligibility check for one URL."""

    url: str
    allowed: bool
    reason: str


class RobotsPolicy:
    """Resolved robots.txt policy for a single crawler user-agent."""

    def __init__(
        self,
        user_agent: str,
        rules: list[RobotsRule],
        *,
        sitemaps: tuple[str, ...] = (),
    ) -> None:
        self.user_agent = user_agent
        self._rules = tuple(rules)
        self._sitemaps = sitemaps

    def can_fetch(self, url: str) -> bool:
        """Return True if the site allows the crawler to fetch *url*.

        The longest matching rule wins; ``Allow`` rules only override
        ``Disallow`` rules when they are more specific (longer pattern).
        When no rule matches, fetching is allowed.
        """
        return self.can_fetch_reason(url).allowed

    def can_fetch_reason(self, url: str) -> RobotsDecision:
        """Like :meth:`can_fetch` but explains the decision.

        The reason names the longest matching rule, or the rule that most
        specifically matched the URL.
        """
        target = _match_target(url)
        matching = [rule for rule in self._rules if rule.matches(target)]
        if not matching:
            return RobotsDecision(url=url, allowed=True, reason="no matching rule")
        best = max(matching, key=lambda rule: len(rule.pattern))
        verb = "Allow" if best.allow else "Disallow"
        reason = f"{verb} rule {best.pattern!r} matched target {target!r}"
        return RobotsDecision(url=url, allowed=best.allow, reason=reason)

    @property
    def rules(self) -> tuple[RobotsRule, ...]:
        return self._rules

    @property
    def sitemaps(self) -> tuple[str, ...]:
        """Sitemap URLs declared anywhere in ``robots.txt``."""
        return self._sitemaps


def parse_robots_txt(content: str, user_agent: str) -> RobotsPolicy:
    """Parse *content* into a :class:`RobotsPolicy` for *user_agent*.

    ``Disallow:`` with an empty value means "allow everything" and is
    dropped. Rules are grouped per user-agent; the most specific group for
    *user_agent* is selected (exact name, then prefix, then ``*``).
    ``Sitemap:`` lines are global and collected onto the policy.
    """
    groups: list[tuple[list[str], list[RobotsRule]]] = []
    agents: list[str] = []
    rules: list[RobotsRule] = []
    sitemaps: list[str] = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if agents:
                groups.append((agents, rules))
                agents = []
                rules = []
            if value:
                agents.append(value)
        elif key == "sitemap":
            if value:
                sitemaps.append(value)
        elif key in ("allow", "disallow"):
            if not agents or not value:
                continue
            rules.append(RobotsRule(pattern=value, allow=(key == "allow")))
    if agents:
        groups.append((agents, rules))

    return RobotsPolicy(
        user_agent=user_agent,
        rules=_select_group_rules(groups, user_agent),
        sitemaps=tuple(sitemaps),
    )


def _select_group_rules(
    groups: list[tuple[list[str], list[RobotsRule]]], user_agent: str
) -> list[RobotsRule]:
    best_score = -1
    selected: list[RobotsRule] = []
    for agents, rules in groups:
        score = _agent_match_score(agents, user_agent)
        if score > best_score:
            best_score = score
            selected = list(rules)
        elif score == best_score and score >= 0:
            selected.extend(rules)
    return selected


def _agent_match_score(agents: list[str], user_agent: str) -> int:
    ua = user_agent.lower()
    scores: list[int] = []
    for agent in agents:
        name = agent.lower()
        if name == "*":
            scores.append(0)
        elif name == ua:
            scores.append(2)
        elif ua.startswith(name) or name.startswith(ua):
            scores.append(1)
    return max(scores, default=-1)


def _match_target(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        return f"{path}?{parts.query}"
    return path


def _pattern_matches(pattern: str, target: str) -> bool:
    if not pattern:
        return True
    if "*" in pattern or pattern.endswith("$"):
        return _wildcard_regex(pattern).match(target) is not None
    return target.startswith(pattern)


def _wildcard_regex(pattern: str) -> re.Pattern[str]:
    body = pattern[:-1] if pattern.endswith("$") else pattern
    end = "$" if pattern.endswith("$") else ""
    escaped = "".join(".*" if ch == "*" else re.escape(ch) for ch in body)
    return re.compile(f"^{escaped}{end}")


@dataclass(frozen=True)
class FilterDecision:
    """Result of a full crawl-eligibility check for one URL."""

    url: str
    allowed: bool
    reason: str


class UrlFilter:
    """Decides whether a discovered URL may be fetched during a crawl.

    The URL is expected to be already normalized (see
    ``hospital_extractor.utils.urls.normalize_url``).
    """

    def __init__(
        self,
        base_url: str,
        *,
        robots: RobotsPolicy | None = None,
        allow_subdomains: bool = False,
        block_path_prefixes: tuple[str, ...] = (),
        block_query_params: tuple[str, ...] = (),
    ) -> None:
        self._base_url = base_url
        self._robots = robots
        self._allow_subdomains = allow_subdomains
        self._block_path_prefixes = block_path_prefixes
        self._block_query_params = block_query_params

    def is_allowed(self, url: str) -> bool:
        """Return True when *url* is eligible to be fetched."""
        return self.decision(url).allowed

    def decision(self, url: str) -> FilterDecision:
        """Return whether *url* may be fetched and the reason why."""
        if not is_same_domain(url, self._base_url, allow_subdomains=self._allow_subdomains):
            return FilterDecision(url=url, allowed=False, reason="off-domain")
        if not is_fetchable(url):
            return FilterDecision(url=url, allowed=False, reason="non-fetchable asset")

        parts = urlsplit(url)
        path = parts.path or "/"
        for prefix in self._block_path_prefixes:
            if path.startswith(prefix):
                return FilterDecision(
                    url=url, allowed=False, reason=f"blocked path prefix {prefix!r}"
                )

        query = parts.query or ""
        for name in self._block_query_params:
            if _has_query_param(query, name):
                return FilterDecision(
                    url=url, allowed=False, reason=f"blocked query parameter {name!r}"
                )

        if self._robots is not None:
            robots_decision = self._robots.can_fetch_reason(url)
            if not robots_decision.allowed:
                return FilterDecision(
                    url=url, allowed=False, reason=f"robots: {robots_decision.reason}"
                )
        return FilterDecision(url=url, allowed=True, reason="allowed")


def _has_query_param(query: str, name: str) -> bool:
    for pair in query.split("&"):
        key, _, _ = pair.partition("=")
        if key == name:
            return True
    return False
