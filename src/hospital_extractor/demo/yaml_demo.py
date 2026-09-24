"""YAML fixture demo: synthetic snapshots validated through existing services.

Loads five deterministic fixtures from ``tests/fixtures/medcompare_demo``:

- ``baseline.yaml`` / ``current.yaml`` — two snapshots of the same fictional
  hospitals (tri-state ``YES`` / ``NO`` / ``UNKNOWN`` fields),
- ``provenance.yaml`` — where field values came from,
- ``conflict.yaml`` — two disagreeing values for one field,
- ``expected.yaml`` — outcomes the validator must *match*, never compute with.

Bridge (non-invasive, reuses the real pipeline)::

    YAML hospital record
      -> HospitalDataset (canonical models)
      -> hospital_extractor.snapshots.change_detection.compute_changes
      -> hospital-level ADDED / REMOVED / MODIFIED / NO_CHANGE

Conflicts reuse :class:`hospital_extractor.models.conflict.Conflict`.
Quality metrics are independent dimensions only — never an overall score
(P23 invariant: no composite ``overall_score``).

Fully offline: no network, no clocks in expected values, no randomness.
Demo/TEST fixture only — fictional ``*.example.test`` domains, no credentials.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from hospital_extractor.models.accreditation import Accreditation
from hospital_extractor.models.conflict import Conflict, ConflictSource
from hospital_extractor.models.contact import Contact
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.facility import Facility
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.speciality import Speciality
from hospital_extractor.quality.models import no_overall_score_note
from hospital_extractor.snapshots.change_detection import compute_changes

__all__ = [
    "DEFAULT_FIXTURE_DIR",
    "UNKNOWN_TOKEN",
    "DemoValidationResult",
    "load_yaml_demo",
    "run_yaml_demo",
]

DEFAULT_FIXTURE_DIR = Path("tests/fixtures/medcompare_demo")
UNKNOWN_TOKEN = "UNKNOWN"
NO_TOKEN = "NO"

# Tri-state capability flags carried as Facility.availability in the bridge.
_CAPABILITY_FIELDS: tuple[str, ...] = (
    "emergency",
    "cardiology",
    "orthopaedics",
    "cath_lab",
)
# Accreditation-style flags carried as Accreditation.status in the bridge.
_ACCREDITATION_FIELDS: tuple[str, ...] = ("nabh", "jci")


def _labelize(key: str) -> str:
    return key.replace("_", " ").title()


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"missing YAML fixture: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return raw


def _hospitals(doc: dict[str, Any], path: Path) -> dict[str, dict[str, Any]]:
    block = doc.get("hospitals")
    if not isinstance(block, dict) or not block:
        raise ValueError(f"expected non-empty 'hospitals' mapping in {path}")
    out: dict[str, dict[str, Any]] = {}
    for key, value in block.items():
        if not isinstance(value, dict):
            raise ValueError(f"hospital {key!r} must be a mapping in {path}")
        out[str(key)] = value
    return out


@dataclass(frozen=True)
class FixtureBundle:
    """All five YAML fixtures, loaded once."""

    baseline: dict[str, dict[str, Any]]
    current: dict[str, dict[str, Any]]
    expected: dict[str, Any]
    provenance: list[dict[str, Any]]
    conflicts: list[dict[str, Any]]
    fixture_dir: Path


def load_yaml_demo(fixture_dir: Path | None = None) -> FixtureBundle:
    """Read the demo fixture set from *fixture_dir* (offline, read-only)."""
    root = fixture_dir if fixture_dir is not None else DEFAULT_FIXTURE_DIR
    baseline_doc = _load_yaml(root / "baseline.yaml")
    current_doc = _load_yaml(root / "current.yaml")
    expected_doc = _load_yaml(root / "expected.yaml")
    provenance_doc = _load_yaml(root / "provenance.yaml")
    conflict_doc = _load_yaml(root / "conflict.yaml")

    provenance_raw = provenance_doc.get("records", [])
    conflicts_raw = conflict_doc.get("conflicts", [])
    if not isinstance(provenance_raw, list):
        raise ValueError("provenance.yaml 'records' must be a list")
    if not isinstance(conflicts_raw, list):
        raise ValueError("conflict.yaml 'conflicts' must be a list")

    return FixtureBundle(
        baseline=_hospitals(baseline_doc, root / "baseline.yaml"),
        current=_hospitals(current_doc, root / "current.yaml"),
        expected=expected_doc,
        provenance=[r for r in provenance_raw if isinstance(r, dict)],
        conflicts=[c for c in conflicts_raw if isinstance(c, dict)],
        fixture_dir=root,
    )


# ---------------------------------------------------------------------------
# YAML -> canonical HospitalDataset (adapter only; no second change detector)
# ---------------------------------------------------------------------------


def hospital_to_dataset(record: dict[str, Any]) -> HospitalDataset:
    """Map one flat YAML hospital record onto canonical MedCompare models.

    Mapping (documented, one-way, demo-only):

    - ``hospital_name`` / ``beds`` -> :class:`HospitalOverview`
    - ``city`` / ``phone`` -> :class:`Contact`
    - capability flags -> :class:`Facility` with ``availability`` tri-state
    - ``nabh`` / ``jci`` -> :class:`Accreditation` with ``status`` tri-state
    - ``specialities`` / ``facilities`` lists -> canonical collections
    """
    source_url = str(record.get("source_url") or "https://example.test/")
    beds = record.get("beds")
    bed_count = beds if isinstance(beds, int) and not isinstance(beds, bool) else None

    hospital = HospitalOverview(
        name=str(record["hospital_name"]) if record.get("hospital_name") else None,
        bed_count=bed_count,
        source_url=source_url,
        extraction_method="yaml_demo_fixture",
        confidence=1.0,
    )

    contacts: list[Contact] = []
    city = record.get("city")
    phone = record.get("phone")
    if city or phone:
        contacts.append(
            Contact(
                city=str(city) if city else None,
                phone=str(phone) if phone else None,
                source_url=source_url,
                extraction_method="yaml_demo_fixture",
                confidence=1.0,
            )
        )

    facilities: list[Facility] = []
    for key in _CAPABILITY_FIELDS:
        if key in record:
            facilities.append(
                Facility(
                    name=_labelize(key),
                    category="capability",
                    availability=str(record[key]),
                    source_url=source_url,
                    extraction_method="yaml_demo_fixture",
                    confidence=1.0,
                )
            )
    for name in record.get("facilities") or []:
        facilities.append(
            Facility(
                name=str(name),
                category="listed",
                availability="YES",
                source_url=source_url,
                extraction_method="yaml_demo_fixture",
                confidence=1.0,
            )
        )

    specialities = [
        Speciality(
            name=str(name),
            source_url=source_url,
            extraction_method="yaml_demo_fixture",
            confidence=1.0,
        )
        for name in (record.get("specialities") or [])
    ]

    accreditations: list[Accreditation] = []
    for key in _ACCREDITATION_FIELDS:
        if key in record:
            accreditations.append(
                Accreditation(
                    name=_labelize(key),
                    organization=_labelize(key),
                    status=str(record[key]),
                    source_url=source_url,
                    extraction_method="yaml_demo_fixture",
                    confidence=1.0,
                )
            )

    return HospitalDataset(
        hospital=hospital,
        contacts=contacts,
        facilities=facilities,
        specialities=specialities,
        accreditations=accreditations,
    )


# ---------------------------------------------------------------------------
# Computed metrics + validation result
# ---------------------------------------------------------------------------


def _iter_scalar_values(record: dict[str, Any]) -> list[tuple[str, Any]]:
    """``(field, value)`` pairs for non-list fields of one hospital record."""
    return [(k, v) for k, v in record.items() if not isinstance(v, list)]


def count_unknown(hospitals: dict[str, dict[str, Any]]) -> int:
    """Scalar values equal to the UNKNOWN token across *hospitals*."""
    total = 0
    for record in hospitals.values():
        for _field_name, value in _iter_scalar_values(record):
            if value == UNKNOWN_TOKEN:
                total += 1
    return total


def unknown_field_keys(hospitals: dict[str, dict[str, Any]]) -> set[tuple[str, str]]:
    """``(hospital_id, field)`` pairs whose value is UNKNOWN."""
    keys: set[tuple[str, str]] = set()
    for hospital_id, record in hospitals.items():
        for field_name, value in _iter_scalar_values(record):
            if value == UNKNOWN_TOKEN:
                keys.add((hospital_id, field_name))
    return keys


def classify_hospitals(
    baseline: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Compute ADDED / REMOVED / MODIFIED / NO_CHANGE hospital membership.

    Set differences come from ID membership. MODIFIED vs NO_CHANGE uses the
    existing :func:`compute_changes` service over bridged canonical datasets —
    this module does not reimplement change detection.
    """
    baseline_ids = set(baseline)
    current_ids = set(current)
    added = sorted(current_ids - baseline_ids)
    removed = sorted(baseline_ids - current_ids)

    modified: list[str] = []
    unchanged: list[str] = []
    for hospital_id in sorted(baseline_ids & current_ids):
        changes = compute_changes(
            hospital_to_dataset(baseline[hospital_id]),
            hospital_to_dataset(current[hospital_id]),
        )
        if changes:
            modified.append(hospital_id)
        else:
            unchanged.append(hospital_id)

    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "unchanged": unchanged,
    }


