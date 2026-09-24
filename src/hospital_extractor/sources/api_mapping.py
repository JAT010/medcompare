"""Mapping from a structured hospital API payload to the canonical dataset.

Shared by the demo (mock) adapter and the future authorized hospital API
adapter, so the boundary is identical: a validated JSON payload becomes a
:class:`~hospital_extractor.models.dataset.HospitalDataset` via these pure
functions.  The comparison engine never sees the payload.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from hospital_extractor.models.accreditation import Accreditation
from hospital_extractor.models.contact import Contact
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.department import Department
from hospital_extractor.models.doctor import Doctor
from hospital_extractor.models.facility import Facility
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.infrastructure import Infrastructure
from hospital_extractor.models.procedure import Procedure
from hospital_extractor.models.speciality import Speciality
from hospital_extractor.models.technology import Technology
from hospital_extractor.models.treatment import Treatment
from hospital_extractor.sources.base import apply_source_provenance
from hospital_extractor.sources.models import SourceMetadata, SourceType

__all__ = ["MalformedPayloadError", "map_api_payload_to_dataset"]

REQUIRED_KEYS = ("hospital",)


class MalformedPayloadError(ValueError):
    """The API payload is not usable as structured hospital data."""


def _payload_meta(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the optional ``source`` provenance block from a payload."""
    value = payload.get("source")
    return value if isinstance(value, dict) else {}


def map_api_payload_to_dataset(
    payload: dict[str, Any],
    *,
    endpoint: str,
    source_type: SourceType,
    provider: str | None = None,
) -> HospitalDataset:
    """Map a validated structured payload to a :class:`HospitalDataset`."""
    from hospital_extractor.models.quality import DataQuality

    missing = [key for key in REQUIRED_KEYS if key not in payload]
    if missing:
        raise MalformedPayloadError(f"payload missing required keys: {', '.join(missing)}")
    hospital_data = payload.get("hospital")
    if not isinstance(hospital_data, dict):
        raise MalformedPayloadError("payload['hospital'] must be an object")

    try:
        dataset = HospitalDataset()
        dataset.hospital = _hospital(hospital_data, endpoint, source_type)
        dataset.contacts = _contacts(payload.get("contact"), endpoint, source_type)
        dataset.infrastructure = _infrastructure(
            payload.get("infrastructure"), endpoint, source_type
        )
        dataset.departments = _many(
            Department, payload.get("departments"), endpoint, source_type
        )
        dataset.specialities = _many(
            Speciality, payload.get("specialities"), endpoint, source_type
        )
        dataset.doctors = _many(Doctor, payload.get("doctors"), endpoint, source_type)
        dataset.procedures = _many(
            Procedure, payload.get("procedures"), endpoint, source_type
        )
        dataset.treatments = _many(
            Treatment, payload.get("treatments"), endpoint, source_type
        )
        dataset.facilities = _many(
            Facility, payload.get("facilities"), endpoint, source_type
        )
        dataset.technologies = _many(
            Technology, payload.get("technologies"), endpoint, source_type
        )
        dataset.accreditations = _many(
            Accreditation, payload.get("accreditations"), endpoint, source_type
        )
    except ValidationError as exc:
        raise MalformedPayloadError(f"payload failed validation: {exc}") from exc

    source_block = _payload_meta(payload)
    provider_name = str(
        source_block.get("provider") or provider or hospital_data.get("hospital_id") or endpoint
    )
    metadata = SourceMetadata(
        source_type=source_type,
        source_id=f"{source_type.value}:{provider_name}",
        provider=str(source_block.get("provider") or provider or "authorized hospital api"),
        base_url=endpoint,
        endpoint=endpoint,
        hospital_ref=str(hospital_data.get("hospital_id") or ""),
        hospital_name=hospital_data.get("name"),
        raw_payload_metadata={
            key: value
            for key, value in source_block.items()
            if not isinstance(value, (dict, list))
        },
        notes="SIMULATED/MOCK DATA — not a real hospital integration"
        if source_type == SourceType.MOCK_API
        else None,
    )
    dataset.quality = DataQuality(
        pages_considered=1,
        pages_crawled=1,
        pages_classified=1,
        pages_parsed=1,
        pages_extracted=1,
        pages_with_information=1,
        records_extracted=_record_count(dataset),
        categories_discovered=_discovered(dataset),
        categories_empty=_empty(dataset),
        records_by_category=_by_category(dataset),
    )
    apply_source_provenance(dataset, metadata)
    return dataset


def _record_count(dataset: Any) -> int:
    return sum(
        len(list(getattr(dataset, category.key)))
        for category in _categories()
        if category.key != "hospital"
    ) + (1 if dataset.hospital is not None else 0)


