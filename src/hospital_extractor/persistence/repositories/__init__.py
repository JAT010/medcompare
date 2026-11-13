"""Repository implementations for the hospital registry."""

from hospital_extractor.persistence.repositories.facilities import FacilityRepository
from hospital_extractor.persistence.repositories.organizations import OrganizationRepository

__all__ = ["FacilityRepository", "OrganizationRepository"]
