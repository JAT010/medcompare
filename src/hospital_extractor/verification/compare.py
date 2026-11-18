"""Pure comparison of a hospital snapshot against website crawl evidence.

Conservative by construction: absence of evidence is never a mismatch. Only
fields with an explicit, comparable website criterion are evaluated;
everything else is reported as ``insufficient_evidence`` (not evaluated).

Outcomes per check: ``match`` | ``mismatch`` | ``insufficient_evidence``.
Aggregation: any mismatch wins; ``match`` only when every triggering change
was evaluated and every evaluated check matched; otherwise
``insufficient_evidence`` — absence of evidence is never a verified match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "ChangeVerification",
    "VerificationAggregate",
    "build_verification",
    "extract_ints",
    "normalize_url_identity",
    "normalize_tokens",
]

_MAX_VALUE_LEN = 2000


@dataclass(frozen=True)
class ChangeVerification:
    path: str
    change_type: str
    entity_name: str
    field: str
    status: str  # match | mismatch | insufficient_evidence
    hospital_value: Any
    website_value: Any
    detail: str


@dataclass(frozen=True)
class VerificationAggregate:
    status: str  # match | mismatch | insufficient_evidence
    checks: tuple[ChangeVerification, ...]
    evaluated: int
    matched: int
    mismatched: int
    insufficient: int

    def evidence_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "evaluated": self.evaluated,
            "matched": self.matched,
            "mismatched": self.mismatched,
            "insufficient": self.insufficient,
            "checks": [
                {
                    "path": c.path,
                    "change_type": c.change_type,
                    "entity_name": c.entity_name,
                    "field": c.field,
                    "status": c.status,
                    "hospital_value": _jsonish(c.hospital_value),
                    "website_value": _jsonish(c.website_value),
                    "detail": c.detail,
                }
                for c in self.checks
            ],
        }


def _jsonish(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_VALUE_LEN else value[:_MAX_VALUE_LEN]
    return value


def normalize_tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())


def extract_ints(value: str) -> list[int]:
    found: list[int] = []
    for raw in re.findall(r"\d[\d,]*", value):
        try:
            found.append(int(raw.replace(",", "")))
        except ValueError:  # pragma: no cover - defensive
            continue
    return found


def normalize_url_identity(value: str) -> tuple[str, str] | None:
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    path = parts.path.rstrip("/") or "/"
    return host, path


def _bed_counts_from_values(values: list[str]) -> list[int]:
    """Bed counts per infrastructure value; items without a count are skipped."""
    found: list[int] = []
    for value in values:
        match = re.search(r"Total beds:\s*([\d,]+)", value, flags=re.IGNORECASE)
        if match:
            try:
                found.append(int(match.group(1).replace(",", "")))
            except ValueError:
                continue
        else:
            ints = extract_ints(value)
            if ints:
                found.append(ints[0])
    return found


def _location_texts(evidence: list[Any]) -> list[str]:
    return [
        str(item.value) for item in evidence if getattr(item, "criterion_key", None) == "location"
    ]


def _name_match(internal: str, website: str) -> bool | None:
    left = set(normalize_tokens(internal))
    right = set(normalize_tokens(website))
    if not left or not right:
        return None
    generic = {
        "hospital",
        "medical",
        "centre",
        "center",
        "clinic",
        "general",
        "super",
        "speciality",
        "specialty",
    }
    left -= generic
    right -= generic
    if not left or not right:
        left, right = set(normalize_tokens(internal)), set(normalize_tokens(website))
    return left <= right or right <= left


def _website_in(values: list[str], needle: str) -> str | None:
    for value in values:
        if needle.casefold() in value.casefold():
            return value
    return None


def _postal_code(value: str) -> str | None:
    match = re.search(r"\b(\d{5,6})\b", value)
    return match.group(1) if match else None


def build_verification(
    *,
    payload: dict[str, Any] | None,
    changes: list[tuple[str, str, str, str, Any, Any]],  # (path, type, entity, field, old, new)
    website_evidence: list[Any],
    website_hospital_name: str | None,
    website_target_url: str | None,
    hospital_url: str | None,
) -> VerificationAggregate:
    """Return per-change verdicts and the aggregate status.

    *changes* are tuples straight out of the detected-change rows:
    ``(path, change_type, entity_name, field, old_value, new_value)``.
    *website_evidence* is a list of EvidenceItem-like objects (criterion_key,
    value, source_url). *payload* is the hospital snapshot's payload (unused
    in this version beyond defensive fallbacks).
    """
    del payload
    checks: list[ChangeVerification] = []
    locations = _location_texts(website_evidence)
    infra_values = [
        str(item.value)
        for item in website_evidence
        if getattr(item, "criterion_key", None) == "infrastructure"
    ]

    for path, change_type, entity_name, field, old_value, new_value in changes:
        effective_value = new_value if change_type in {"added", "modified"} else old_value
        website_value: Any = None
        status = "insufficient_evidence"
        detail = "No comparable website evidence for this field."

        if path in {"hospital.name", "contact.name"} and effective_value:
            website_value = website_hospital_name
            if effective_value and website_value:
                match = _name_match(str(effective_value), str(website_value))
                if match is True:
                    status, detail = "match", "Facility name matches the website heading."
                elif match is False:
                    status, detail = "mismatch", "Facility name differs from the website."
                else:
                    status, detail = (
                        "insufficient_evidence",
                        "Name could not be compared deterministically.",
                    )
        elif path == "hospital.bed_count" or path == "infrastructure.total_beds":
            bed_counts = _bed_counts_from_values(infra_values)
            distinct = set(bed_counts)
            if len(distinct) > 1:
                website_value, status = sorted(distinct), "insufficient_evidence"
                detail = "Website infrastructure evidence gives conflicting bed counts."
            elif distinct:
                website_value = distinct.pop()
                if effective_value is not None:
                    try:
                        same = int(effective_value) == int(website_value)
                    except (TypeError, ValueError):
                        same = None
                    if same is True:
                        status, detail = "match", "Bed count matches the website."
                    elif same is False:
                        status, detail = "mismatch", "Bed count differs from the website."
                    else:
                        status, detail = (
                            "insufficient_evidence",
                            "Bed count could not be compared.",
                        )
            elif effective_value is not None:
                detail = "Website infrastructure evidence does not state a bed count."

        elif path in {"hospital.website", "contact.website"}:
            claimed_host = None
            if isinstance(effective_value, str) and effective_value:
                claimed_host = normalize_url_identity(effective_value)
            website_id = normalize_url_identity(website_target_url or "")
            website_value = website_target_url
            if claimed_host and website_id:
                status = "match" if claimed_host == website_id else "mismatch"
                detail = (
                    "Website identity matches the claimed URL."
                    if status == "match"
                    else "Website identity differs from the claimed URL."
                )
            elif hospital_url and effective_value:
                detail = "Hospital URL could not be compared."
        elif path.startswith("contact.") or path.startswith("hospital."):
            # Identity/location fields only; other scalar fields have no
            # reliable website criterion.
            if field in {"city", "address", "pin_code", "postal_code", "location"}:
                claimed = str(effective_value or "")
                post = _postal_code(claimed)
                website_value = None
                location_posts = {
                    post for post in (_postal_code(text) for text in locations) if post
                }
                conflicting_location = len(location_posts) > 1
                if conflicting_location:
                    website_value = " | ".join(locations)
                    detail = "Website location evidence gives conflicting postal codes."
                elif post and locations:
                    for text in locations:
                        if post in text:
                            website_value, status = text, "match"
                            detail = "Postal code matches the website location."
                            break
                    if website_value is None and post not in " ".join(locations):
                        status, website_value = "mismatch", " ".join(locations)
                        detail = "Postal code not found in the website location."
                elif claimed:
                    hit = _website_in(locations, claimed) if locations else None
                    if hit is not None:
                        status, website_value, detail = "match", hit, "Location matches."
                    elif locations:
                        status, website_value = "insufficient_evidence", " ".join(locations)
                        detail = "City/address not stated on the website; cannot confirm."
                    else:
                        detail = "Website location evidence missing."
            elif field == "phone" or field == "email":
                detail = "Website contact evidence is not mapped to a criterion yet."
            elif field in {"doctor_count", "staff_count", "established_year"}:
                detail = "This field is not stated by the website in a machine-comparable form."
        # else: collections/departments/procedures/accreditations etc.

        checks.append(
            ChangeVerification(
                path=path,
                change_type=change_type,
                entity_name=entity_name,
                field=field,
                status=status,
                hospital_value=effective_value,
                website_value=website_value,
                detail=detail,
            )
        )

    evaluated = sum(1 for c in checks if c.status in {"match", "mismatch"})
    matched = sum(1 for c in checks if c.status == "match")
    mismatched = sum(1 for c in checks if c.status == "mismatch")
    insufficient = sum(1 for c in checks if c.status == "insufficient_evidence")
    if mismatched:
        aggregate_status = "mismatch"
    elif checks and evaluated == len(checks) and matched == evaluated:
        # Every triggering change was evaluated and every evaluated check
        # matched. Any change without website evidence must prevent a
        # "verified" verdict.
        aggregate_status = "match"
    else:
        aggregate_status = "insufficient_evidence"
    return VerificationAggregate(
        status=aggregate_status,
        checks=tuple(checks),
        evaluated=evaluated,
        matched=matched,
        mismatched=mismatched,
        insufficient=insufficient,
    )
