"""Honest capability report for the comparison feature.

Every capability the comparison UI can offer is described here from the code
that actually exists. A capability that is not implemented reports
``available=False`` together with the precise backend integration required, so
the interface can say what is missing instead of pretending to work.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from hospital_extractor.comparison.schemas import CapabilityReport, CapabilityStatus

__all__ = ["assess_capabilities"]

_SEARCH_PROVIDER_VARS = ("MEDCOMPARE_SEARCH_PROVIDER", "TAVILY_API_KEY", "SERPAPI_API_KEY")
_SEARCH_CLIENT_VARS = ("MEDCOMPARE_SEARCH_API_KEY", "BING_SEARCH_API_KEY", "BRAVE_SEARCH_API_KEY")
_MODEL_KEY_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MEDCOMPARE_MODEL_API_KEY")

_SEARCH_INTEGRATION = (
    "A server-side search provider client (for example Tavily, SerpAPI or Bing Web "
    "Search) with its API key supplied through the server environment "
    "(MEDCOMPARE_SEARCH_PROVIDER / MEDCOMPARE_SEARCH_API_KEY). The key must stay on "
    "the server; no search credential belongs in frontend code."
)
_JOB_STORE_INTEGRATION = (
    "A persistent job store: an Alembic migration adding crawl job rows, a worker "
    "that executes queued extractions, and status/result endpoints "
    "(Milestone 3: jobs and datasets)."
)
_MODEL_INTEGRATION = (
    "A server-side language-model client with credentials (for example "
    "MEDCOMPARE_MODEL_API_KEY). This codebase ships no AI SDK, so interpretation "
    "is deterministic parsing today."
)


def _any_set(names: tuple[str, ...], environ: Mapping[str, str]) -> bool:
    return any(environ.get(name) for name in names)


def assess_capabilities(environ: Mapping[str, str] | None = None) -> CapabilityReport:
    """Describe which comparison capabilities this deployment can actually serve."""
    env = os.environ if environ is None else environ
    search_env_configured = _any_set(_SEARCH_PROVIDER_VARS, env) or _any_set(
        _SEARCH_CLIENT_VARS, env
    )
    model_key_configured = _any_set(_MODEL_KEY_VARS, env)

    search_detail = (
        "No search provider client exists in this codebase: there is no search "
        "dependency, no search configuration and no search endpoint. Public "
        "internet research cannot be performed by this deployment."
    )
    if search_env_configured:
        search_detail += (
            " Search credentials appear to be set in the environment, but no client "
            "code reads them yet."
        )

    model_detail = (
        "No AI/model integration is present, so ambiguous or open-ended "
        "requests cannot be rephrased by a model. Interpretation is deterministic "
        "clause parsing."
    )
    if model_key_configured:
        model_detail += (
            " Model credentials appear to be set in the environment, but no client "
            "code reads them yet."
        )

    capabilities = [
        CapabilityStatus(
            key="registered_hospital_retrieval",
            label="Registered hospital retrieval",
            available=True,
            detail=(
                "Facilities are read from the hospital registry tables through the "
                "existing service layer."
            ),
            implementation="GET /api/v1/facilities and GET /api/v1/facilities/{facility_id}",
        ),
        CapabilityStatus(
            key="standard_comparison",
            label="Standard comparison",
            available=True,
            detail=(
                "The twelve standard criteria are evaluated against stored evidence; "
                "criteria without evidence are reported as unavailable, never as no."
            ),
            implementation="POST /api/v1/comparison",
        ),
        CapabilityStatus(
            key="custom_prompt_interpretation",
            label="Custom prompt interpretation",
            available=True,
            detail=(
                "User wording is parsed deterministically into criteria with evidence "
                "requirements and clarification questions."
            ),
            implementation="POST /api/v1/comparison/interpret",
        ),
        CapabilityStatus(
            key="database_website_consistency_audit",
            label="Database-to-website consistency audit",
            available=True,
            detail=(
                "Compares the stored registry record of one physical facility with a "
                "persisted website extraction. Findings distinguish consistent, differing, "
                "missing and potentially outdated information; missing data is never "
                "treated as proof of absence."
            ),
            implementation=(
                "POST /api/v1/consistency/audit and "
                "GET /api/v1/consistency/audit/{facility_id}"
            ),
        ),
        CapabilityStatus(
            key="authorized_hospital_system_integration",
            label="Authorized hospital-system integration",
            available=False,
            detail=(
                "No live hospital API or database connection is configured. A capture "
                "subsystem exists (versioned immutable snapshots, field-level change "
                "detection, deterministic verification) and a clearly-labelled simulated "
                "connector ships for demos and tests, but no real hospital is connected "
                "by default. Live captures require an authorized integration with an "
                "allowlisted base_url and environment-backed credentials."
            ),
            required_integration=(
                "An authorized hospital API/database connection with scoped credentials, "
                "access controls and an agreed data contract. Verification of service "
                "fields against the hospital's own database depends on this integration."
            ),
        ),
        CapabilityStatus(
            key="hospital_source_capture",
            label="Authorized hospital-source capture",
            available=True,
            detail=(
                "Integrations are facility-scoped and fail closed unless explicitly "
                "enabled with an allowlist and environment-backed credentials. Captures "
                "are versioned, immutable and fingerprinted; detected changes are "
                "persisted. The available connector is the clearly-labelled SIMULATED "
                "mock hospital API used for tests and demos — no real hospital is "
                "connected by default."
            ),
            implementation=(
                "POST /api/v1/facilities/{facility_id}/integrations/{integration_id}/capture"
            ),
        ),
        CapabilityStatus(
            key="persistent_verification_jobs",
            label="Persistent verification jobs",
            available=True,
            detail=(
                "A separate worker claims due jobs (PostgreSQL FOR UPDATE SKIP LOCKED, "
                "leases, bounded retries) and compares detected hospital changes against "
                "SSRF-protected website evidence. Outcomes are match, mismatch or "
                "insufficient_evidence with full provenance; absence of evidence is never "
                "reported as a mismatch. Sensitive operations require the operator token."
            ),
            implementation="python -m hospital_extractor.worker",
        ),
        CapabilityStatus(
            key="scheduled_monitoring",
            label="Scheduled monitoring",
            available=True,
            detail=(
                "Enabled schedules periodically run the capture pipeline and create "
                "verification jobs for detected changes. Monitoring never crawls websites "
                "itself; every scheduler decision is recorded immutably."
            ),
            implementation=(
                "GET /api/v1/facilities/{facility_id}/monitoring/schedules and "
                "POST /api/v1/facilities/{facility_id}/monitoring/schedules"
            ),
        ),
        CapabilityStatus(
            key="reconciliation_review",
            label="Reconciliation and human review",
            available=True,
            detail=(
                "Inconclusive or contradicted verification results open review cases in an "
                "append-only workflow (open → under_review → resolved/dismissed) with an "
                "immutable event trail. Resolution is a decision about the evidence; "
                "nothing is published to the hospital."
            ),
            implementation=(
                "GET /api/v1/facilities/{facility_id}/reconciliation-cases and "
                "POST /api/v1/facilities/{facility_id}/reconciliation-cases/{case_id}/transitions"
            ),
        ),
        CapabilityStatus(
            key="model_assisted_interpretation",
            label="Model-assisted interpretation",
            available=False,
            detail=model_detail,
            required_integration=_MODEL_INTEGRATION,
        ),
        CapabilityStatus(
            key="public_internet_search",
            label="Public internet search",
            available=False,
            detail=search_detail,
            required_integration=_SEARCH_INTEGRATION,
        ),
        CapabilityStatus(
            key="website_crawling_on_request",
            label="Website crawling on request",
            available=True,
            detail=(
                "Explicitly requested URL extraction runs through the existing crawl "
                "and extraction pipeline with bounded limits and SSRF validation. "
                "Execution is synchronous within the request."
            ),
            implementation="POST /api/v1/crawl/extract",
        ),
        CapabilityStatus(
            key="asynchronous_crawl_jobs",
            label="Asynchronous crawl jobs",
            available=False,
            detail=(
                "There is no persistent queue for website crawling; website extraction "
                "runs synchronously within the request. The persistent job queue covers "
                "hospital-source verification (a separate worker process), not general "
                "crawl jobs."
            ),
            required_integration=_JOB_STORE_INTEGRATION,
        ),
        CapabilityStatus(
            key="evidence_provenance",
            label="Evidence provenance and citations",
            available=True,
            detail=(
                "Each finding carries its source, source name, source URL, retrieval "
                "and publication dates, and an availability status."
            ),
            implementation="comparison.schemas.EvidenceItem",
        ),
    ]
    return CapabilityReport(capabilities=capabilities)
