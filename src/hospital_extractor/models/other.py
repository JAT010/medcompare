"""Controlled fallback for hospital information not covered by a canonical type."""

from __future__ import annotations

from hospital_extractor.models.base import EntityRecord


class OtherInformation(EntityRecord):
    """A labelled hospital-level fact that fits no canonical category.

    Used sparingly; prefer a canonical entity whenever one applies.
    """

    label: str | None = None
    value: str | None = None
