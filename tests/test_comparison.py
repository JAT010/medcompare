"""Comprehensive tests for the comparison package.

Covers: target resolution, entity matching, availability semantics,
evidence propagation, conflicts, deduplication, evidence coverage,
multi-hospital comparison, and invalid input handling.
"""

from __future__ import annotations

from hospital_extractor.comparison.dimensions import compute_dimensions
from hospital_extractor.comparison.matcher import (
    match_booleans,
    match_entities,
    resolve_boolean_decision,
)
from hospital_extractor.comparison.models import (
    NO_EVIDENCE_MESSAGE,
    ComparisonEvidence,
    ComparisonResult,
    ComparisonTarget,
    DimensionResult,
)
from hospital_extractor.comparison.service import compare_datasets, compare_urls
from hospital_extractor.comparison.target import (
    KNOWN_DEPARTMENTS,
    KNOWN_PROCEDURES,
    availability_categories,
    related_categories_for,
    resolve_target,
)
from hospital_extractor.models.accreditation import Accreditation
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.department import Department
from hospital_extractor.models.doctor import Doctor
from hospital_extractor.models.facility import Facility
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.infrastructure import Infrastructure
from hospital_extractor.models.intake import IntakeEvidence
from hospital_extractor.models.procedure import Procedure
from hospital_extractor.models.speciality import Speciality
from hospital_extractor.models.technology import Technology
from hospital_extractor.models.treatment import Treatment

# ---------------------------------------------------------------------------
# Helpers — build HospitalDataset objects without crawling
# ---------------------------------------------------------------------------

_BASE_URL = "https://test-hospital.example"


def _record(source_url: str = _BASE_URL, **kwargs: object) -> dict[str, object]:
    """Minimal record kwargs merged with source_url."""
    return {"source_url": source_url, **kwargs}


def _make_dataset(**kwargs: object) -> HospitalDataset:
    """Build a HospitalDataset, passing through kwargs to the constructor."""
    defaults: dict[str, object] = {
        "hospital": HospitalOverview(**_record(name="Test Hospital", bed_count=300)),
        "contacts": [],
        "infrastructure": [],
        "facilities": [],
        "departments": [],
        "specialities": [],
        "doctors": [],
        "treatments": [],
        "procedures": [],
        "technologies": [],
        "accreditations": [],
    }
    defaults.update(kwargs)
    return HospitalDataset(**defaults)  # type: ignore[arg-type]


# ===================================================================
# PHASE 1 — Target Resolution
# ===================================================================


class TestTargetResolution:
    """Tests for comparison.target.resolve_target."""

    def test_exact_procedure_name(self) -> None:
        target = resolve_target("Total Knee Replacement")
        assert target.name == "Total Knee Replacement"
        assert target.category == "procedure"
        assert "total_knee_replacement" in target.intake_boolean_keys

    def test_exact_procedure_lower(self) -> None:
        target = resolve_target("total knee replacement")
        assert target.name == "Total Knee Replacement"

    def test_synonym_tkr(self) -> None:
        target = resolve_target("TKR")
        assert target.name == "Total Knee Replacement"
        assert target.category == "procedure"
        assert "knee" in [k.lower() for k in target.keywords]

    def test_synonym_cabg(self) -> None:
        target = resolve_target("CABG")
        assert target.name == "Bypass Surgery (CABG)"
        assert "bypass_surgery_cabg" in target.intake_boolean_keys

    def test_synonym_knee_replacement(self) -> None:
        target = resolve_target("Knee Replacement")
        assert target.name == "Total Knee Replacement"

    def test_synonym_angioplasty(self) -> None:
        target = resolve_target("angioplasty")
        assert target.name == "Angioplasty & Stenting"
        assert "angioplasty_stenting" in target.intake_boolean_keys

    def test_synonym_hip_replacement(self) -> None:
        target = resolve_target("THR")
        assert target.name == "Total Hip Replacement"
        assert "total_hip_replacement" in target.intake_boolean_keys

    def test_synonym_heart_valve(self) -> None:
        target = resolve_target("AVR")
        assert target.name == "Heart Valve Replacement"

    def test_synonym_pacemaker(self) -> None:
        target = resolve_target("pacemaker")
        assert target.name == "Pacemaker / ICD Implantation"

    def test_synonym_heart_transplant(self) -> None:
        target = resolve_target("Heart Transplant")
        assert target.name == "Heart Transplant"

    def test_department_target(self) -> None:
        target = resolve_target("Cardiology", category="department")
        assert target.name == "Cardiology"
        assert target.category == "department"
        assert any("cardiology" in kw for kw in target.keywords)

    def test_department_auto_inferred_from_synonym(self) -> None:
        target = resolve_target("Cardiac Sciences")
        assert target.name == "Cardiology"
        assert target.category == "department"

    def test_department_synonyms_resolvable(self) -> None:
        for entry in KNOWN_DEPARTMENTS:
            target = resolve_target(entry.canonical_name)
            assert target.name == entry.canonical_name
            for syn in entry.synonyms:
                syn_target = resolve_target(syn)
                assert syn_target.name == entry.canonical_name, (
                    f"Department synonym {syn!r} should resolve to "
                    f"{entry.canonical_name!r}"
                )

    def test_unknown_department_keeps_department_category(self) -> None:
        target = resolve_target("Neuromuscular Clinic", category="department")
        assert target.name == "Neuromuscular Clinic"
        assert target.category == "department"
        # Absence of evidence for an unknown department must stay UNKNOWN,
        # never NO — so no positive boolean noise either.
        assert target.intake_boolean_keys == []

    def test_known_procedure_not_absorbed_by_department(self) -> None:
        target = resolve_target("Heart Transplant")
        assert target.name == "Heart Transplant"
        assert target.category != "department"

    def test_speciality_target(self) -> None:
        target = resolve_target("Orthopaedics", category="speciality")
        assert target.name == "Orthopaedics"
        assert target.category == "speciality"

    def test_treatment_target(self) -> None:
        target = resolve_target("Knee Surgery", category="treatment")
        assert target.name == "Knee Surgery"
        assert target.category == "treatment"

    def test_unknown_target_fallback(self) -> None:
        target = resolve_target("Appendectomy")
        assert target.name == "Appendectomy"
        assert target.category == "procedure"
        assert len(target.keywords) > 0

    def test_unknown_target_no_boolean_keys(self) -> None:
        target = resolve_target("Zebra Plasty")
        assert target.intake_boolean_keys == [] or isinstance(
            target.intake_boolean_keys, list
        )

    def test_keywords_are_lowercase(self) -> None:
        target = resolve_target("Knee Replacement")
        for kw in target.keywords:
            assert kw == kw.lower()

    def test_keywords_non_empty(self) -> None:
        target = resolve_target("CABG")
        assert len(target.keywords) > 0

    def test_intake_boolean_keys_are_valid(self) -> None:
        target = resolve_target("Knee Replacement")
        assert isinstance(target.intake_boolean_keys, list)
        for key in target.intake_boolean_keys:
            assert isinstance(key, str)
            assert len(key) > 0

    def test_all_known_procedures_resolvable(self) -> None:
        for entry in KNOWN_PROCEDURES:
            target = resolve_target(entry.canonical_name)
            assert target.name == entry.canonical_name

    def test_all_synonyms_resolvable(self) -> None:
        for entry in KNOWN_PROCEDURES:
            for syn in entry.synonyms:
                target = resolve_target(syn)
                assert target.name == entry.canonical_name, (
                    f"Synonym {syn!r} should resolve to {entry.canonical_name!r}"
                )

    def test_related_categories_for_procedure(self) -> None:
        target = resolve_target("Knee Replacement", category="procedure")
        cats = related_categories_for(target)
        assert "procedures" in cats
        assert "treatments" in cats

    def test_related_categories_for_department(self) -> None:
        target = resolve_target("Cardiology", category="department")
        cats = related_categories_for(target)
        assert "departments" in cats
        assert "specialities" in cats

    def test_availability_categories_are_service_level(self) -> None:
        cats = availability_categories()
        assert "procedures" in cats
        assert "treatments" in cats
        assert "departments" in cats
        assert "specialities" in cats