def build_conflicts(raw_conflicts: list[dict[str, Any]]) -> list[Conflict]:
    """Turn fixture conflict entries into existing :class:`Conflict` models."""
    built: list[Conflict] = []
    for entry in raw_conflicts:
        raw_values = entry.get("values") or []
        sources: list[ConflictSource] = []
        values: list[str] = []
        for item in raw_values:
            if not isinstance(item, dict):
                continue
            values.append(str(item.get("value", "")))
            sources.append(
                ConflictSource(
                    url=str(item.get("source_url", "")),
                    extraction_method=str(item.get("extraction_method") or "") or None,
                )
            )
        if len(set(values)) < 2:
            continue
        built.append(
            Conflict(
                category=str(entry.get("category", "contacts")),
                field=str(entry.get("field", "")),
                primary=values[0],
                alternative=values[1],
                values=values,
                sources=sources,
                source_urls=[s.url for s in sources],
                resolution_status="unresolved",
                status="conflicting",
            )
        )
    return built


def _quality_metrics(
    current: dict[str, dict[str, Any]],
    provenance: list[dict[str, Any]],
    conflicts: list[Conflict],
) -> dict[str, Any]:
    """Independent quality dimensions only — never a composite score (P23)."""
    scalar_pairs = [
        (hospital_id, field_name, value)
        for hospital_id, record in current.items()
        for field_name, value in _iter_scalar_values(record)
    ]
    field_keys = sorted({field_name for _h, field_name, _v in scalar_pairs})
    slots = len(current) * len(field_keys)
    populated = sum(
        1
        for _h, _fname, value in scalar_pairs
        if value is not None and value != "" and value != UNKNOWN_TOKEN
    )
    unknowns = sum(1 for _h, _f, value in scalar_pairs if value == UNKNOWN_TOKEN)

    prov_keys = {
        (str(row.get("hospital_id", "")), str(row.get("field", ""))) for row in provenance
    }
    unknown_keys = unknown_field_keys(current)
    covered_unknown = len(unknown_keys & prov_keys)

    conflict_pairs = {c.field for c in conflicts}
    metrics: dict[str, Any] = {
        "field_coverage": round(populated / slots, 6) if slots else 0.0,
        "field_coverage_populated": populated,
        "field_coverage_slots": slots,
        "unknown_count": unknowns,
        "unknown_rate": round(unknowns / len(scalar_pairs), 6) if scalar_pairs else 0.0,
        "provenance_records": len(provenance),
        "provenance_distinct_pairs": len(prov_keys),
        "provenance_unknown_coverage": (
            round(covered_unknown / len(unknown_keys), 6) if unknown_keys else 1.0
        ),
        "conflict_present": bool(conflicts),
        "conflict_fields": sorted(conflict_pairs),
        "duplicate_values_detected": len(conflicts),
        "note": no_overall_score_note,
    }
    # P23 invariant: no composite overall score is ever computed here.
    assert "overall_score" not in metrics
    return metrics


