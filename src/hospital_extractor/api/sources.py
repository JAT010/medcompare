"""P21 source / connector introspection API.

Read-only listing of registered source types and connectors, plus a safe
authorization probe for a *configured* source type.  Clients cannot supply an
arbitrary external URL for the server to fetch — only registered source types
are accepted, and acquisition is refused unless the source is authorized.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from hospital_extractor.sources.registry import (
    SourceConfig,
    get_default_registry,
    load_sources_config,
)
from hospital_extractor.storage.database import default_database_path

__all__ = ["sources_router"]

sources_router = APIRouter()


def _open_db_path(request: Request) -> Any:
    return getattr(request.app.state, "medcompare_db_path", default_database_path())


@sources_router.get("/api/v1/sources")
async def api_list_sources() -> JSONResponse:
    """List registered source type definitions (no secrets)."""
    registry = get_default_registry()
    rows = []
    for definition in registry.definitions():
        try:
            connector = registry.connector_for(definition.source_type)
            connector_name = connector.__name__
        except KeyError:
            connector_name = ""
        rows.append(
            {
                "source_type": definition.source_type.value,
                "label": definition.display_name,
                "simulated": definition.simulated,
                "requires_authorization": definition.requires_authorization,
                "authorization_status": (
                    "UNAUTHORIZED"
                    if definition.requires_authorization
                    else ("SIMULATED / MOCK" if definition.simulated else "PUBLIC")
                ),
                "connector": connector_name,
            }
        )
    return JSONResponse(content={"sources": rows})


@sources_router.get("/api/v1/connectors")
async def api_list_connectors() -> JSONResponse:
    """List connector classes resolved by the source registry."""
    registry = get_default_registry()
    rows = []
    for source_type in registry.source_types():
        try:
            connector = registry.connector_for(source_type)
            name = connector.__name__
            resolved = True
        except KeyError:
            name = ""
            resolved = False
        definition = registry.definition(source_type)
        rows.append(
            {
                "source_type": source_type,
                "connector": name,
                "resolved": resolved,
                "simulated": definition.simulated,
                "requires_authorization": definition.requires_authorization,
            }
        )
    return JSONResponse(content={"connectors": rows})


@sources_router.get("/api/v1/sources/configured")
async def api_list_configured_sources() -> JSONResponse:
    """List entries from ``config/sources.toml`` (credential *names* only)."""
    registry = get_default_registry()
    rows = []
    for config in load_sources_config():
        definition = registry.definition(config.source_type)
        if definition.requires_authorization and not config.authorized:
            status = "UNAUTHORIZED"
        elif definition.requires_authorization:
            status = "AUTHORIZED"
        elif definition.simulated:
            status = "SIMULATED / MOCK"
        else:
            status = "PUBLIC"
        rows.append(
            {
                "name": config.name,
                "source_type": config.source_type,
                "hospital_ref": config.hospital_ref,
                "base_url": config.base_url,
                "provider": config.provider,
                "authorization_status": status,
                "credential_ref": f"env:{config.credential_env}"
                if config.credential_env
                else None,
                "allowed_hosts": list(config.allowed_hosts),
            }
        )
    return JSONResponse(content={"configured_sources": rows})


@sources_router.get("/api/v1/sources/{source_type}/test")
async def api_test_source(source_type: str) -> JSONResponse:
    """Offline authorization/config probe for a registered source type.

    Never accepts an arbitrary URL.  Never performs a network request.
    Returns 404 for unknown types, 401-style unauthorized status for types
    that require authorization but are not configured as authorized.
    """
    registry = get_default_registry()
    if not registry.has(source_type):
        return JSONResponse(
            status_code=404,
            content={"error": "Unknown source type", "detail": source_type},
        )
    definition = registry.definition(source_type)
    config = SourceConfig(
        source_type=source_type,
        hospital_ref="probe",
        authorized=not definition.requires_authorization,
    )
    policy = config.authorization_policy(
        requires_authorization=definition.requires_authorization
    )
    try:
        connector_name = registry.connector_for(source_type).__name__
    except KeyError:
        connector_name = ""
    authorization = dict(policy.safe_dict())
    body: dict[str, Any] = {
        "source_type": source_type,
        "connector": connector_name,
        "simulated": definition.simulated,
        "requires_authorization": definition.requires_authorization,
        "authorization": authorization,
        "fetch_allowed": False,
        "note": "offline probe only — no external request is made",
    }
    if definition.requires_authorization and not config.authorized:
        authorization["status"] = "UNAUTHORIZED"
        return JSONResponse(status_code=401, content=body)
    return JSONResponse(content=body)