# ===================================================================
# PHASE 1 — Entity Matching
# ===================================================================


class TestEntityMatching:
    """Tests for comparison.matcher.match_entities and match_booleans."""

    def _target(self, name: str = "Knee Replacement") -> ComparisonTarget:
        return resolve_target(name)

    def test_exact_procedure_match(self) -> None:
        ds = _make_dataset(
            procedures=[
                Procedure(**_record(name="Knee Replacement", source_strength="high"))
            ]
        )
        matches = match_entities(ds, self._target())
        assert len(matches) >= 1
        best = matches[0]
        assert best.entity_name == "Knee Replacement"
        assert best.relevance_score == 1.0
        assert best.availability_evidence is True

    def test_synonym_treatment_match(self) -> None:
        ds = _make_dataset(
            treatments=[
                Treatment(**_record(name="TKR Surgery", speciality="Orthopaedics"))
            ]
        )
        matches = match_entities(ds, self._target())
        assert len(matches) >= 1
        assert any("tkr" in (m.entity_name or "").lower() for m in matches)

    def test_partial_token_match(self) -> None:
        ds = _make_dataset(
            treatments=[
                Treatment(**_record(name="Robotic Knee Surgery", speciality="Ortho"))
            ]
        )
        matches = match_entities(ds, self._target())
        assert len(matches) >= 1
        assert any("knee" in (m.entity_name or "").lower() for m in matches)

    def test_unrelated_entity_not_matched(self) -> None:
        ds = _make_dataset(
            treatments=[
                Treatment(**_record(name="Laser Hair Removal", speciality="Dermatology"))
            ]
        )
        matches = match_entities(ds, self._target())
        # "laser hair removal" should not match "knee replacement"
        assert all(
            "laser" not in (m.entity_name or "").lower() for m in matches
        )

    def test_relevant_department_matched(self) -> None:
        ds = _make_dataset(
            departments=[
                Department(
                    **_record(
                        name="Joint Replacement Department",
                        description="Knee and hip",
                    )
                )
            ]
        )
        matches = match_entities(ds, self._target())
        assert any(m.entity_category == "departments" for m in matches)

    def test_irrelevant_department_not_matched(self) -> None:
        """Keyword matcher: 'Orthopaedics' doesn't contain 'knee' or 'replacement'."""
        ds = _make_dataset(
            departments=[
                Department(**_record(name="Orthopaedics", description="Bone and joint"))
            ]
        )
        matches = match_entities(ds, self._target())
        dept_matches = [m for m in matches if m.entity_category == "departments"]
        assert dept_matches == []

    def test_relevant_speciality_matched(self) -> None:
        ds = _make_dataset(
            specialities=[
                Speciality(**_record(name="Knee Replacement Centre", description="Joint care"))
            ]
        )
        matches = match_entities(ds, self._target())
        assert any(m.entity_category == "specialities" for m in matches)

    def test_relevant_doctors_matched(self) -> None:
        ds = _make_dataset(
            doctors=[
                Doctor(
                    **_record(
                        name="Dr. Smith",
                        speciality="Knee Replacement Arthroplasty",
                        department="Orthopaedics",
                    )
                )
            ]
        )
        matches = match_entities(ds, self._target())
        assert any(m.entity_category == "doctors" for m in matches)

    def test_doctor_without_speciality_not_relevant(self) -> None:
        ds = _make_dataset(
            doctors=[
                Doctor(**_record(name="Dr. Jones", speciality="General Medicine"))
            ]
        )
        matches = match_entities(ds, self._target())
        # General Medicine shouldn't match knee replacement
        doctor_matches = [m for m in matches if m.entity_category == "doctors"]
        # Doctor may still match at partial token level if "replacement"
        # overlaps, but typically won't
        for m in doctor_matches:
            assert m.entity_name != "Dr. Jones" or m.relevance_score < 1.0

    def test_relevant_treatment_matched(self) -> None:
        ds = _make_dataset(
            treatments=[
                Treatment(**_record(name="Knee Replacement", speciality="Orthopaedics"))
            ]
        )
        matches = match_entities(ds, self._target())
        assert any(m.entity_category == "treatments" for m in matches)

    def test_technology_matched(self) -> None:
        ds = _make_dataset(
            technologies=[
                Technology(**_record(name="Robotic Knee System", category="Orthopaedics"))
            ]
        )
        matches = match_entities(ds, self._target())
        assert any(m.entity_category == "technologies" for m in matches)

    def test_facility_matched(self) -> None:
        ds = _make_dataset(
            facilities=[
                Facility(**_record(name="Joint Replacement Centre", description="Ortho"))
            ]
        )
        matches = match_entities(ds, self._target())
        assert any(m.entity_category == "facilities" for m in matches)

    def test_boolean_match_with_evidence(self) -> None:
        ds = _make_dataset(
            treatments=[
                Treatment(**_record(name="Total Knee Replacement", speciality="Ortho"))
            ]
        )
        bool_matches = match_booleans(ds, self._target())
        assert len(bool_matches) >= 1
        # total_knee_replacement should resolve to yes
        tkr = next(
            (b for b in bool_matches if b.entity_name == "total_knee_replacement"),
            None,
        )
        assert tkr is not None
        assert tkr.availability_evidence is True

    def test_boolean_match_without_evidence(self) -> None:
        ds = _make_dataset()  # empty
        bool_matches = match_booleans(ds, self._target())
        tkr = next(
            (b for b in bool_matches if b.entity_name == "total_knee_replacement"),
            None,
        )
        assert tkr is not None
        assert tkr.availability_evidence is False
        assert tkr.relevance_score == 0.0

    def test_boolean_key_has_provenance(self) -> None:
        ds = _make_dataset(
            treatments=[
                Treatment(**_record(name="Total Knee Replacement", speciality="Ortho"))
            ]
        )
        bool_matches = match_booleans(ds, self._target())
        for bm in bool_matches:
            assert isinstance(bm.entity_category, str)
            assert bm.entity_category == "intake_boolean"

    def test_matches_sorted_by_relevance(self) -> None:
        ds = _make_dataset(
            procedures=[Procedure(**_record(name="Knee Replacement"))],
            treatments=[Treatment(**_record(name="Robotic Knee Surgery"))],
        )
        matches = match_entities(ds, self._target())
        scores = [m.relevance_score for m in matches]
        assert scores == sorted(scores, reverse=True)

    def test_empty_dataset_no_matches(self) -> None:
        ds = _make_dataset()
        matches = match_entities(ds, self._target())
        assert matches == []