def _discovered(dataset: Any) -> list[str]:
    return [category.key for category in _categories() if _has(dataset, category.key)]


def _empty(dataset: Any) -> list[str]:
    return [category.key for category in _categories() if not _has(dataset, category.key)]


def _by_category(dataset: Any) -> dict[str, int]:
    from hospital_extractor.schema.canonical import CATEGORY_LABELS

    return {
        CATEGORY_LABELS[category.key]: len(list(getattr(dataset, category.key)))
        for category in _categories()
        if category.key != "hospital" and getattr(dataset, category.key)
    }


def _has(dataset: Any, key: str) -> bool:
    if key == "hospital":
        return dataset.hospital is not None
    return bool(getattr(dataset, key))


def _categories() -> list[Any]:
    from hospital_extractor.schema.canonical import CANONICAL_CATEGORIES

    return list(CANONICAL_CATEGORIES)


def _hospital(data: dict[str, Any], endpoint: str, source_type: SourceType) -> HospitalOverview:
    return HospitalOverview(
        name=_text(data.get("name")),
        hospital_type=_text(data.get("hospital_type")),
        description=_text(data.get("description")),
        parent_organization=_text(data.get("parent_organization")),
        established_year=_int(data.get("established_year")),
        status=_text(data.get("status")),
        bed_count=_int(data.get("bed_count")),
        doctor_count=_int(data.get("doctor_count")),
        staff_count=_int(data.get("staff_count")),
        website=_text(data.get("website")),
        source_url=f"{endpoint}#/hospital",
        extraction_method="api-json",
        source_strength="high",
        confidence=1.0,
        source_type=source_type.value,
    )


def _contacts(data: Any, endpoint: str, source_type: SourceType) -> list[Contact]:
    if not isinstance(data, dict):
        return []
    return [
        Contact(
            address=_text(data.get("address")),
            locality=_text(data.get("locality")),
            city=_text(data.get("city")),
            state=_text(data.get("state")),
            country=_text(data.get("country")),
            postal_code=_text(data.get("postal_code")),
            phone=_text(data.get("phone")),
            emergency_phone=_text(data.get("emergency_phone")),
            appointment_phone=_text(data.get("appointment_phone")),
            email=_text(data.get("email")),
            maps_url=_text(data.get("maps_url")),
            source_url=f"{endpoint}#/hospital/contact",
            extraction_method="api-json",
            source_strength="high",
            confidence=1.0,
            source_type=source_type.value,
        )
    ]


def _infrastructure(data: Any, endpoint: str, source_type: SourceType) -> list[Infrastructure]:
    if not isinstance(data, dict):
        return []
    return [
        Infrastructure(
            total_beds=_int(data.get("total_beds")),
            icu_beds=_int(data.get("icu_beds")),
            operation_theatres=_int(data.get("operation_theatres")),
            cath_labs=_int(data.get("cath_labs")),
            critical_care_beds=_int(data.get("critical_care_beds")),
            patient_rooms=_int(data.get("patient_rooms")),
            source_url=f"{endpoint}#/hospital/infrastructure",
            extraction_method="api-json",
            source_strength="high",
            confidence=1.0,
            source_type=source_type.value,
        )
    ]


def _many(
    model: type[Any],
    items: Any,
    endpoint: str,
    source_type: SourceType,
) -> list[Any]:
    if not isinstance(items, list):
        return []
    endpoint_suffix = _collection_name(model)
    records: list[Any] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        kwargs: dict[str, Any] = {}
        for raw_key, value in item.items():
            mapped = _FIELD_MAP.get(model, {}).get(raw_key) or raw_key
            kwargs[mapped] = value
        if model is Doctor and isinstance(kwargs.get("experience"), int):
            kwargs["experience"] = f"{kwargs['experience']} years"
        records.append(
            model(
                **kwargs,
                source_url=f"{endpoint}#/hospital/{endpoint_suffix}/{index}",
                page_url=None,
                extraction_method="api-json",
                source_strength="high",
                confidence=1.0,
                source_type=source_type.value,
            )
        )
    return records


_FIELD_MAP: dict[type[Any], dict[str, str]] = {
    Doctor: {"experience_years": "experience"},
}

_COLLECTIONS: dict[type[Any], str] = {
    Department: "departments",
    Speciality: "specialities",
    Doctor: "doctors",
    Procedure: "procedures",
    Treatment: "treatments",
    Facility: "facilities",
    Technology: "technologies",
    Accreditation: "accreditations",
}


def _collection_name(model: type[Any]) -> str:
    return _COLLECTIONS.get(model, "records")


def _text(value: Any) -> str | None:
    return str(value).strip() if isinstance(value, str) and value.strip() else None


def _int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None