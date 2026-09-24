"""Fictional inventory for the DEMO Hospital API.

Every entity here is fabricated for demonstration only.  No real hospital,
physician, patient or private endpoint is referenced.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

DEMO_HOSPITAL_ID = "demo-northstar"
DEMO_HOSPITAL_ID_B = "demo-solstice"
DEMO_PROVIDER = "Northstar Demo Data Services"
LAST_UPDATED = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Second fictional demo hospital — a narrower ortho/cardiac institute, written
# to make the demo comparison show YES / NO / UNKNOWN and differing
# infrastructure and accreditations.
# ---------------------------------------------------------------------------

SOLSTICE_HOSPITAL = {
    "hospital_id": DEMO_HOSPITAL_ID_B,
    "name": "Solstice Heart & Bone Institute",
    "hospital_type": "Single-speciality ortho-cardiac institute",
    "description": (
        "A fictional 420-bed orthopaedics-and-cardiology institute used to "
        "demonstrate the MedCompare platform. Not a real facility."
    ),
    "parent_organization": "Solstice Health Ventures (fictional)",
    "established_year": 2011,
    "bed_count": 420,
    "doctor_count": 22,
    "staff_count": 480,
    "website": "https://demo.solstice.example.in",
    "status": "operational",
}

SOLSTICE_CONTACT = {
    "hospital_id": DEMO_HOSPITAL_ID_B,
    "address": "7 Palm Court, Baner Road",
    "locality": "Baner",
    "city": "Pune",
    "state": "Maharashtra",
    "country": "IN",
    "postal_code": "411045",
    "phone": "+91-20-5050-6060",
    "emergency_phone": "+91-20-5050-6061",
    "appointment_phone": "+91-20-5050-6062",
    "email": "care@solstice.example.in",
    "maps_url": "https://maps.example.in/solstice",
}

SOLSTICE_INFRASTRUCTURE = {
    "hospital_id": DEMO_HOSPITAL_ID_B,
    "total_beds": 420,
    "icu_beds": 45,
    "operation_theatres": 6,
    "cath_labs": 2,
    "critical_care_beds": 45,
    "patient_rooms": 180,
}

SOLSTICE_DEPARTMENTS = [
    {"name": "Cardiology", "category": "Clinical Department",
     "description": "Interventional heart care."},
    {"name": "Orthopaedics & Joint Replacement", "category": "Clinical Department",
     "description": "Joint and bone surgery."},
    {"name": "Emergency Medicine", "category": "Clinical Department",
     "description": "Round-the-clock emergency care."},
]

SOLSTICE_SPECIALITIES = [
    {"name": "Cardiology", "category": "Speciality",
     "description": "Interventional cardiology and diagnostics."},
    {"name": "Orthopaedics", "category": "Speciality",
     "description": "Joint replacement surgery."},
]

SOLSTICE_DOCTORS = [
    {"name": "Dr. Ira Mohanty", "speciality": "Cardiology",
     "designation": "Interventional Cardiologist", "experience_years": 11},
    {"name": "Dr. Dev Patel", "speciality": "Orthopaedics",
     "designation": "Joint Replacement Surgeon", "experience_years": 13},
]

SOLSTICE_PROCEDURES = [
    {"name": "Total Knee Replacement", "speciality": "Orthopaedics",
     "description": "Total knee arthroplasty."},
    {"name": "Total Hip Replacement", "speciality": "Orthopaedics",
     "description": "Total hip arthroplasty."},
    {"name": "Angioplasty & Stenting", "speciality": "Cardiology",
     "description": "Balloon angioplasty with stenting."},
]

SOLSTICE_TREATMENTS = [
    {"name": "Congestive Heart Failure Management", "speciality": "Cardiology",
     "description": "Chronic heart failure care program."},
]

SOLSTICE_FACILITIES = [
    {"name": "24x7 Pharmacy", "category": "Pharmacy",
     "description": "Round-the-clock pharmacy."},
    {"name": "Intensive Care Unit", "category": "Critical Care",
     "description": "45-bed ICU."},
]

SOLSTICE_TECHNOLOGY = [
    {"name": "3 Tesla MRI Scanner", "category": "Diagnostic Imaging",
     "description": "High-field MRI."},
]

SOLSTICE_ACCREDITATIONS = [
    {"name": "ISO 9001:2015 Certification", "organization": "ISO",
     "year": 2020},
]

HOSPITAL = {
    "hospital_id": DEMO_HOSPITAL_ID,
    "name": "Northstar Multispeciality Hospital",
    "hospital_type": "Multi-speciality tertiary care hospital",
    "description": (
        "A fictional 750-bed multi-speciality hospital used to demonstrate "
        "the MedCompare source-agnostic architecture. Not a real facility."
    ),
    "parent_organization": "Northstar Healthcare Group (fictional)",
    "established_year": 2004,
    "bed_count": 750,
    "doctor_count": 40,
    "staff_count": 900,
    "website": "https://demo.northstar.example.in",
    "status": "operational",
}

CONTACT = {
    "hospital_id": DEMO_HOSPITAL_ID,
    "address": "112 Lakeview Road, Koregaon Park",
    "locality": "Koregaon Park",
    "city": "Pune",
    "state": "Maharashtra",
    "country": "IN",
    "postal_code": "411001",
    "phone": "+91-20-4040-5050",
    "emergency_phone": "+91-20-4040-5051",
    "appointment_phone": "+91-20-4040-5052",
    "email": "care@northstar.example.in",
    "maps_url": "https://maps.example.in/northstar",
}

INFRASTRUCTURE = {
    "hospital_id": DEMO_HOSPITAL_ID,
    "total_beds": 750,
    "icu_beds": 90,
    "operation_theatres": 12,
    "cath_labs": 4,
    "critical_care_beds": 90,
    "patient_rooms": 320,
}

DEPARTMENTS = [
    {"name": "Cardiology", "category": "Clinical Department",
     "description": "Heart and vascular care, diagnostics and interventions."},
    {"name": "Orthopaedics & Joint Replacement", "category": "Clinical Department",
     "description": "Bone, joint and arthroplasty services."},
    {"name": "Neurology", "category": "Clinical Department",
     "description": "Disorders of the brain and nervous system."},
    {"name": "Oncology", "category": "Clinical Department",
     "description": "Comprehensive cancer care."},
    {"name": "Gastroenterology", "category": "Clinical Department",
     "description": "Digestive system and liver care."},
    {"name": "Emergency Medicine", "category": "Clinical Department",
     "description": "Round-the-clock emergency and trauma care."},
]

SPECIALITIES = [
    {"name": "Cardiology", "category": "Speciality",
     "description": "Interventional cardiology and cardiac diagnostics."},
    {"name": "Cardiothoracic Surgery", "category": "Speciality",
     "description": "Heart and chest surgery."},
    {"name": "Orthopaedics", "category": "Speciality",
     "description": "Joint replacement and musculoskeletal surgery."},
    {"name": "Neurology", "category": "Speciality",
     "description": "Brain, spine and nervous system medicine."},
    {"name": "Oncology", "category": "Speciality",
     "description": "Medical, surgical and radiation oncology."},
    {"name": "Gastroenterology", "category": "Speciality",
     "description": "Digestive and liver care."},
    {"name": "Nephrology", "category": "Speciality",
     "description": "Kidney care and dialysis."},
]

DOCTORS = [
    {"name": "Dr. Asha Verma", "speciality": "Cardiology",
     "designation": "Senior Consultant Interventional Cardiologist", "experience_years": 18},
    {"name": "Dr. Rohan Iyer", "speciality": "Cardiology",
     "designation": "Consultant Cardiologist", "experience_years": 12},
    {"name": "Dr. Meera Krishnan", "speciality": "Cardiothoracic Surgery",
     "designation": "Chief Cardiac Surgeon", "experience_years": 20},
    {"name": "Dr. Kabir Mehta", "speciality": "Orthopaedics",
     "designation": "Joint Replacement Surgeon", "experience_years": 15},
    {"name": "Dr. Sunita Rao", "speciality": "Neurology",
     "designation": "Senior Neurologist", "experience_years": 16},
    {"name": "Dr. Vikram Bhat", "speciality": "Oncology",
     "designation": "Medical Oncologist", "experience_years": 14},
]

PROCEDURES = [
    {"name": "Total Knee Replacement", "speciality": "Orthopaedics",
     "description": "Total knee arthroplasty for advanced knee arthritis."},
    {"name": "Total Hip Replacement", "speciality": "Orthopaedics",
     "description": "Total hip arthroplasty for hip joint disease."},
    {"name": "Coronary Artery Bypass Grafting (CABG)", "speciality": "Cardiothoracic Surgery",
     "description": "Bypass surgery for blocked coronary arteries."},
    {"name": "Angioplasty & Stenting", "speciality": "Cardiology",
     "description": "Balloon angioplasty with coronary stent placement."},
    {"name": "Heart Valve Replacement", "speciality": "Cardiothoracic Surgery",
     "description": "Aortic or mitral valve replacement surgery."},
    {"name": "Heart Valve Repair", "speciality": "Cardiothoracic Surgery",
     "description": "Surgical repair of a malfunctioning heart valve."},
    {"name": "Pacemaker / ICD Implantation", "speciality": "Cardiology",
     "description": "Implantation of pacemakers and defibrillators."},
    {"name": "Heart Transplant", "speciality": "Cardiothoracic Surgery",
     "description": "Orthotopic heart transplantation."},
]

TREATMENTS = [
    {"name": "Acute Coronary Syndrome Management", "speciality": "Cardiology",
     "description": "Medical management of heart attacks and unstable angina."},
    {"name": "Congestive Heart Failure Management", "speciality": "Cardiology",
     "description": "Care program for chronic heart failure."},
]

FACILITIES = [
    {"name": "24x7 Pharmacy", "category": "Pharmacy",
     "description": "Round-the-clock in-house pharmacy."},
    {"name": "Blood Bank", "category": "Laboratory",
     "description": "Blood storage, typing and cross-match services."},
    {"name": "Cath Lab", "category": "Cardiac Care",
     "description": "Cardiac catheterisation laboratory (4 labs)."},
    {"name": "MRI (3 Tesla)", "category": "Diagnostic Imaging",
     "description": "3 Tesla magnetic resonance imaging."},
    {"name": "Operating Theatres", "category": "Surgical",
     "description": "12 modular operation theatres."},
    {"name": "Intensive Care Unit", "category": "Critical Care",
     "description": "90-bed ICU with critical-care beds."},
]

TECHNOLOGY = [
    {"name": "Da Vinci Robotic Surgery System", "category": "Robotic Surgery",
     "description": "Robot-assisted minimally invasive surgery."},
    {"name": "3 Tesla MRI Scanner", "category": "Diagnostic Imaging",
     "description": "High-field magnetic resonance imaging."},
    {"name": "Cath Lab (Biplane)", "category": "Cardiac Imaging",
     "description": "Biplane angiography imaging."},
]

ACCREDITATIONS = [
    {"name": "NABH Accreditation", "organization": "National Accreditation Board for Hospitals",
     "year": 2015},
    {"name": "ISO 9001:2015 Certification", "organization": "ISO",
     "year": 2019},
]


def mock_hospital_payload(hospital_id: str) -> dict[str, Any] | None:
    """Full structured payload for a fictional hospital, or ``None`` if unknown."""
    if hospital_id == DEMO_HOSPITAL_ID:
        return {
            "hospital": HOSPITAL,
            "contact": CONTACT,
            "infrastructure": INFRASTRUCTURE,
            "departments": DEPARTMENTS,
            "specialities": SPECIALITIES,
            "doctors": DOCTORS,
            "procedures": PROCEDURES,
            "treatments": TREATMENTS,
            "facilities": FACILITIES,
            "technologies": TECHNOLOGY,
            "accreditations": ACCREDITATIONS,
            "source": {
                "provider": DEMO_PROVIDER,
                "type": "mock_api",
                "last_updated": LAST_UPDATED.isoformat(),
            },
        }
    if hospital_id == DEMO_HOSPITAL_ID_B:
        return {
            "hospital": SOLSTICE_HOSPITAL,
            "contact": SOLSTICE_CONTACT,
            "infrastructure": SOLSTICE_INFRASTRUCTURE,
            "departments": SOLSTICE_DEPARTMENTS,
            "specialities": SOLSTICE_SPECIALITIES,
            "doctors": SOLSTICE_DOCTORS,
            "procedures": SOLSTICE_PROCEDURES,
            "treatments": SOLSTICE_TREATMENTS,
            "facilities": SOLSTICE_FACILITIES,
            "technologies": SOLSTICE_TECHNOLOGY,
            "accreditations": SOLSTICE_ACCREDITATIONS,
            "source": {
                "provider": DEMO_PROVIDER,
                "type": "mock_api",
                "last_updated": LAST_UPDATED.isoformat(),
            },
        }
    return None