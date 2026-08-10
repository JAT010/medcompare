"""Base model shared by every extracted entity.

Every record carries its source provenance so the pipeline can always answer
"where did this value come from?".
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class EntityRecord(BaseModel):
    """Base class for all extracted entities.

    - ``source_url``: page the value was extracted from.
    - ``page_title``: title of that page, when available.
    - ``extracted_at``: when the record was created (UTC).
    - ``context``: short surrounding text snippet for human verification.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    source_url: str
    page_title: str | None = None
    extracted_at: datetime = Field(default_factory=utc_now)
    context: str | None = None


class NamedEntity(EntityRecord):
    """Common shape for name + category + description entities.

    Used by Facility, Department, Speciality, Treatment, Procedure,
    Technology, PatientService and Program records.
    """

    name: str | None = None
    category: str | None = None
    description: str | None = None