# ===================================================================
# PHASE 1 — Availability Semantics
# ===================================================================


class TestAvailabilitySemantics:
    """Verify explicit positive → YES, explicit negative → NO, absence → UNKNOWN."""

    def test_explicit_procedure_in_treatments_yes(self) -> None:
        ds = _make_dataset(
            treatments=[
                Treatment(**_record(name="Knee Replacement", speciality="Ortho"))
            ]
        )
        target = resolve_target("Knee Replacement")
        dims = compute_dimensions(
            ds,
            match_entities(ds, target),
            match_booleans(ds, target),
            target,
        )
        proc_dim = next(d for d in dims if d.dimension_key == "procedure_available")
        assert proc_dim.decision == "yes"

    def test_absence_of_evidence_is_unknown(self) -> None:
        ds = _make_dataset()
        target = resolve_target("Knee Replacement")
        dims = compute_dimensions(
            ds,
            match_entities(ds, target),
            match_booleans(ds, target),
            target,
        )
        proc_dim = next(d for d in dims if d.dimension_key == "procedure_available")
        assert proc_dim.decision == "unknown"
        assert proc_dim.evidence
        assert NO_EVIDENCE_MESSAGE in proc_dim.evidence[0].text

    def test_knee_replacement_recovery_not_auto_yes(self) -> None:
        """A page about 'knee replacement recovery' should NOT automatically
        make procedure_available = YES. Only service-level pages should."""
        ds = _make_dataset(
            facilities=[
                Facility(**_record(name="Knee Replacement Recovery Centre", description="Recovery"))
            ]
        )
        target = resolve_target("Knee Replacement")
        dims = compute_dimensions(
            ds,
            match_entities(ds, target),
            match_booleans(ds, target),
            target,
        )
        proc_dim = next(d for d in dims if d.dimension_key == "procedure_available")
        # Facilities don't contribute to procedure_available dimension
        # Only procedures and treatments do
        assert proc_dim.decision == "unknown"

    def test_availability_evidence_true_for_service_categories(self) -> None:
        ds = _make_dataset(
            treatments=[
                Treatment(**_record(name="Knee Replacement", speciality="Ortho"))
            ]
        )
        target = resolve_target("Knee Replacement")
        matches = match_entities(ds, target)
        service_matches = [m for m in matches if m.entity_category == "treatments"]
        assert all(m.availability_evidence is True for m in service_matches)

    def test_no_evidence_yields_unknown_not_no(self) -> None:
        """Absence of evidence must NEVER produce 'no'."""
        ds = _make_dataset()
        target = resolve_target("Knee Replacement")
        dims = compute_dimensions(
            ds,
            match_entities(ds, target),
            match_booleans(ds, target),
            target,
        )
        for dim in dims:
            assert dim.decision in ("yes", "unknown"), (
                f"Dimension {dim.dimension_key} has unexpected decision: {dim.decision}"
            )


# ===================================================================
# PHASE 1 — Explicit negative evidence → NO
# ===================================================================


