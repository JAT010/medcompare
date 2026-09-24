"""Source/provenance metadata models (canonical, storage-safe).

Living in the models package keeps them importable from anywhere in the
pipeline (dataset, storage, comparison, profile) without creating cycles:
they depend only on :mod:`models.base`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hospital_extractor.models.base import utc_now

__all__ = ["FetchStatus", "SourceIdentity", "SourceMetadata", "SourceType"]


class SourceType(StrEnum):
    """Kinds of data-acquisition source an adapter can implement."""

    WEBSITE = "website"
    HOSPITAL_API = "hospital_api"
    MOCK_API = "mock_api"
    JSON_FEED = "json_feed"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        """Every source-type string — the single authoritative list.

        CLI choices, API validation and web-form selectors all derive their
        allowed values from here; never duplicate the literals in a consumer.
        """
        return tuple(member.value for member in cls)

    @property
    def label(self) -> str:
        """User-facing, professional label for badges/UI."""
        return {
            SourceType.WEBSITE: "Website",
            SourceType.HOSPITAL_API: "Hospital API",
            SourceType.MOCK_API: "Demo API",
            SourceType.JSON_FEED: "Imported Data",
        }[self]


class FetchStatus(StrEnum):
    """Outcome of a source fetch."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class SourceIdentity(BaseModel):
    """What a source fetch is *about* (which hospital it targets).

    ``hospital_ref`` is the adapter-specific reference (e.g. a website URL or
    a demo hospital id).  ``hospital_name`` is the discovered/authoritative
    name when known; adapters set it during ``fetch()`` when possible.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    hospital_ref: str
    hospital_name: str | None = None


class SourceMetadata(BaseModel):
    """Provenance describing *how* a dataset was obtained.

    Attached to a :class:`~hospital_extractor.models.dataset.HospitalDataset`
    so storage, profiles, comparison and export can always answer
    "where did this data come from and when?".
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    source_type: SourceType
    source_id: str = ""
    provider: str | None = None
    base_url: str | None = None
    endpoint: str | None = None
    hospital_ref: str = ""
    hospital_name: str | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    status: FetchStatus = FetchStatus.SUCCESS
    raw_payload_metadata: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None