"""Base interface every hospital data source implements.

A *data source* is any capability that can produce a canonical
:class:`~hospital_extractor.models.dataset.HospitalDataset`:

- :class:`WebsiteDataSource` — an existing crawler + extraction run.
- :class:`HospitalAPIDataSource` — a configured authorized hospital API.
- :class:`MockHospitalAPIDataSource` — the local demo API.
- :class:`JSONFeedDataSource` — a previously persisted canonical dataset.

Downstream consumers (comparison, storage, profile, export) only ever see
the canonical dataset; they never learn which physical source produced it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from hospital_extractor.models.base import EntityRecord
from hospital_extractor.models.dataset import ENTITY_FIELDS, HospitalDataset
from hospital_extractor.models.source import SourceRecord
from hospital_extractor.schema.canonical import CANONICAL_CATEGORIES
from hospital_extractor.schema.discovery import category_records
from hospital_extractor.sources.models import SourceIdentity, SourceMetadata, SourceType

__all__ = [
    "ENTITY_FIELDS_REGISTRY",
    "HospitalDataSource",
    "apply_source_provenance",
    "rebuild_sources",
]


class HospitalDataSource(ABC):
    """Acquisition boundary for hospital intelligence data.

    Implementors expose :attr:`source_type` and :meth:`identity`, and
    :meth:`fetch` returns a fully-formed canonical :class:`HospitalDataset`.
    """

    source_type: SourceType

    @abstractmethod
    def identity(self) -> SourceIdentity:
        """Return what this source is configured to fetch."""

    @abstractmethod
    async def fetch(self) -> HospitalDataset:
        """Fetch and map the hospital data into canonical form.

        The returned dataset must have ``source`` populated and every entity
        record tagged with ``source_type`` (see :func:`apply_source_provenance`).

        Raises :class:`SourceFetchError` on unrecoverable failure.
        """


class SourceFetchError(RuntimeError):
    """The source could not produce a dataset."""


def apply_source_provenance(
    dataset: HospitalDataset,
    metadata: SourceMetadata,
    *,
    rebuild: bool = True,
) -> HospitalDataset:
    """Tag every record and the dataset itself with *metadata*.

    Sets ``dataset.source`` and stamps ``source_type`` on every canonical
    record so provenance survives each adapter. When ``rebuild`` is true the
    dataset's provenance worksheet (``sources``) is regenerated from the
    records.
    """
    dataset.source = metadata
    for category in CANONICAL_CATEGORIES:
        for record in category_records(dataset, category):
            if isinstance(record, EntityRecord):
                record.source_type = metadata.source_type.value
    if rebuild:
        dataset.sources = rebuild_sources(dataset)
    return dataset


def rebuild_sources(dataset: HospitalDataset) -> list[SourceRecord]:
    """Regenerate the provenance worksheet from the extracted records."""
    sources: list[SourceRecord] = []
    seen: set[tuple[str, str, str]] = set()
    for category in CANONICAL_CATEGORIES:
        for record in category_records(dataset, category):
            data = _record_data(record)
            key = (category.label, str(data.get("source_url", "")), str(data.get("context", "")))
            if key in seen:
                continue
            seen.add(key)
            sources.append(
                SourceRecord(
                    entity=category.label,
                    source_url=data.get("source_url") or "",
                    page_title=data.get("page_title"),
                    context=data.get("context"),
                    extraction_method=data.get("extraction_method"),
                    source_strength=data.get("source_strength"),
                    confidence=data.get("confidence"),
                    source_type=_source_type_for(dataset, data),
                )
            )
    return sources


def _source_type_for(dataset: HospitalDataset, data: dict[str, object]) -> str:
    recorded = data.get("source_type")
    if recorded:
        return str(recorded)
    source = dataset.source
    return source.source_type.value if source is not None else ""


def _record_data(record: object) -> dict[str, object]:
    dump = getattr(record, "model_dump", None)
    if callable(dump):
        try:
            return dict(dump(exclude={"extracted_at"}))
        except TypeError:
            return dict(dump())
    return dict(vars(record))


# Re-exported for convenience: every canonical category key -> model class.
ENTITY_FIELDS_REGISTRY: dict[str, type] = dict(ENTITY_FIELDS)