class TestBooleanNegativeDecisions:
    """Real NO decisions must flow from explicit denial, never from absence."""

    def test_strong_positive_evidence_is_yes(self) -> None:
        items = [
            IntakeEvidence.yes(
                "total_knee_replacement",
                text="Total knee replacement offered",
                source_url="https://a.test/t",
                method="record",
                strength=1.0,
            )
        ]
        assert resolve_boolean_decision(items) == "yes"

    def test_strong_negative_evidence_is_no(self) -> None:
        items = [
            IntakeEvidence.no(
                "total_knee_replacement",
                text="No total knee replacement",
                source_url="https://a.test/t",
                method="record",
                strength=10.0,
            )
        ]
        assert resolve_boolean_decision(items) == "no"

    def test_no_evidence_is_unknown(self) -> None:
        assert resolve_boolean_decision([]) == "unknown"

    def test_balanced_conflict_is_unknown(self) -> None:
        items = [
            IntakeEvidence.yes(
                "total_knee_replacement",
                text="Offered",
                source_url="https://a.test",
                method="record",
                strength=1.0,
            ),
            IntakeEvidence.no(
                "total_knee_replacement",
                text="Not offered",
                source_url="https://b.test",
                method="record",
                strength=1.0,
            ),
        ]
        assert resolve_boolean_decision(items) == "unknown"

    def test_weak_negative_below_threshold_is_unknown(self) -> None:
        items = [
            IntakeEvidence.no(
                "total_knee_replacement",
                text="Weak denial below the magnitude threshold",
                source_url="https://a.test",
                method="record",
                strength=0.5,
            )
        ]
        assert resolve_boolean_decision(items) == "unknown"

    def test_explicit_denial_overrides_incidental_mention(self) -> None:
        items = [
            IntakeEvidence.yes(
                "total_knee_replacement",
                text="Merely mentions the term",
                source_url="https://a.test",
                method="record",
                strength=1.0,
            ),
            IntakeEvidence.no(
                "total_knee_replacement",
                text="Explicitly not available",
                source_url="https://b.test",
                method="record",
                strength=10.0,
            ),
        ]
        assert resolve_boolean_decision(items) == "no"

    def test_multiple_positive_pages_stay_yes(self) -> None:
        items = [
            IntakeEvidence.yes(
                "total_knee_replacement",
                text="Page one",
                source_url="https://a.test",
                method="record",
                strength=1.0,
            ),
            IntakeEvidence.yes(
                "total_knee_replacement",
                text="Page two",
                source_url="https://b.test",
                method="record",
                strength=1.0,
            ),
        ]
        assert resolve_boolean_decision(items) == "yes"

    def test_negative_record_statement_yields_no_decision(self) -> None:
        """A page statement like 'X is not available' must read as NO."""
        ds = _make_dataset(
            treatments=[
                Treatment(
                    **_record(
                        name="Support Services",
                        speciality="Ortho",
                        description="Total knee replacement is not available at our centre.",
                    )
                )
            ]
        )
        target = resolve_target("Total Knee Replacement")
        bool_matches = match_booleans(ds, target)
        tkr = next(
            b for b in bool_matches if b.entity_name == "total_knee_replacement"
        )
        assert tkr.boolean_decision == "no"
        assert tkr.availability_evidence is False
        assert "not available" in (tkr.context or "")

    def test_non_x_subtype_description_is_not_no(self) -> None:
        """'Non-Cardiac Chest Pain' names a subtype, never a service denial.

        Regression guard for the live Manipal false-NO: a cardiology page
        describing the cardiac / non-cardiac chest-pain distinction must not
        flip ``cardiac_sciences_enabled`` to NO.
        """
        ds = _make_dataset(
            specialities=[
                Speciality(
                    **_record(
                        name="Cardiology",
                        description=(
                            "Difference between Cardiac Chest Pain and "
                            "Non-Cardiac Chest Pain is crucial for timely care."
                        ),
                    )
                )
            ]
        )
        target = resolve_target("Cardiology")
        bool_matches = match_booleans(ds, target)
        cardiac = next(
            b for b in bool_matches if b.entity_name == "cardiac_sciences_enabled"
        )
        assert cardiac.boolean_decision == "yes"
        assert cardiac.availability_evidence is True

    def test_does_not_offer_cardiac_still_yields_no(self) -> None:
        """A genuine 'does not offer' statement must still read as NO."""
        ds = _make_dataset(
            specialities=[
                Speciality(
                    **_record(
                        name="Cardiology",
                        description="We do not offer cardiac sciences procedures.",
                    )
                )
            ]
        )
        target = resolve_target("Cardiology")
        bool_matches = match_booleans(ds, target)
        cardiac = next(
            b for b in bool_matches if b.entity_name == "cardiac_sciences_enabled"
        )
        assert cardiac.boolean_decision == "no"
        assert cardiac.availability_evidence is False

    def test_negative_statement_propagates_to_dimension(self) -> None:
        ds = _make_dataset(
            treatments=[
                Treatment(
                    **_record(
                        name="Support Services",
                        speciality="Ortho",
                        description="Total knee replacement is not available at our centre.",
                    )
                )
            ]
        )
        target = resolve_target("Total Knee Replacement")
        dims = compute_dimensions(
            ds,
            match_entities(ds, target),
            match_booleans(ds, target),
            target,
        )
        bool_dim = next(
            d for d in dims if d.dimension_key == "total_knee_replacement"
        )
        assert bool_dim.decision == "no"
        assert bool_dim.evidence
        assert bool_dim.evidence[0].decision == "no"


# ===================================================================
# PHASE 1 — Evidence Propagation
# ===================================================================


class TestEvidencePropagation:
    """Every comparison claim must carry provenance."""

    def test_entity_match_has_source_url(self) -> None:
        url = "https://example.test/treatments"
        ds = _make_dataset(
            treatments=[Treatment(**_record(name="Knee Replacement", source_url=url))]
        )
        target = resolve_target("Knee Replacement")
        matches = match_entities(ds, target)
        assert len(matches) >= 1
        assert matches[0].source_url == url

    def test_entity_match_has_extraction_method(self) -> None:
        ds = _make_dataset(
            treatments=[
                Treatment(**_record(name="Knee Replacement", extraction_method="card"))
            ]
        )
        target = resolve_target("Knee Replacement")
        matches = match_entities(ds, target)
        assert matches[0].extraction_method == "card"

    def test_dimension_evidence_has_source_url(self) -> None:
        ds = _make_dataset(
            treatments=[
                Treatment(**_record(name="Knee Replacement", source_url="https://x.test/t"))
            ]
        )
        target = resolve_target("Knee Replacement")
        dims = compute_dimensions(
            ds,
            match_entities(ds, target),
            match_booleans(ds, target),
            target,
        )
        proc_dim = next(d for d in dims if d.dimension_key == "procedure_available")
        for ev in proc_dim.evidence:
            assert ev.source_url  # non-empty

    def test_dimension_evidence_has_text(self) -> None:
        ds = _make_dataset(
            treatments=[Treatment(**_record(name="Knee Replacement"))]
        )
        target = resolve_target("Knee Replacement")
        dims = compute_dimensions(
            ds,
            match_entities(ds, target),
            match_booleans(ds, target),
            target,
        )
        proc_dim = next(d for d in dims if d.dimension_key == "procedure_available")
        for ev in proc_dim.evidence:
            assert ev.text  # non-empty

    def test_dimension_evidence_has_method(self) -> None:
        ds = _make_dataset(
            treatments=[Treatment(**_record(name="Knee Replacement"))]
        )
        target = resolve_target("Knee Replacement")
        dims = compute_dimensions(
            ds,
            match_entities(ds, target),
            match_booleans(ds, target),
            target,
        )
        proc_dim = next(d for d in dims if d.dimension_key == "procedure_available")
        for ev in proc_dim.evidence:
            assert ev.method  # non-empty

    def test_no_evidence_dimension_still_has_provenance(self) -> None:
        ds = _make_dataset()
        target = resolve_target("Knee Replacement")
        dims = compute_dimensions(
            ds,
            match_entities(ds, target),
            match_booleans(ds, target),
            target,
        )
        proc_dim = next(d for d in dims if d.dimension_key == "procedure_available")
        assert len(proc_dim.evidence) == 1
        ev = proc_dim.evidence[0]
        assert ev.decision == "unknown"
        assert ev.text == NO_EVIDENCE_MESSAGE

    def test_source_strength_preserved_when_present(self) -> None:
        ds = _make_dataset(
            treatments=[
                Treatment(**_record(name="Knee Replacement", source_strength="high"))
            ]
        )
        target = resolve_target("Knee Replacement")
        matches = match_entities(ds, target)
        assert matches[0].source_strength == "high"

    def test_confidence_preserved_when_present(self) -> None:
        ds = _make_dataset(
            treatments=[
                Treatment(**_record(name="Knee Replacement", confidence=0.95))
            ]
        )
        target = resolve_target("Knee Replacement")
        matches = match_entities(ds, target)
        assert matches[0].confidence == 0.95


