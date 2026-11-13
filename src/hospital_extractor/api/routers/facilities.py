"""Read endpoints for registered hospital facilities (evidence source A).

These endpoints expose the existing registry service over HTTP. They return
physical facilities (branches), not parent organisations, because comparison
identity is a property of a specific building at a specific address.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from hospital_extractor.api.deps import get_db_session
from hospital_extractor.persistence.exceptions import FacilityNotFoundError
from hospital_extractor.persistence.models import HospitalFacility
from hospital_extractor.persistence.registry.service import RegistryService

__all__ = ["FacilityListResponse", "FacilityOut", "router"]

router = APIRouter(tags=["facilities"])

IDENTITY_NOTE = (
    "Results are physical facilities (branches) from the MedCompare registry. "
    "Each facility has its own identity; a parent organisation name alone does "
    "not identify a branch."
)


class FacilityOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    name: str
    organization_id: uuid.UUID | None = None
    organization_name: str | None = None
    canonical_url: str | None = None
    website_domain: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    postal_code: str | None = None
    status: str


class FacilityListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[FacilityOut]
    identity_note: str = IDENTITY_NOTE
    limit: int
    offset: int


def _to_out(facility: HospitalFacility) -> FacilityOut:
    organization = facility.organization
    return FacilityOut(
        id=facility.id,
        name=facility.name,
        organization_id=facility.organization_id,
        organization_name=organization.name if organization is not None else None,
        canonical_url=facility.canonical_url,
        website_domain=facility.website_domain,
        address=facility.address,
        city=facility.city,
        state=facility.state,
        country=facility.country,
        postal_code=facility.postal_code,
        status=facility.status,
    )


def facility_location(facility: HospitalFacility) -> str | None:
    """Human-readable location used as the column subtitle in a comparison."""
    parts = [facility.city, facility.state, facility.postal_code]
    joined = ", ".join(str(part).strip() for part in parts if part and str(part).strip())
    return joined or None


@router.get("/facilities", response_model=FacilityListResponse)
def list_facilities(
    session: Annotated[Session, Depends(get_db_session)],
    q: str | None = Query(default=None, max_length=200, description="Facility name fragment"),
    city: str | None = Query(default=None, max_length=128),
    state: str | None = Query(default=None, max_length=128),
    status: str = Query(default="active", description="Use 'any' to include inactive facilities"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> FacilityListResponse:
    """Search registered facilities by name, city or state."""
    service = RegistryService(session)
    status_filter = None if status.strip().lower() in {"", "any", "all"} else status
    try:
        facilities = service.list_facilities(
            name_query=q,
            city=city,
            state=state,
            status=status_filter,
            limit=limit,
            offset=offset,
        )
    except SQLAlchemyError as exc:  # pragma: no cover - depends on deployment DB
        raise HTTPException(
            status_code=503,
            detail={
                "error": "registry_unavailable",
                "message": "The hospital registry database is not reachable.",
            },
        ) from exc
    return FacilityListResponse(
        items=[_to_out(facility) for facility in facilities],
        limit=limit,
        offset=offset,
    )


@router.get("/facilities/{facility_id}", response_model=FacilityOut)
def get_facility(
    facility_id: uuid.UUID,
    session: Annotated[Session, Depends(get_db_session)],
) -> FacilityOut:
    """Fetch one registered facility by its immutable identity."""
    service = RegistryService(session)
    try:
        facility = service.get_facility(facility_id)
    except FacilityNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "facility_not_found",
                "message": f"No registered facility with id {facility_id}.",
            },
        ) from exc
    except SQLAlchemyError as exc:  # pragma: no cover - depends on deployment DB
        raise HTTPException(
            status_code=503,
            detail={
                "error": "registry_unavailable",
                "message": "The hospital registry database is not reachable.",
            },
        ) from exc
    return _to_out(facility)
