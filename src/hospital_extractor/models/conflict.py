"""Conflict representation for values that disagree across sources."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["Conflict"]


class Conflict(BaseModel):
    """One field whose value differs between sources.

    ``primary`` is the value kept in the dataset (first strong source wins);
    ``alternative`` is the conflicting value. Both source URLs are preserved
    so the later Data Quality export can surface the disagreement. Conflicts
    are never silently resolved.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    category: str
    field: str
    primary: str | None = None
    alternative: str | None = None
    source_urls: list[str] = Field(default_factory=list)
    status: str = "conflicting"