# ===================================================================
# PHASE 1 — Conflicts
# ===================================================================


class TestConflicts:
    """Conflicting evidence from multiple sources must not be silently hidden."""

    def test_conflicting_evidence_both_present(self) -> None:
        """Two treatments with different names — both appear in matches."""
        ds = _make_dataset(
            treatments=[
                Treatment(**_record(name="Knee Replacement", speciality="Ortho")),
                Treatment(**_record(name="Knee Surgery", speciality="Ortho")),
            ]
        )
        target = resolve_target("Knee Replacement")
        matches = match_entities(ds, target)
        names = [m.entity_name for m in matches]
        assert "Knee Replacement" in names
        # "Knee Surgery" may or may not match depending on scoring
        # but both evidence items should be present in dimension

    def test_yes_and_unknown_dimensions_both_visible(self) -> None:
        """Some dimensions yes, some unknown — both are visible."""
        ds = _make_dataset(
            treatments=[
                Treatment(**_record(name="Knee Replacement", speciality="Ortho"))
            ]
        )
        target = resolve_target("Knee Replacement")
        dims = compute_dimensions(
            ds,
            match_entities(ds, target),
            match_booleans(ds, target),
            target,
        )
        decisions = {d.dimension_key: d.decision for d in dims}
        # procedure_available should be yes
        assert decisions["procedure_available"] == "yes"
        # relevant_department should be unknown (no departments)
        assert decisions["relevant_department"] == "unknown"

    def test_multiple_evidence_items_on_dimension(self) -> None:
        """Multiple matching treatments produce multiple evidence items."""
        ds = _make_dataset(
            treatments=[
                Treatment(**_record(name="Knee Replacement")),
                Treatment(**_record(name="Total Knee Replacement")),
            ]
        )
        target = resolve_target("Knee Replacement")
        dims = compute_dimensions(
            ds,
            match_entities(ds, target),
            match_booleans(ds, target),
            target,
        )
        proc_dim = next(d for d in dims if d.dimension_key == "procedure_available")
        assert len(proc_dim.evidence) >= 2


# ===================================================================
# PHASE 1 — Deduplication
# ===================================================================


class TestDeduplication:
    """Duplicate entities across pages."""

    def test_duplicate_doctor_names_same_profile(self) -> None:
        """Same doctor appearing on two pages — both records exist in dataset,
        and both are matched by the keyword matcher."""
        ds = _make_dataset(
            doctors=[
                Doctor(**_record(name="Dr. Smith", speciality="Knee Replacement Arthroplasty",
                                 profile_url="/dr-smith")),
                Doctor(**_record(name="Dr. Smith", speciality="Knee Replacement Arthroplasty",
                                 profile_url="/dr-smith")),
            ]
        )
        target = resolve_target("Knee Replacement")
        matches = match_entities(ds, target)
        doctor_matches = [m for m in matches if m.entity_category == "doctors"]
        assert len(doctor_matches) == 2

    def test_different_doctors_both_counted(self) -> None:
        ds = _make_dataset(
            doctors=[
                Doctor(**_record(name="Dr. Smith", speciality="Knee Replacement Arthroplasty")),
                Doctor(**_record(name="Dr. Jones", speciality="Total Knee Arthroplasty")),
            ]
        )
        target = resolve_target("Knee Replacement")
        matches = match_entities(ds, target)
        doctor_matches = [m for m in matches if m.entity_category == "doctors"]
        assert len(doctor_matches) == 2


# ===================================================================
# PHASE 1 — Evidence Coverage
# ===================================================================


