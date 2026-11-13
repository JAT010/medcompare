"""Persistence-layer domain exceptions."""

from __future__ import annotations

import uuid
from uuid import UUID


class RegistryError(Exception):
    """Base class for registry persistence errors."""


class OrganizationNotFoundError(RegistryError):
    def __init__(self, organization_id: UUID) -> None:
        super().__init__(f"Organization not found: {organization_id}")
        self.organization_id = organization_id


class FacilityNotFoundError(RegistryError):
    def __init__(self, facility_id: UUID) -> None:
        super().__init__(f"Facility not found: {facility_id}")
        self.facility_id = facility_id


class DuplicateCanonicalUrlError(RegistryError):
    def __init__(self, normalized_canonical_url: str, existing_facility_id: UUID) -> None:
        super().__init__(
            "A facility with normalized canonical URL "
            f"'{normalized_canonical_url}' already exists: {existing_facility_id}"
        )
        self.normalized_canonical_url = normalized_canonical_url
        self.existing_facility_id = existing_facility_id


class InvalidOrganizationReferenceError(RegistryError):
    def __init__(self, organization_id: UUID) -> None:
        super().__init__(f"Referenced organization does not exist: {organization_id}")
        self.organization_id = organization_id


class InvalidFacilityUrlError(RegistryError):
    """Raised when a facility canonical URL cannot be normalized/accepted."""


class CrawlRunNotFoundError(RegistryError):
    def __init__(self, crawl_run_id: uuid.UUID) -> None:
        super().__init__(f"Crawl run not found: {crawl_run_id}")
        self.crawl_run_id = crawl_run_id


class CrawlRunOwnershipError(RegistryError):
    def __init__(
        self,
        crawl_run_id: uuid.UUID,
        *,
        facility_id: uuid.UUID | None = None,
        comparison_subject_id: uuid.UUID | None = None,
    ) -> None:
        target = facility_id or comparison_subject_id
        super().__init__(
            f"Crawl run {crawl_run_id} does not belong to the requested "
            f"comparison subject {target}."
        )
        self.crawl_run_id = crawl_run_id
        self.facility_id = facility_id
        self.comparison_subject_id = comparison_subject_id
