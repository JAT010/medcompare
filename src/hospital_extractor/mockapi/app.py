"""DEMO Hospital API application.

Completely simulated.  Serves structured, fictional hospital data over REST so
the :class:`~hospital_extractor.sources.mock_api.MockHospitalAPIDataSource`
adapter has a realistic HTTP surface to consume — the same surface a future
*authorized* hospital API would expose.

Never deploy this pointing at, or claiming to be, a real hospital.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from hospital_extractor.mockapi.data import (
    DEMO_HOSPITAL_ID,
    DEMO_HOSPITAL_ID_B,
    LAST_UPDATED,
    mock_hospital_payload,
)

__all__ = ["APP_TITLE", "DEMO_HOSPITAL_ID", "create_mock_app", "app"]

APP_TITLE = "MedCompare Demo Hospital API"


class DemoHospitalSummary(BaseModel):
    """Summary of one fictional hospital served by the demo API."""

    hospital_id: str
    name: str
    city: str | None = None
    provider: str
    last_updated: str


class DemoError(BaseModel):
    error: str
    detail: str = ""


def create_mock_app() -> FastAPI:
    """Build the simulated hospital API (factory form)."""
    application = FastAPI(
        title=APP_TITLE,
        version="0.1.0",
        description=(
            "SIMULATED hospital data service for the MedCompare source-agnostic "
            "architecture demonstration. This is a MOCK — no real hospital or "
            "patient data is involved."
        ),
        docs_url="/demo/docs",
        redoc_url=None,
    )

    @application.get("/demo/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "demo-hospital-api"}

    @application.get("/demo/hospital", response_model=list[DemoHospitalSummary])
    async def list_hospitals() -> list[DemoHospitalSummary]:
        summaries: list[DemoHospitalSummary] = []
        for hospital_id in (DEMO_HOSPITAL_ID, DEMO_HOSPITAL_ID_B):
            payload = mock_hospital_payload(hospital_id)
            if payload is None:
                continue
            hospital = payload["hospital"]
            contact = payload.get("contact") or {}
            assert isinstance(hospital, dict)
            summaries.append(
                DemoHospitalSummary(
                    hospital_id=hospital_id,
                    name=str(hospital["name"]),
                    city=contact.get("city") if isinstance(contact, dict) else None,
                    provider=str(payload["source"]["provider"]),
                    last_updated=str(payload["source"]["last_updated"]),
                )
            )
        return summaries

    @application.get("/demo/hospital/{hospital_id}")
    async def get_hospital(hospital_id: str) -> dict[str, Any]:
        payload = mock_hospital_payload(hospital_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="unknown hospital id")
        source = payload["source"]
        if isinstance(source, dict):
            source = {**source, "retrieved_via": f"GET /demo/hospital/{hospital_id}"}
            payload["source"] = source
        return payload

    @application.get("/demo/hospital/{hospital_id}/{section}")
    async def get_section(
        hospital_id: str, section: Literal["contact", "departments", "specialities", "doctors",
                                          "procedures", "treatments", "facilities", "technologies",
                                          "accreditations"]
    ) -> JSONResponse:
        payload = mock_hospital_payload(hospital_id) or {}
        key = section if section in payload else "specialities"
        source = payload.get("source") or {}
        return JSONResponse(
            {
                "hospital_id": hospital_id,
                "section": section,
                "items": payload.get(key, []),
                "source": {
                    "provider": str(source.get("provider") or ""),
                    "type": "mock_api",
                    "last_updated": LAST_UPDATED.isoformat(),
                },
            }
        )

    return application


app = create_mock_app()