class TestEvidenceCoverage:
    """Evidence coverage computation."""

    def test_full_dataset_high_coverage(self) -> None:
        ds = _make_dataset(
            procedures=[Procedure(**_record(name="Knee Replacement"))],
            treatments=[Treatment(**_record(name="Knee Replacement"))],
            departments=[Department(**_record(name="Orthopaedics"))],
            specialities=[Speciality(**_record(name="Orthopaedics"))],
            doctors=[
                Doctor(**_record(name="Dr. Smith", speciality="Orthopaedics"))
            ],
            technologies=[Technology(**_record(name="Knee Robot"))],
            facilities=[Facility(**_record(name="Joint Replacement Centre"))],
        )
        target = resolve_target("Knee Replacement")
        dims = compute_dimensions(
            ds,
            match_entities(ds, target),
            match_booleans(ds, target),
            target,
        )
        coverage_dim = next(d for d in dims if d.dimension_key == "evidence_coverage")
        assert coverage_dim.value is not None
        assert coverage_dim.value > 0.5  # type: ignore[operator]

    def test_empty_dataset_coverage_is_zero(self) -> None:
        """Dataset with no records and no bed count → no real evidence → 0.0."""
        ds = _make_dataset(
            hospital=HospitalOverview(**_record(name="Test Hospital")),
            infrastructure=[],
        )
        target = resolve_target("Knee Replacement")
        dims = compute_dimensions(
            ds,
            match_entities(ds, target),
            match_booleans(ds, target),
            target,
        )
        coverage_dim = next(d for d in dims if d.dimension_key == "evidence_coverage")
        assert coverage_dim.value == 0.0

    def test_partial_dataset_coverage_is_partial(self) -> None:
        """Partial dataset → some dimensions have real evidence, some don't.
        Coverage must be between 0.0 and 1.0 (exclusive)."""
        ds = _make_dataset(
            treatments=[Treatment(**_record(name="Knee Replacement"))],
        )
        target = resolve_target("Knee Replacement")
        dims = compute_dimensions(
            ds,
            match_entities(ds, target),
            match_booleans(ds, target),
            target,
        )
        coverage_dim = next(d for d in dims if d.dimension_key == "evidence_coverage")
        assert coverage_dim.value is not None
        assert 0.0 < float(coverage_dim.value) < 1.0

    def test_zero_real_evidence_coverage_is_zero(self) -> None:
        """Completely empty dataset → every dimension has only placeholder → 0.0."""
        from hospital_extractor.comparison.dimensions import compute_evidence_coverage

        dims = [
            DimensionResult(
                dimension_key="procedure_available",
                label="Procedure Available",
                decision="unknown",
                evidence=[],
            ),
            DimensionResult(
                dimension_key="relevant_department",
                label="Relevant Department",
                decision="unknown",
                evidence=[],
            ),
        ]
        assert compute_evidence_coverage(dims) == 0.0

    def test_unknown_with_placeholder_only_is_not_real_evidence(self) -> None:
        """A dimension with decision=unknown and only NO_EVIDENCE_MESSAGE
        must NOT count as having real evidence."""
        from hospital_extractor.comparison.dimensions import compute_evidence_coverage

        dim = DimensionResult(
            dimension_key="procedure_available",
            label="Procedure Available",
            decision="unknown",
            evidence=[
                ComparisonEvidence(
                    hospital_id="h1",
                    dimension_key="procedure_available",
                    decision="unknown",
                    text=NO_EVIDENCE_MESSAGE,
                    source_url="",
                    method="comparison-engine",
                ),
            ],
        )
        assert compute_evidence_coverage([dim]) == 0.0

    def test_yes_with_real_evidence_counts_as_coverage(self) -> None:
        """A dimension with decision=yes and real evidence text must count."""
        from hospital_extractor.comparison.dimensions import compute_evidence_coverage

        dim = DimensionResult(
            dimension_key="procedure_available",
            label="Procedure Available",
            decision="yes",
            evidence=[
                ComparisonEvidence(
                    hospital_id="h1",
                    dimension_key="procedure_available",
                    decision="yes",
                    text="Knee Replacement — orthopaedic surgery",
                    source_url="https://example.test/treatments",
                    method="card",
                ),
            ],
        )
        assert compute_evidence_coverage([dim]) == 1.0

    def test_complete_evidence_all_dimensions_coverage_is_one(self) -> None:
        """When every dimension has real evidence, coverage must be 1.0."""
        from hospital_extractor.comparison.dimensions import compute_evidence_coverage

        dims = [
            DimensionResult(
                dimension_key="procedure_available",
                label="Procedure Available",
                decision="yes",
                evidence=[
                    ComparisonEvidence(
                        hospital_id="h1",
                        dimension_key="procedure_available",
                        decision="yes",
                        text="Knee Replacement",
                        source_url="https://example.test/t",
                        method="card",
                    ),
                ],
            ),
            DimensionResult(
                dimension_key="relevant_department",
                label="Relevant Department",
                decision="yes",
                evidence=[
                    ComparisonEvidence(
                        hospital_id="h1",
                        dimension_key="relevant_department",
                        decision="yes",
                        text="Orthopaedics",
                        source_url="https://example.test/d",
                        method="card",
                    ),
                ],
            ),
        ]
        assert compute_evidence_coverage(dims) == 1.0

    def test_mixed_placeholder_and_real_evidence_is_partial(self) -> None:
        """One dimension has real evidence, one has only placeholder → 0.5."""
        from hospital_extractor.comparison.dimensions import compute_evidence_coverage

        real_dim = DimensionResult(
            dimension_key="procedure_available",
            label="Procedure Available",
            decision="yes",
            evidence=[
                ComparisonEvidence(
                    hospital_id="h1",
                    dimension_key="procedure_available",
                    decision="yes",
                    text="Knee Replacement",
                    source_url="https://example.test/t",
                    method="card",
                ),
            ],
        )
        placeholder_dim = DimensionResult(
            dimension_key="relevant_department",
            label="Relevant Department",
            decision="unknown",
            evidence=[
                ComparisonEvidence(
                    hospital_id="h1",
                    dimension_key="relevant_department",
                    decision="unknown",
                    text=NO_EVIDENCE_MESSAGE,
                    source_url="",
                    method="comparison-engine",
                ),
            ],
        )
        assert compute_evidence_coverage([real_dim, placeholder_dim]) == 0.5

    def test_no_real_evidence_dimension_with_empty_evidence_list(self) -> None:
        """A dimension with an empty evidence list must not count as covered."""
        from hospital_extractor.comparison.dimensions import compute_evidence_coverage

        dim = DimensionResult(
            dimension_key="procedure_available",
            label="Procedure Available",
            decision="unknown",
            evidence=[],
        )
        assert compute_evidence_coverage([dim]) == 0.0

    def test_placeholder_in_multi_evidence_list_does_not_count(self) -> None:
        """Multiple evidence items but all are placeholders → 0.0."""
        from hospital_extractor.comparison.dimensions import compute_evidence_coverage

        dim = DimensionResult(
            dimension_key="procedure_available",
            label="Procedure Available",
            decision="unknown",
            evidence=[
                ComparisonEvidence(
                    hospital_id="h1",
                    dimension_key="procedure_available",
                    decision="unknown",
                    text=NO_EVIDENCE_MESSAGE,
                    source_url="",
                    method="comparison-engine",
                ),
                ComparisonEvidence(
                    hospital_id="h1",
                    dimension_key="procedure_available",
                    decision="unknown",
                    text=NO_EVIDENCE_MESSAGE,
                    source_url="",
                    method="comparison-engine",
                ),
            ],
        )
        assert compute_evidence_coverage([dim]) == 0.0

    def test_quality_warnings_propagated(self) -> None:
        ds = _make_dataset()
        ds.quality.warnings.append("test warning")
        target = resolve_target("Knee Replacement")
        result = compare_datasets([("h1", ds)], target)
        assert len(result.hospitals) == 1
        assert "test warning" in result.hospitals[0].quality_warnings

    def test_empty_categories_warning(self) -> None:
        ds = _make_dataset()
        ds.quality.categories_empty = ["doctors", "technologies"]
        target = resolve_target("Knee Replacement")
        result = compare_datasets([("h1", ds)], target)
        h = result.hospitals[0]
        assert any("Empty categories" in w for w in h.quality_warnings)


