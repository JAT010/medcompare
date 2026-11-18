"""Website evidence view consumed by the verification service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

__all__ = ["WebsiteEvidence"]


@dataclass(frozen=True)
class WebsiteEvidence:
    """Evidence harvested from the facility's website (or its absence)."""

    status: str  # completed | completed_with_warnings | failed | timed_out | empty | unavailable
    target_url: str
    crawl_run_id: uuid.UUID | None
    hospital_name: str | None = None
    pages_fetched: int = 0
    retrieved_at: datetime | None = None
    evidence: list[Any] = field(default_factory=list)  # EvidenceItem-like
    warnings: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.status in {"completed", "completed_with_warnings"} and bool(
            self.evidence or self.hospital_name
        )
