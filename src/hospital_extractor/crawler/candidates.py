"""Candidate page representation and queue management.

Every URL the crawler might fetch is a :class:`CandidatePage` that records
where it was discovered, which sitemap it came from, its parent page and its
depth. Candidates are deduplicated by normalized URL before crawling.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "CandidatePage",
    "CandidateSource",
    "SkipRecord",
    "dedupe_candidates",
]

SOURCE_SITEMAP = "sitemap"
SOURCE_INTERNAL_LINK = "internal_link"
SOURCE_SEED = "seed"

CandidateSource = str


@dataclass(frozen=True)
class SkipRecord:
    """One URL that was skipped during discovery/selection/crawl, with reason."""

    url: str
    reason: str


@dataclass(frozen=True)
class CandidatePage:
    """One URL the crawler may fetch."""

    url: str
    source_of_discovery: CandidateSource = SOURCE_SEED
    sitemap_name: str | None = None
    parent_url: str | None = None
    depth: int = 0
    preliminary_category: str | None = None
    selection_score: float | None = None


def dedupe_candidates(candidates: list[CandidatePage]) -> list[CandidatePage]:
    """Drop candidates whose normalized URL was already seen.

    The first occurrence of each URL wins; ordering is otherwise preserved.
    """
    seen: set[str] = set()
    result: list[CandidatePage] = []
    for candidate in candidates:
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        result.append(candidate)
    return result


@dataclass
class CandidateQueue:
    """A bounded FIFO of candidates to crawl, deduplicating by URL."""

    max_size: int = 10_000
    _seen: set[str] = field(default_factory=set)
    _items: list[CandidatePage] = field(default_factory=list)

    def add(self, candidate: CandidatePage) -> bool:
        """Add *candidate* unless its URL is already queued/known.

        Returns True when the candidate was newly added.
        """
        if candidate.url in self._seen:
            return False
        if len(self._items) >= self.max_size:
            return False
        self._seen.add(candidate.url)
        self._items.append(candidate)
        return True

    def pop(self) -> CandidatePage | None:
        if not self._items:
            return None
        return self._items.pop(0)

    def __len__(self) -> int:
        return len(self._items)
