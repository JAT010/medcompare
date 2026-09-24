"""Verification evaluation: compare triggering changes to the target dataset.

A job exists because a ChangeSet identified specific authoritative changes.
Verification NEVER re-checks the whole hospital — only the changes that
triggered the job are evaluated against a FULL_DATASET capture of the target
source (MedCompare does not claim targeted crawling; it captures the target
dataset and checks the relevant dimensions).

Per-change rules (``UNKNOWN`` is never treated as ``NO``):

- ADDED    + entity present on website      -> VERIFIED
- ADDED    + entity absent on website       -> UNVERIFIED (website did not mention it)
- REMOVED  + entity absent on website       -> VERIFIED
- REMOVED  + entity still present           -> MISMATCH
- MODIFIED + website value equals claim     -> VERIFIED
- MODIFIED + website value unknown (None)   -> UNVERIFIED
- MODIFIED + website value differs          -> MISMATCH

Result aggregation: any MISMATCH wins; otherwise every change must be VERIFIED
for a VERIFIED result; anything else is UNVERIFIED.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any, cast

from hospital_extractor.models.base import EntityRecord
from hospital_extractor.models.change import Change, ChangeSet, ChangeType
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.verification import (
    ChangeVerification,
    VerificationOutcome,
    VerificationResultStatus,
)

__all__ = [
    "evaluate_change",
    "evaluate_change_set",
    "find_target_value",
]

_PRIMARY_IDENTITIES = {"primary", ""}
_WHITESPACE = re.compile(r"\s+")


def _identity(record: EntityRecord) -> str:
    for field in ("profile_url", "name"):
        value = getattr(record, field, None)
        if value:
            return _WHITESPACE.sub("", str(value)).strip().lower()
    return "primary"


def _collection_records(dataset: HospitalDataset, category: str) -> list[EntityRecord]:
    """Records for a category as a list (hospital scalar is wrapped)."""
    if category == "hospital":
        hospital = dataset.hospital
        return [hospital] if hospital is not None else []
    items = getattr(dataset, category, [])
    return cast("list[EntityRecord]", list(items) if isinstance(items, list) else [])


def _match_entity(
    dataset: HospitalDataset, category: str, entity_name: str | None
) -> EntityRecord | None:
    """The target-source record corresponding to a change's entity.

    Named entities match on normalized name or profile URL (schema-aware);
    single-record categories (``hospital``, ``contacts``, ``infrastructure``)
    and unnamed entities resolve to their sole record.
    """
    records = _collection_records(dataset, category)
    if entity_name is None or entity_name in _PRIMARY_IDENTITIES:
        return records[0] if len(records) == 1 else (records[0] if records else None)
    needle = _WHITESPACE.sub("", entity_name).strip().lower()
    for record in records:
        if _identity(record) == needle:
            return record
    return None


def _field_value(record: EntityRecord, field: str) -> Any:
    if field in {"name", "category", "description"} and not hasattr(record, field):
        return None
    return getattr(record, field, None)


def _values_equal(left: Any, right: Any) -> bool:
    """Deep equality that is order-insensitive for lists (like fingerprints)."""
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return False
        left_json = {json.dumps(item, sort_keys=True, default=str) for item in left}
        right_json = {json.dumps(item, sort_keys=True, default=str) for item in right}
        return left_json == right_json
    return bool(left == right)


def find_target_value(
    dataset: HospitalDataset,
    change: Change,
) -> tuple[Any, bool]:
    """The website-side value for a change and whether its entity was found.

    Returns ``(value, entity_present)``.  For a MODIFIED change ``value`` is
    the entity's attribute (``None`` if the entity/field is unknown); for an
    ADDED/REMOVED change the "value" is the record itself and only presence
    matters.
    """
    entity = _match_entity(dataset, change.category, change.entity_name)
    if entity is None:
        return None, False
    if change.change_type is ChangeType.MODIFIED:
        return _field_value(entity, change.field), True
    return entity, True


def evaluate_change(change: Change, dataset: HospitalDataset) -> ChangeVerification:
    """Classify one change against the target-source dataset."""
    website_value, entity_present = find_target_value(dataset, change)
    claimed = change.new_value

    if change.change_type is ChangeType.ADDED:
        if entity_present:
            return ChangeVerification(
                path=change.path,
                category=change.category,
                change_type=change.change_type,
                entity_name=change.entity_name,
                claimed_value=claimed,
                website_value=str(getattr(website_value, "name", "") or change.entity_name or ""),
                outcome=VerificationOutcome.VERIFIED,
                detail=f"website lists {change.entity_name or 'the new entity'}",
            )
        return ChangeVerification(
            path=change.path,
            category=change.category,
            change_type=change.change_type,
            entity_name=change.entity_name,
            claimed_value=claimed,
            website_value=None,
            outcome=VerificationOutcome.UNVERIFIED,
            detail="website does not mention it (unknown, never treated as NO)",
        )

    if change.change_type is ChangeType.REMOVED:
        if entity_present:
            return ChangeVerification(
                path=change.path,
                category=change.category,
                change_type=change.change_type,
                entity_name=change.entity_name,
                claimed_value=None,
                website_value=str(getattr(website_value, "name", "") or change.entity_name or ""),
                outcome=VerificationOutcome.MISMATCH,
                detail="website still lists the removed entity",
            )
        return ChangeVerification(
            path=change.path,
            category=change.category,
            change_type=change.change_type,
            entity_name=change.entity_name,
            claimed_value=None,
            website_value=None,
            outcome=VerificationOutcome.VERIFIED,
            detail="website no longer lists it",
        )

    if website_value is None:
        if claimed is None:
            return ChangeVerification(
                path=change.path,
                category=change.category,
                change_type=change.change_type,
                entity_name=change.entity_name,
                claimed_value=claimed,
                website_value=None,
                outcome=VerificationOutcome.VERIFIED,
                detail="both sources are UNKNOWN for this field",
            )
        return ChangeVerification(
            path=change.path,
            category=change.category,
            change_type=change.change_type,
            entity_name=change.entity_name,
            claimed_value=claimed,
            website_value=None,
            outcome=VerificationOutcome.UNVERIFIED,
            detail="website value is UNKNOWN (not treated as NO)",
        )

    if _values_equal(website_value, claimed):
        return ChangeVerification(
            path=change.path,
            category=change.category,
            change_type=change.change_type,
            entity_name=change.entity_name,
            claimed_value=claimed,
            website_value=website_value,
            outcome=VerificationOutcome.VERIFIED,
            detail="website confirms the authoritative value",
        )
    return ChangeVerification(
        path=change.path,
        category=change.category,
        change_type=change.change_type,
        entity_name=change.entity_name,
        claimed_value=claimed,
        website_value=website_value,
        outcome=VerificationOutcome.MISMATCH,
        detail=(
            f"website shows {change.field}={website_value!r}; "
            f"authoritative claims {claimed!r}"
        ),
    )


def evaluate_change_set(
    change_set: ChangeSet,
    dataset: HospitalDataset,
) -> tuple[list[ChangeVerification], VerificationResultStatus, str]:
    """Evaluate every change in *change_set* against the target *dataset*.

    Only the triggering changes are considered — unrelated website differences
    are ignored.  Returns ``(results, aggregated_status, explanation)``.
    """
    results = [evaluate_change(change, dataset) for change in change_set.changes]
    if not results:
        return (
            [],
            VerificationResultStatus.UNVERIFIED,
            "change set contains no changes to verify",
        )

    mismatches = [r for r in results if r.outcome is VerificationOutcome.MISMATCH]
    if mismatches:
        detail = "; ".join(
            f"{r.path} {r.entity_name or ''}".rstrip() for r in mismatches[:5]
        )
        explanation = f"website contradicts {len(mismatches)} triggering change(s): {detail}"
        return results, VerificationResultStatus.MISMATCH, explanation

    if all(r.outcome is VerificationOutcome.VERIFIED for r in results):
        explanation = f"all {len(results)} triggering change(s) confirmed by the website"
        return results, VerificationResultStatus.VERIFIED, explanation

    unverified = [r for r in results if r.outcome is VerificationOutcome.UNVERIFIED]
    detail = "; ".join(f"{r.path} {r.entity_name or ''}".rstrip() for r in unverified[:5])
    explanation = (
        f"website could not confirm {len(unverified)} change(s) "
        f"(unknown, not negated): {detail}"
    )
    return results, VerificationResultStatus.UNVERIFIED, explanation


def evaluation_outcomes(results: Iterable[ChangeVerification]) -> dict[str, int]:
    """Count verification outcomes for result payloads / UI summaries."""
    counts = {
        "verified": 0,
        "mismatch": 0,
        "unverified": 0,
        "not_evaluated": 0,
    }
    for result in results:
        counts[result.outcome.value] = counts.get(result.outcome.value, 0) + 1
    return counts