"""Tests for deterministic custom-prompt interpretation."""

from __future__ import annotations

from hospital_extractor.comparison.interpret import interpret_prompt, standard_criteria

EXAMPLE_PROMPT = (
    "Compare parking availability, vegetarian meal options, private rooms, "
    "visiting hours and availability of wheelchair assistance."
)


def test_example_prompt_splits_into_five_criteria() -> None:
    response = interpret_prompt(EXAMPLE_PROMPT)

    labels = [criterion.label for criterion in response.criteria]
    assert labels == [
        "Parking Availability",
        "Vegetarian Meal Options",
        "Private Rooms",
        "Visiting Hours",
        "Wheelchair Assistance",
    ]
    assert all(criterion.origin == "custom" for criterion in response.criteria)
    assert response.clarification_questions == []


def test_each_custom_criterion_states_what_evidence_is_needed() -> None:
    response = interpret_prompt(EXAMPLE_PROMPT)

    for criterion in response.criteria:
        assert "citable source" in criterion.evidence_needed
        assert criterion.label.lower() in criterion.evidence_needed.lower()


def test_verbatim_user_wording_is_preserved() -> None:
    response = interpret_prompt(EXAMPLE_PROMPT)

    assert "availability of wheelchair assistance" in (
        response.criteria[-1].original_text or ""
    )


def test_command_framing_is_removed() -> None:
    response = interpret_prompt("Please can you compare parking and visiting hours")

    assert [criterion.label for criterion in response.criteria] == [
        "Parking",
        "Visiting Hours",
    ]


def test_ambiguous_wording_asks_for_clarification_instead_of_inventing() -> None:
    response = interpret_prompt("compare the good things about these hospitals")

    assert response.criteria == []
    assert len(response.clarification_questions) == 1
    assert "good" in response.clarification_questions[0]


def test_empty_prompt_produces_guidance_question() -> None:
    response = interpret_prompt("   ")

    assert response.criteria == []
    assert response.clarification_questions


def test_duplicate_clauses_are_dropped() -> None:
    response = interpret_prompt("compare parking, parking, and visiting hours")

    assert [criterion.label for criterion in response.criteria] == ["Parking", "Visiting Hours"]


def test_custom_criterion_overlapping_a_standard_is_flagged() -> None:
    response = interpret_prompt("compare wheelchair ramp access and parking")

    by_label = {criterion.label: criterion for criterion in response.criteria}
    assert by_label["Wheelchair Ramp Access"].overlaps_standard == "accessibility"
    assert by_label["Parking"].overlaps_standard is None


def test_no_hospital_information_is_generated() -> None:
    response = interpret_prompt("compare private rooms")

    assert response.interpreter == "deterministic_clause_parser"
    assert "No language model is configured" in response.interpretation_note
    assert "No hospital information" in response.interpretation_note


def test_standard_criteria_match_the_twelve_required_labels() -> None:
    labels = [criterion.label for criterion in standard_criteria()]

    assert labels == [
        "Affordability",
        "Location",
        "Doctor experience",
        "Department expertise",
        "Infrastructure",
        "Emergency services",
        "Doctor availability",
        "Patient reviews",
        "Diagnostic facilities",
        "Insurance compatibility",
        "Accessibility",
        "Treatment facilities",
    ]
    assert all(criterion.origin == "standard" for criterion in standard_criteria())


def test_standard_criterion_without_exposed_fields_still_explains_requirement() -> None:
    affordability = next(c for c in standard_criteria() if c.key == "affordability")

    assert affordability.matched_source is None
    assert "price" in affordability.evidence_needed.lower()