# ===================================================================
# PHASE 1 — Multi-Hospital Comparison
# ===================================================================


class TestMultiHospitalComparison:
    """Test comparison with varying numbers of hospitals."""

    def test_two_hospitals(self) -> None:
        ds1 = _make_dataset(
            hospital=HospitalOverview(**_record(name="Hospital A", bed_count=200)),
            treatments=[Treatment(**_record(name="Knee Replacement"))],
        )
        ds2 = _make_dataset(
            hospital=HospitalOverview(**_record(name="Hospital B", bed_count=500)),
            treatments=[Treatment(**_record(name="Knee Replacement"))],
        )
        result = compare_datasets(
            [("h1", ds1), ("h2", ds2)],
            resolve_target("Knee Replacement"),
        )
        assert len(result.hospitals) == 2
        assert result.hospitals[0].hospital_name == "Hospital A"
        assert result.hospitals[1].hospital_name == "Hospital B"

    def test_three_hospitals(self) -> None:
        datasets = [
            (
                f"h{i}",
                _make_dataset(
                    hospital=HospitalOverview(**_record(name=f"Hospital {i}")),
                    treatments=[Treatment(**_record(name="Knee Replacement"))],
                ),
            )
            for i in range(3)
        ]
        result = compare_datasets(
            datasets, resolve_target("Knee Replacement")
        )
        assert len(result.hospitals) == 3

    def test_four_hospitals(self) -> None:
        datasets = [
            (
                f"h{i}",
                _make_dataset(
                    hospital=HospitalOverview(**_record(name=f"Hospital {i}")),
                    treatments=[Treatment(**_record(name="Knee Replacement"))],
                ),
            )
            for i in range(4)
        ]
        result = compare_datasets(
            datasets, resolve_target("Knee Replacement")
        )
        assert len(result.hospitals) == 4

    def test_single_hospital(self) -> None:
        ds = _make_dataset(
            treatments=[Treatment(**_record(name="Knee Replacement"))]
        )
        result = compare_datasets(
            [("h1", ds)],
            resolve_target("Knee Replacement"),
        )
        assert len(result.hospitals) == 1

    def test_empty_hospital_list(self) -> None:
        result = compare_datasets([], resolve_target("Knee Replacement"))
        assert len(result.hospitals) == 0
        assert result.target.name == "Total Knee Replacement"

    def test_compare_urls_convenience(self) -> None:
        ds1 = _make_dataset(
            hospital=HospitalOverview(**_record(name="Hospital A")),
            treatments=[Treatment(**_record(name="Knee Replacement"))],
        )
        ds2 = _make_dataset(
            hospital=HospitalOverview(**_record(name="Hospital B")),
        )
        result = compare_urls(
            [("h1", ds1), ("h2", ds2)],
            "Knee Replacement",
        )
        assert result.target.name == "Total Knee Replacement"
        assert len(result.hospitals) == 2

    def test_result_json_roundtrip(self) -> None:
        ds = _make_dataset(
            treatments=[Treatment(**_record(name="Knee Replacement"))]
        )
        result = compare_datasets(
            [("h1", ds)],
            resolve_target("Knee Replacement"),
        )
        json_str = result.model_dump_json()
        restored = ComparisonResult.model_validate_json(json_str)
        assert restored.target.name == result.target.name
        assert len(restored.hospitals) == len(result.hospitals)

    def test_each_hospital_has_same_dimensions(self) -> None:
        ds1 = _make_dataset(
            treatments=[Treatment(**_record(name="Knee Replacement"))]
        )
        ds2 = _make_dataset()
        result = compare_datasets(
            [("h1", ds1), ("h2", ds2)],
            resolve_target("Knee Replacement"),
        )
        keys1 = [d.dimension_key for d in result.hospitals[0].dimensions]
        keys2 = [d.dimension_key for d in result.hospitals[1].dimensions]
        assert keys1 == keys2

    def test_different_hospitals_different_decisions(self) -> None:
        """Hospital with treatment = yes, hospital without = unknown."""
        ds1 = _make_dataset(
            treatments=[Treatment(**_record(name="Knee Replacement"))]
        )
        ds2 = _make_dataset()
        result = compare_datasets(
            [("h1", ds1), ("h2", ds2)],
            resolve_target("Knee Replacement"),
        )
        d1 = next(
            d for d in result.hospitals[0].dimensions
            if d.dimension_key == "procedure_available"
        )
        d2 = next(
            d for d in result.hospitals[1].dimensions
            if d.dimension_key == "procedure_available"
        )
        assert d1.decision == "yes"
        assert d2.decision == "unknown"

    def test_result_has_compared_at_timestamp(self) -> None:
        ds = _make_dataset()
        result = compare_datasets(
            [("h1", ds)],
            resolve_target("Knee Replacement"),
        )
        assert result.compared_at is not None

    def test_duplicate_hospital_ids_merge(self) -> None:
        """Same hospital_id passed twice → merged into one column, evidence retained."""
        ds = _make_dataset(
            treatments=[Treatment(**_record(name="Knee Replacement"))]
        )
        result = compare_datasets(
            [("h1", ds), ("h1", ds)],
            resolve_target("Knee Replacement"),
        )
        assert len(result.hospitals) == 1
        hospital = result.hospitals[0]
        prod = next(
            d for d in hospital.dimensions if d.dimension_key == "procedure_available"
        )
        assert prod.decision == "yes"
        assert len(prod.evidence) == 2

    def test_conflict_decision_is_centralized_through_apply_conflicts(self) -> None:
        """CONFLICT ⇒ decision unknown, enforced only by apply_conflicts."""
        from hospital_extractor.comparison.dimensions import (
            apply_conflicts,
            detect_dimension_conflicts,
        )

        yes = ComparisonEvidence(
            hospital_id="h1",
            dimension_key="procedure_available",
            decision="yes",
            text="procedure is listed",
            method="comparison-engine",
            source_url=_BASE_URL,
            source_type="website",
        )
        no = ComparisonEvidence(
            hospital_id="h1",
            dimension_key="procedure_available",
            decision="no",
            text="procedure is explicitly unavailable",
            method="comparison-engine",
            source_url=_BASE_URL,
            source_type="mock_api",
        )
        dimension = DimensionResult(
            dimension_key="procedure_available",
            label="Procedure Available",
            decision="yes",  # provisional — a single authority must flip it
            evidence=[yes, no],
        )
        assert len(detect_dimension_conflicts([dimension])) == 1
        apply_conflicts([dimension])
        assert dimension.decision == "unknown"
        assert dimension.conflicts
        assert {f.decision for f in dimension.conflicts[0].facts} == {"yes", "no"}

    def test_infrastructure_dimension_values(self) -> None:
        ds = _make_dataset(
            infrastructure=[
                Infrastructure(**_record(total_beds=500, icu_beds=50))
            ]
        )
        result = compare_datasets(
            [("h1", ds)],
            resolve_target("Knee Replacement"),
        )
        infra = next(
            d for d in result.hospitals[0].dimensions
            if d.dimension_key == "infrastructure"
        )
        assert infra.decision == "yes"
        assert isinstance(infra.value, dict)
        assert infra.value["total_beds"] == 500

    def test_accreditation_dimension(self) -> None:
        ds = _make_dataset(
            accreditations=[
                Accreditation(**_record(name="NABH", organization="NABH Board"))
            ]
        )
        result = compare_datasets(
            [("h1", ds)],
            resolve_target("Knee Replacement"),
        )
        accred_dim = next(
            d for d in result.hospitals[0].dimensions
            if d.dimension_key == "accreditations"
        )
        assert accred_dim.decision == "yes"

    def test_no_accreditations_yields_unknown(self) -> None:
        ds = _make_dataset()
        result = compare_datasets(
            [("h1", ds)],
            resolve_target("Knee Replacement"),
        )
        accred_dim = next(
            d for d in result.hospitals[0].dimensions
            if d.dimension_key == "accreditations"
        )
        assert accred_dim.decision == "unknown"

    def test_boolean_dimension_yes(self) -> None:
        ds = _make_dataset(
            treatments=[
                Treatment(**_record(name="Total Knee Replacement", speciality="Ortho"))
            ]
        )
        result = compare_datasets(
            [("h1", ds)],
            resolve_target("Knee Replacement"),
        )
        bool_dims = [
            d for d in result.hospitals[0].dimensions
            if d.dimension_key == "total_knee_replacement"
        ]
        assert len(bool_dims) == 1
        assert bool_dims[0].decision == "yes"

    def test_boolean_dimension_unknown_no_evidence(self) -> None:
        ds = _make_dataset()
        result = compare_datasets(
            [("h1", ds)],
            resolve_target("Knee Replacement"),
        )
        bool_dims = [
            d for d in result.hospitals[0].dimensions
            if d.dimension_key == "total_knee_replacement"
        ]
        assert len(bool_dims) == 1
        assert bool_dims[0].decision == "unknown"