@dataclass
class DemoValidationResult:
    """Computed outcomes + comparison against expected.yaml."""

    fixture_dir: Path
    baseline_count: int
    current_count: int
    added: list[str]
    modified: list[str]
    unchanged: list[str]
    removed: list[str]
    unknown_count: int
    baseline_unknown_count: int
    provenance_count: int
    conflict_descriptions: list[str]
    conflict_detected: bool
    quality: dict[str, Any]
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def result_label(self) -> str:
        return "PASS" if self.passed else "FAIL"

    def render(self) -> str:
        """Human-readable report; every number comes from the computation."""
        removed = ", ".join(self.removed) if self.removed else "none"
        conflicts = (
            "\n".join(f"  {desc}" for desc in self.conflict_descriptions)
            if self.conflict_descriptions
            else "  none"
        )
        failure_block = ""
        if self.failures:
            failure_block = "\n\nFailures:\n" + "\n".join(f"  - {f}" for f in self.failures)
        return (
            "MedCompare Demo Validation\n"
            "==========================\n"
            f"Baseline hospitals : {self.baseline_count}\n"
            f"Current hospitals  : {self.current_count}\n"
            "\n"
            "Added:\n"
            + ("\n".join(f"  {h}" for h in self.added) or "  none")
            + "\n\nModified:\n"
            + ("\n".join(f"  {h}" for h in self.modified) or "  none")
            + "\n\nUnchanged:\n"
            + ("\n".join(f"  {h}" for h in self.unchanged) or "  none")
            + "\n\nRemoved:\n"
            f"  {removed}\n"
            "\n"
            f"UNKNOWN values (current): {self.unknown_count}\n"
            f"UNKNOWN values (baseline): {self.baseline_unknown_count}\n"
            f"Provenance records: {self.provenance_count}\n"
            "\n"
            f"Conflict:\n{conflicts}\n"
            "\n"
            "Quality (independent dimensions):\n"
            f"  field_coverage        = {self.quality['field_coverage']}\n"
            f"  unknown_rate          = {self.quality['unknown_rate']}\n"
            f"  provenance_coverage   = {self.quality['provenance_unknown_coverage']}\n"
            f"  conflict_present      = {self.quality['conflict_present']}\n"
            f"  overall_score         = null (never computed)\n"
            "\n"
            f"Result:\n  {self.result_label}{failure_block}\n"
        )


