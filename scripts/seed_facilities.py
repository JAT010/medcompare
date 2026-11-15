"""Seed initial facilities into the MedCompare PostgreSQL registry."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src is on sys.path
root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir / "src"))

from hospital_extractor.persistence.registry.service import (
    FacilityCreate,
    OrganizationCreate,
    RegistryService,
)
from hospital_extractor.persistence.session import session_scope

SAMPLE_FACILITIES = [
    {
        "org_name": "Apollo Hospitals Group",
        "org_url": "https://www.apollohospitals.com",
        "facilities": [
            {
                "name": "Apollo Hospitals, Greams Road",
                "canonical_url": "https://chennai.apollohospitals.com/greams-road",
                "address": "21 Greams Lane, Off Greams Road",
                "city": "Chennai",
                "state": "Tamil Nadu",
                "postal_code": "600006",
                "country": "India",
            },
            {
                "name": "Apollo Hospital, Bannerghatta Road",
                "canonical_url": "https://bangalore.apollohospitals.com/bannerghatta-road",
                "address": "154/11, Bannerghatta Road, Opp. IIM-B",
                "city": "Bengaluru",
                "state": "Karnataka",
                "postal_code": "560076",
                "country": "India",
            },
        ],
    },
    {
        "org_name": "Max Healthcare",
        "org_url": "https://www.maxhealthcare.in",
        "facilities": [
            {
                "name": "Max Super Speciality Hospital, Saket",
                "canonical_url": "https://www.maxhealthcare.in/hospital-network/max-super-speciality-hospital-saket",
                "address": "1, 2, Press Enclave Road, Saket",
                "city": "New Delhi",
                "state": "Delhi",
                "postal_code": "110017",
                "country": "India",
            },
            {
                "name": "Max Hospital, Gurugram",
                "canonical_url": "https://www.maxhealthcare.in/hospital-network/max-hospital-gurugram",
                "address": "B-Block, Sushant Lok 1",
                "city": "Gurugram",
                "state": "Haryana",
                "postal_code": "122001",
                "country": "India",
            },
        ],
    },
    {
        "org_name": "Northgate Health Group (Demo)",
        "org_url": "https://northgate.example/",
        "facilities": [
            {
                "name": "Northgate General Hospital",
                "canonical_url": "https://northgate.example/branch-main",
                "address": "12 Sector 12",
                "city": "Northbridge",
                "state": "Northbridge State",
                "postal_code": "411001",
                "country": "Testland",
            },
            {
                "name": "Southwind Medical Centre",
                "canonical_url": "https://southwind.example/",
                "address": "45 Ocean Drive",
                "city": "Southmere",
                "state": "Southmere State",
                "postal_code": "411002",
                "country": "Testland",
            },
        ],
    },
]


def seed() -> None:
    print("Connecting to database and running seed...")
    with session_scope() as session:
        service = RegistryService(session)
        existing = service.list_facilities(limit=10)
        if existing:
            print(f"Registry already contains {len(existing)} facilities. Skipping seed.")
            return

        for org_data in SAMPLE_FACILITIES:
            org = service.create_organization(
                OrganizationCreate(
                    name=org_data["org_name"],
                    website_url=org_data.get("org_url"),
                )
            )
            print(f"Created organization: {org.name} ({org.id})")
            for fac in org_data["facilities"]:
                facility = service.register_facility(
                    FacilityCreate(
                        name=fac["name"],
                        organization_id=org.id,
                        canonical_url=fac["canonical_url"],
                        address=fac.get("address"),
                        city=fac.get("city"),
                        state=fac.get("state"),
                        postal_code=fac.get("postal_code"),
                        country=fac.get("country"),
                    )
                )
                print(f"  + Registered facility: {facility.name} ({facility.id})")
    print("Database seeding completed successfully.")


if __name__ == "__main__":
    seed()