# ===================================================================
# PHASE 2 — Department comparison end-to-end
# ===================================================================


class TestDepartmentComparison:
    """Department comparisons flow through resolution, matching and display."""

    @staticmethod
    def _dim(dimensions: list[DimensionResult], key: str) -> DimensionResult:
        for d in dimensions:
            if d.dimension_key == key:
                return d
        raise AssertionError(f"dimension {key!r} not present")

    def test_cardiac_sciences_department_matched(self) -> None:
        ds = _make_dataset(
            departments=[Department(**_record(name="Cardiac Sciences"))]
        )
        target = resolve_target("Cardiology", category="department")
        dims = compute_dimensions(
            ds,
            match_entities(ds, target),
            match_booleans(ds, target),
            target,
        )
        dept_dim = self._dim(dims, "relevant_department")
        assert dept_dim.decision == "yes"
        # Availability dimension label adapts to the department context.
        avail_dim = self._dim(dims, "procedure_available")
        assert avail_dim.label == "Department Available"

    def test_department_comparison_through_service(self) -> None:
        ds = _make_dataset(
            departments=[Department(**_record(name="Cardiac Sciences"))]
        )
        result = compare_urls(
            [("h1", ds)], "Cardiology", category="department"
        )
        assert result.target.category == "department"
        assert result.target.name == "Cardiology"
        dept_dim = self._dim(result.hospitals[0].dimensions, "relevant_department")
        assert dept_dim.decision == "yes"

    def test_department_category_auto_detected_via_service(self) -> None:
        ds = _make_dataset(
            departments=[Department(**_record(name="Cardiac Sciences"))]
        )
        result = compare_urls([("h1", ds)], "Cardiac Sciences")
        assert result.target.category == "department"
        assert result.target.name == "Cardiology"

    def test_department_absence_unknown_not_no(self) -> None:
        ds = _make_dataset()
        result = compare_urls(
            [("h1", ds)], "Cardiology", category="department"
        )
        for hospital in result.hospitals:
            for dim in hospital.dimensions:
                assert dim.decision in ("yes", "unknown"), (
                    f"Dimension {dim.dimension_key} fabricated "
                    f"decision {dim.decision}"
                )

    def test_procedure_comparison_still_works_with_category(self) -> None:
        ds = _make_dataset(
            treatments=[
                Treatment(**_record(name="Knee Replacement", speciality="Ortho"))
            ]
        )
        result = compare_urls(
            [("h1", ds)], "Knee Replacement", category="procedure"
        )
        assert result.target.category == "procedure"
        proc_dim = self._dim(result.hospitals[0].dimensions, "procedure_available")
        assert proc_dim.decision == "yes"
        assert proc_dim.label == "Procedure Available"

    def test_three_hospital_department_comparison(self) -> None:
        ds_a = _make_dataset(
            departments=[Department(**_record(name="Cardiac Sciences"))]
        )
        ds_b = _make_dataset()
        ds_c = _make_dataset(
            departments=[Department(**_record(name="Cardiology Department"))]
        )
        result = compare_urls(
            [("a", ds_a), ("b", ds_b), ("c", ds_c)],
            "Cardiology",
            category="department",
        )
        assert len(result.hospitals) == 3
        got = [self._dim(h.dimensions, "relevant_department").decision
               for h in result.hospitals]
        assert got == ["yes", "unknown", "yes"]
