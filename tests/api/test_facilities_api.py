"""Tests for the registered-facility read endpoints (evidence source A)."""

from __future__ import annotations

import uuid

from hospital_extractor.api.routers.facilities import IDENTITY_NOTE

NORTHGATE = "Northgate General Hospital"
SOUTHWIND = "Southwind Medical Centre"


def test_list_returns_physical_facilities_with_identity_note(client, seeded) -> None:
    response = client.get("/api/v1/facilities")

    assert response.status_code == 200
    body = response.json()
    assert body["identity_note"] == IDENTITY_NOTE
    names = [item["name"] for item in body["items"]]
    assert names == [NORTHGATE, SOUTHWIND]
    northgate = body["items"][0]
    assert northgate["city"] == "Northbridge"
    assert northgate["organization_name"] == "Northgate Health Group"
    assert northgate["website_domain"] == "northgate.example"
    assert northgate["status"] == "active"


def test_list_filters_by_name_fragment(client, seeded) -> None:
    response = client.get("/api/v1/facilities", params={"q": "southwind"})

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["name"] for item in items] == [SOUTHWIND]


def test_list_filters_by_city(client, seeded) -> None:
    response = client.get("/api/v1/facilities", params={"city": "Northbridge"})

    assert response.status_code == 200
    assert [item["name"] for item in response.json()["items"]] == [NORTHGATE]


def test_list_rejects_out_of_range_pagination(client) -> None:
    assert client.get("/api/v1/facilities", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/facilities", params={"offset": -1}).status_code == 422


def test_detail_returns_single_facility_by_identity(client, seeded) -> None:
    facility = seeded["northgate"]

    response = client.get(f"/api/v1/facilities/{facility.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(facility.id)
    assert body["name"] == NORTHGATE
    assert body["address"] == "12 Sector 12"
    assert body["postal_code"] == "411001"


def test_detail_404s_for_unknown_facility(client) -> None:
    response = client.get(f"/api/v1/facilities/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "facility_not_found"


def test_detail_422s_for_malformed_identity(client) -> None:
    response = client.get("/api/v1/facilities/not-a-uuid")

    assert response.status_code == 422
