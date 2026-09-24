"""Source provenance record used for the Sources worksheet."""

from __future__ import annotations

from hospital_extractor.models.base import EntityRecord


class SourceRecord(EntityRecord):
    """One row of extraction provenance.

    Generated from the ``source_url``/``page_title``/``context`` carried by
    every extracted record. ``entity`` names the canonical category the record
    belongs to (e.g. ``Doctors``).
    """

    entity: str | None = None
