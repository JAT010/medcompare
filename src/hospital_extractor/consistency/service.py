"""Deterministic database-to-website consistency auditing.

The service is pure: it receives one stored facility record and one persisted
website extraction (already resolved and ownership-checked by the caller) and
reports how the two views agree. It never fetches, never writes, never chooses a
"correct" value and never converts missing data into a negative claim.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from urllib.parse import urlsplit

from hospital_extractor.comparison.schemas import EvidenceItem
from hospital_extractor.consistency.schemas import (
    AUDIT_DISCLAIMER,
    BRANCH_IDENTITY_NOTE,
    INTERNAL_SOURCE_NOTE,
    AuditFinding,
    AuditSourceValue,
    AuditStatus,
    AuditSummary,
    ConsistencyAuditResponse,
    InternalRecordSummary,
    WebsiteSnapshotSummary,
)
from hospital_extractor.persistence.exceptions import InvalidFacilityUrlError
from hospital_extractor.persistence.models import HospitalFacility
from hospital_extractor.persistence.models_crawl import HospitalCrawlRun
from hospital_extractor.persistence.url_normalization import normalize_canonical_url
from hospital_extractor.recommendation.criteria import CRITERIA_BY_KEY

__all__ = ["build_audit"]

INTERNAL_SOURCE_NAME = "MedCompare registry (stored facility record)"
WEBSITE_SOURCE_NAME = "Hospital website (persisted crawl)"

_IDENTITY_WEBSITE_KEYS = frozenset({"location"})
_MISSING_WEBSITE_NOTE = (
    "Not found in the crawled pages of the persisted website extraction. This is not "
    "evidence that the website omits the field."
)
_MISSING_INTERNAL_NOTE = (
    "The MedCompare registry does not store this field, so it cannot be verified from "
    "the internal record. An authorized hospital-system integration would be required "
    "to compare it with the hospital's own database."
)
_NO_SNAPSHOT_NOTE = "No usable persisted website extraction is available for this facility."
_INTEGRATION_WARNING = (
    "Website-extracted service fields are not stored in the MedCompare registry. "
    "Verifying them against the hospital's internal system requires an authorized "
    "hospital-server integration; until then they are reported as missing from the "
    "internal record, not as database errors."
)


def build_audit(
    facility: HospitalFacility,
    run: HospitalCrawlRun | None,
    evidence: Sequence[EvidenceItem] = (),
    *,
    warnings: Sequence[str] = (),
) -> ConsistencyAuditResponse:
    """Compare the registry record for *facility* with its website extraction."""
    audit_warnings = list(dict.fromkeys(warnings))
    findings: list[AuditFinding] = []

    internal_updated_at = _as_aware(facility.updated_at)
    website_retrieved_at = _run_timestamp(run)
    snapshot_present = run is not None

    findings.append(
        _identity_finding(
            field_key="facility_name",
            label="Facility name",
            internal_value=facility.name,
            website_value=run.hospital_name if run is not None else None,
            facility=facility,
            run=run,
            match=_name_match,
            snapshot_present=snapshot_present,
            internal_updated_at=internal_updated_at,
            website_retrieved_at=website_retrieved_at,
        )
    )

    website_url = None
    if run is not None:
        website_url = run.canonical_url or run.target_url
    findings.append(
        _identity_finding(
            field_key="website_url",
            label="Website URL",
            internal_value=facility.canonical_url,
            website_value=website_url,
            facility=facility,
            run=run,
            match=_url_match,
            snapshot_present=snapshot_present,
            internal_updated_at=internal_updated_at,
            website_retrieved_at=website_retrieved_at,
        )
    )

    website_location = _website_evidence(evidence, "location")
    findings.append(
        _identity_finding(
            field_key="location",
            label="Address / locality",
            internal_value=_internal_location(facility),
            website_value=website_location.value if website_location else None,
            facility=facility,
            run=run,
            match=lambda internal, website: _location_match(internal, website, facility),
            snapshot_present=snapshot_present,
            internal_updated_at=internal_updated_at,
            website_retrieved_at=website_retrieved_at,
            website_confidence=website_location.confidence if website_location else None,
            website_source_url=website_location.source_url if website_location else None,
        )
    )

    for criterion_key, website_value in _website_only_evidence(evidence).items():
        findings.append(_website_only_finding(criterion_key, website_value, facility, run))

    if not snapshot_present:
        audit_warnings.append(_NO_SNAPSHOT_NOTE)
    if any(finding.requires_authorized_integration for finding in findings):
        audit_warnings.append(_INTEGRATION_WARNING)

    return ConsistencyAuditResponse(
        facility_id=facility.id,
        facility_name=facility.name,
        organization_name=(
            facility.organization.name if facility.organization is not None else None
        ),
        branch_identity_note=BRANCH_IDENTITY_NOTE,
        internal_record=_internal_summary(facility),
        website_snapshot=_website_summary(run, evidence) if run is not None else None,
        findings=findings,
        summary=_summary(findings),
        warnings=list(dict.fromkeys(audit_warnings)),
        disclaimer=AUDIT_DISCLAIMER,
    )


def _identity_finding(
    *,
    field_key: str,
    label: str,
    internal_value: str | None,
    website_value: str | None,
    facility: HospitalFacility,
    run: HospitalCrawlRun | None,
    match: Callable[[str, str], bool | None],
    snapshot_present: bool,
    internal_updated_at: datetime | None,
    website_retrieved_at: datetime | None,
    website_confidence: float | None = None,
    website_source_url: str | None = None,
) -> AuditFinding:
    internal_text = _clean(internal_value)
    website_text = _clean(website_value)
    internal_source = (
        _internal_provenance(internal_text, facility) if internal_text is not None else None
    )
    website_source = (
        _website_provenance(
            website_text,
            run=run,
            confidence=website_confidence,
            source_url=website_source_url,
        )
        if website_text is not None
        else None
    )

    comparison: bool | None = None
    if internal_text is not None and website_text is not None:
        comparison = match(internal_text, website_text)

    status = _status(
        internal_source,
        website_source,
        comparison,
        snapshot_present=snapshot_present,
        internal_updated_at=internal_updated_at,
        website_retrieved_at=website_retrieved_at,
    )
    return AuditFinding(
        field_key=field_key,
        label=label,
        status=status,
        internal=internal_source,
        website=website_source,
        explanation=_explanation(field_key, label, status, comparison),
        requires_authorized_integration=False,
    )


def _website_only_finding(
    criterion_key: str,
    item: EvidenceItem,
    facility: HospitalFacility,
    run: HospitalCrawlRun | None,
) -> AuditFinding:
    label = _label_for(criterion_key)
    return AuditFinding(
        field_key=criterion_key,
        label=label,
        status="missing_from_internal_record",
        internal=None,
        website=_website_provenance(
            _clean(item.value) or item.value,
            run=run,
            confidence=item.confidence,
            source_url=item.source_url,
            note=item.note,
        ),
        explanation=(
            f"The website extraction reports {label.lower()}, but the MedCompare registry "
            f"does not store this field for {facility.name}. "
            f"{_MISSING_INTERNAL_NOTE}"
        ),
        requires_authorized_integration=True,
    )


def _status(
    internal: AuditSourceValue | None,
    website: AuditSourceValue | None,
    comparison: bool | None,
    *,
    snapshot_present: bool,
    internal_updated_at: datetime | None,
    website_retrieved_at: datetime | None,
) -> AuditStatus:
    if internal is not None and website is not None:
        if comparison is True:
            return "consistent"
        if comparison is None:
            return "unverifiable"
        if (
            internal_updated_at is not None
            and website_retrieved_at is not None
            and website_retrieved_at < internal_updated_at
        ):
            return "potentially_outdated"
        return "differs"
    if internal is not None:
        return "missing_from_website" if snapshot_present else "unverifiable"
    if website is not None:
        return "missing_from_internal_record"
    return "unverifiable"


def _explanation(
    field_key: str,
    label: str,
    status: AuditStatus,
    comparison: bool | None,
) -> str:
    if status == "consistent":
        return f"Both sources agree on {label.lower()} for this physical facility."
    if status == "differs":
        return (
            f"The internal record and the website extraction state different values for "
            f"{label.lower()}. Manual review is required; neither value is treated as "
            "automatically correct."
        )
    if status == "potentially_outdated":
        return (
            f"The website extraction predates the last update of the internal record and "
            f"states a different {label.lower()}. The website may be outdated; manual "
            "review is required."
        )
    if status == "missing_from_website":
        return (
            f"The internal record has a {label.lower()} value, but the website extraction "
            f"did not find one. {_MISSING_WEBSITE_NOTE}"
        )
    if status == "missing_from_internal_record":
        return _MISSING_INTERNAL_NOTE
    if comparison is None and field_key in {"facility_name", "website_url", "location"}:
        return (
            f"Both sources contain a value for {label.lower()}, but they cannot be compared "
            "reliably with deterministic matching. Manual review is required."
        )
    return (
        f"{label} cannot be verified because at least one source does not contain the "
        "required information. Missing information is not treated as a negative finding."
    )


def _internal_provenance(value: str, facility: HospitalFacility) -> AuditSourceValue:
    return AuditSourceValue(
        source="internal_record",
        source_name=INTERNAL_SOURCE_NAME,
        value=value,
        source_url=facility.canonical_url,
        retrieved_at=_as_aware(facility.updated_at),
        confidence=1.0,
        note=f"{INTERNAL_SOURCE_NOTE} Facility id {facility.id}.",
    )


def _website_provenance(
    value: str,
    *,
    run: HospitalCrawlRun | None,
    confidence: float | None,
    source_url: str | None,
    note: str | None = None,
) -> AuditSourceValue:
    return AuditSourceValue(
        source="website_crawl",
        source_name=WEBSITE_SOURCE_NAME,
        value=value,
        source_url=source_url or (run.target_url if run is not None else None),
        retrieved_at=_run_timestamp(run),
        confidence=confidence,
        note=note or "Value extracted from the persisted website crawl run.",
    )


def _website_evidence(evidence: Sequence[EvidenceItem], key: str) -> EvidenceItem | None:
    for item in evidence:
        if item.criterion_key == key:
            return item
    return None


def _website_only_evidence(evidence: Sequence[EvidenceItem]) -> dict[str, EvidenceItem]:
    grouped: dict[str, EvidenceItem] = {}
    for item in evidence:
        if item.criterion_key in _IDENTITY_WEBSITE_KEYS:
            continue
        current = grouped.get(item.criterion_key)
        if current is None:
            grouped[item.criterion_key] = item
            continue
        merged = EvidenceItem(
            criterion_key=item.criterion_key,
            value=" | ".join(dict.fromkeys([current.value, item.value])),
            source=item.source,
            source_name=item.source_name,
            source_url=item.source_url or current.source_url,
            retrieved_at=_max_datetime(current.retrieved_at, item.retrieved_at),
            published_at=item.published_at or current.published_at,
            status="conflicting" if "conflicting" in {current.status, item.status} else item.status,
            confidence=_max_confidence(current.confidence, item.confidence),
            note=item.note or current.note,
        )
        grouped[item.criterion_key] = merged
    return grouped


def _label_for(criterion_key: str) -> str:
    spec = CRITERIA_BY_KEY.get(criterion_key)
    if spec is not None:
        return spec.label
    return criterion_key.replace("_", " ").title()


def _internal_location(facility: HospitalFacility) -> str | None:
    parts = [
        facility.address,
        facility.city,
        facility.state,
        facility.postal_code,
        facility.country,
    ]
    seen: list[str] = []
    for part in parts:
        text = _clean(part)
        if text is None:
            continue
        if any(text.casefold() in existing.casefold() for existing in seen):
            continue
        seen.append(text)
    return ", ".join(seen) or None


def _name_match(left: str, right: str) -> bool | None:
    left_tokens = set(_tokens(left))
    right_tokens = set(_tokens(right))
    if not left_tokens or not right_tokens:
        return None
    return left_tokens <= right_tokens or right_tokens <= left_tokens


def _url_match(left: str, right: str) -> bool | None:
    left_identity = _url_identity(left)
    right_identity = _url_identity(right)
    if left_identity is None or right_identity is None:
        return None
    return left_identity == right_identity


def _location_match(left: str, right: str, facility: HospitalFacility) -> bool | None:
    left_tokens = set(_tokens(left))
    right_tokens = set(_tokens(right))
    if not left_tokens or not right_tokens:
        return None
    if left_tokens <= right_tokens or right_tokens <= left_tokens:
        return True

    left_postal = _postal_code(left)
    right_postal = _postal_code(right)
    if left_postal and right_postal:
        return left_postal == right_postal

    city_tokens = set(_tokens(facility.city or ""))
    if city_tokens:
        return city_tokens <= right_tokens or city_tokens <= left_tokens
    return None


def _postal_code(value: str) -> str | None:
    match = re.search(r"\b(\d{5,6})\b", value)
    return match.group(1) if match else None


def _url_identity(value: str) -> tuple[str, str, str] | None:
    try:
        normalized = normalize_canonical_url(value).normalized
    except InvalidFacilityUrlError:
        return None
    parts = urlsplit(normalized)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host, parts.path.rstrip("/") or "/", parts.query


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _run_timestamp(run: HospitalCrawlRun | None) -> datetime | None:
    if run is None:
        return None
    return _as_aware(run.retrieved_at or run.completed_at)


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _max_datetime(left: datetime | None, right: datetime | None) -> datetime | None:
    left_aware = _as_aware(left)
    right_aware = _as_aware(right)
    if left_aware is None:
        return right_aware
    if right_aware is None:
        return left_aware
    return left_aware if left_aware >= right_aware else right_aware


def _max_confidence(left: float | None, right: float | None) -> float | None:
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def _internal_summary(facility: HospitalFacility) -> InternalRecordSummary:
    organization = facility.organization
    return InternalRecordSummary(
        facility_id=facility.id,
        name=facility.name,
        organization_name=organization.name if organization is not None else None,
        canonical_url=facility.canonical_url,
        address=facility.address,
        city=facility.city,
        state=facility.state,
        postal_code=facility.postal_code,
        country=facility.country,
        updated_at=_as_aware(facility.updated_at),
    )


def _website_summary(
    run: HospitalCrawlRun, evidence: Sequence[EvidenceItem]
) -> WebsiteSnapshotSummary:
    return WebsiteSnapshotSummary(
        crawl_run_id=run.id,
        status=run.status,
        target_url=run.target_url,
        requested_url=run.requested_url,
        hospital_name=run.hospital_name,
        pages_fetched=run.pages_fetched,
        evidence_count=len(evidence),
        usable_for_comparison=bool(run.usable_for_comparison),
        requested_at=_as_aware(run.requested_at),
        completed_at=_as_aware(run.completed_at),
        retrieved_at=_as_aware(run.retrieved_at),
    )


def _summary(findings: Sequence[AuditFinding]) -> AuditSummary:
    counts = Counter(finding.status for finding in findings)
    return AuditSummary(
        findings_total=len(findings),
        consistent=counts.get("consistent", 0),
        differs=counts.get("differs", 0),
        missing_from_website=counts.get("missing_from_website", 0),
        missing_from_internal_record=counts.get("missing_from_internal_record", 0),
        potentially_outdated=counts.get("potentially_outdated", 0),
        unverifiable=counts.get("unverifiable", 0),
    )
