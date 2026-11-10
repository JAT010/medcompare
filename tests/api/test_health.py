"""Tests for the Milestone 0 health endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hospital_extractor.api import create_app


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_endpoint_does_not_require_database(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://invalid:invalid@invalid:5432/invalid")
    client = TestClient(create_app())

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