def _as_sorted_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"expected a list in expected.yaml, got {type(value).__name__}")
    return sorted(str(v) for v in value)


def run_yaml_demo(fixture_dir: Path | None = None) -> DemoValidationResult:
    """Load fixtures, compute every outcome, compare to expected.yaml.

    The comparison is one-way: expected values never influence the computed
    added/modified/unchanged/removed sets.
    """
    bundle = load_yaml_demo(fixture_dir)
    classification = classify_hospitals(bundle.baseline, bundle.current)
    conflicts = build_conflicts(bundle.conflicts)

    unknown_count = count_unknown(bundle.current)
    baseline_unknown = count_unknown(bundle.baseline)
    provenance_count = len(bundle.provenance)
    quality = _quality_metrics(bundle.current, bundle.provenance, conflicts)

    expected = bundle.expected
    failures: list[str] = []

    checks: list[tuple[str, list[str], list[str]]] = [
        ("added", classification["added"], _as_sorted_str_list(expected.get("added"))),
        ("modified", classification["modified"], _as_sorted_str_list(expected.get("modified"))),
        (
            "unchanged",
            classification["unchanged"],
            _as_sorted_str_list(expected.get("unchanged")),
        ),
        ("removed", classification["removed"], _as_sorted_str_list(expected.get("removed"))),
    ]
    for name, actual, want in checks:
        if actual != want:
            failures.append(f"{name}: computed {actual} != expected {want}")

    expected_unknown = expected.get("expected_unknown_count")
    if not isinstance(expected_unknown, int):
        failures.append("expected_unknown_count missing or not an int")
    elif unknown_count != expected_unknown:
        failures.append(
            f"unknown_count: computed {unknown_count} != expected {expected_unknown}"
        )

    min_prov = expected.get("expected_minimum_provenance_records")
    if not isinstance(min_prov, int):
        failures.append("expected_minimum_provenance_records missing or not an int")
    elif provenance_count < min_prov:
        failures.append(
            f"provenance_count: computed {provenance_count} < expected minimum {min_prov}"
        )

    expected_conflict = bool(expected.get("expected_conflict"))
    if bool(conflicts) is not expected_conflict:
        failures.append(
            f"conflict: computed {bool(conflicts)} != expected {expected_conflict}"
        )

    # UNKNOWN must remain a distinct semantic token from NO.
    if UNKNOWN_TOKEN == NO_TOKEN:
        failures.append("UNKNOWN token collides with NO")

    conflict_descriptions = [
        f"{_conflict_hospital(bundle.conflicts, c)}.{c.field}"
        f" -> CONFLICT ({' / '.join(c.values)})"
        for c in conflicts
    ]

    return DemoValidationResult(
        fixture_dir=bundle.fixture_dir,
        baseline_count=len(bundle.baseline),
        current_count=len(bundle.current),
        added=classification["added"],
        modified=classification["modified"],
        unchanged=classification["unchanged"],
        removed=classification["removed"],
        unknown_count=unknown_count,
        baseline_unknown_count=baseline_unknown,
        provenance_count=provenance_count,
        conflict_descriptions=conflict_descriptions,
        conflict_detected=bool(conflicts),
        quality=quality,
        failures=failures,
    )


def _conflict_hospital(raw: list[dict[str, Any]], conflict: Conflict) -> str:
    """Hospital id for a built Conflict (looked up from the raw fixture)."""
    for entry in raw:
        if str(entry.get("field", "")) == conflict.field:
            return str(entry.get("hospital_id", "?"))
    return "?"
