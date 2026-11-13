"""Tests for comparison matrix assembly (no database, no network)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from hospital_extractor.comparison.interpret import interpret_prompt
from hospital_extractor.comparison.schemas import (
    ComparisonColumn,
    ComparisonRequest,
    EvidenceItem,
    RegisteredHospital,
    SourceStatus,
)
from hospital_extractor.comparison.service import (
    ComparisonSubject,
    build_comparison,
    resolve_criteria,
)

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)

STATUSES = [SourceStatus(source="medcompare_server", status="active", detail="registry")]
ALL_STATUSES = [
    SourceStatus(source="medcompare_server", status="active", detail="registry"),
    SourceStatus(
        source="public_research",
        status="not_configured",
        detail="no search provider",
        required_integration="search provider client",
    ),
    SourceStatus(source="website_crawl", status="active", detail="crawl endpoint"),
]


def registered_hospital(facility_id: uuid.UUID | None = None) -> RegisteredHospital:
    return RegisteredHospital(facility_id=facility_id or uuid.uuid4())


def column(column_id: str, *, name: str, location: str | None = None) -> ComparisonColumn:
    return ComparisonColumn(
        column_id=column_id,
        kind="registered",
        name=name,
        location=location,
        organization_name="Northgate Health Group",
        website_url="https://northgate.example/",
        identity_note="Registered MedCompare facility",
    )


def location_evidence(
    value: str = "Sector 12, Northbridge, NB 411001",
    *,
    source: str = "medcompare_server",
) -> EvidenceItem:
    return EvidenceItem(
        criterion_key="location",
        value=value,
        source=source,  # type: ignore[arg-type]
        source_name="MedCompare registry",
        status="available",
        note="Stored identity record.",
    )


def standard_request(*hospitals: RegisteredHospital) -> ComparisonRequest:
    return ComparisonRequest(
        scope="standard_12",
        source="medcompare_server",
        hospitals=list(hospitals) or [registered_hospital(), registered_hospital()],
    )


def two_subjects() -> list[ComparisonSubject]:
    return [
        ComparisonSubject(
            column=column("a", name="Northgate General Hospital", location="Northbridge"),
            evidence=(location_evidence(),),
        ),
        ComparisonSubject(
            column=column("b", name="Southwind Medical Centre", location="Southmere"),
            evidence=(),
        ),
    ]


def test_standard_scope_produces_twelve_rows_for_two_hospitals() -> None:
    response = build_comparison(
        standard_request(), two_subjects(), source_statuses=STATUSES
    )

    assert response.scope == "standard_12"
    assert len(response.columns) == 2
    expected_keys = [criterion.key for criterion in resolve_criteria(standard_request())[0]]
    assert [row.criterion.key for row in response.rows] == expected_keys
    assert len(response.rows) == 12
    assert all(len(row.cells) == 2 for row in response.rows)


def test_location_evidence_is_reported_with_provenance() -> None:
    response = build_comparison(standard_request(), two_subjects(), source_statuses=STATUSES)
    location_row = next(row for row in response.rows if row.criterion.key == "location")

    populated, empty = location_row.cells
    assert populated.status == "available"
    assert populated.value == "Sector 12, Northbridge, NB 411001"
    assert populated.source == "medcompare_server"
    assert populated.source_name == "MedCompare registry"
    assert "Stored identity record." in populated.explanation

    assert empty.status == "unavailable"
    assert empty.value is None
    assert "not evidence that it lacks" in empty.explanation


def test_missing_evidence_is_never_rendered_as_no() -> None:
    response = build_comparison(standard_request(), two_subjects(), source_statuses=STATUSES)
    affordability = next(row for row in response.rows if row.criterion.key == "affordability")

    for cell in affordability.cells:
        assert cell.status == "unavailable"
        assert cell.value is None
        assert cell.confidence is None
        assert "No price" in cell.explanation or "not available" in cell.explanation.lower()


def test_response_carries_no_winner_or_score_field() -> None:
    response = build_comparison(standard_request(), two_subjects(), source_statuses=STATUSES)

    fields = set(type(response).model_fields)
    assert not fields & {"winner", "ranking", "score", "scores", "best_hospital"}
    assert "No winner" in response.disclaimer
    assert response.source_statuses == STATUSES


def test_standard_plus_custom_appends_custom_rows_and_warns_on_overlap() -> None:
    interpreted = interpret_prompt("compare parking and private rooms")
    request = ComparisonRequest(
        scope="standard_12_plus_custom",
        source="medcompare_server",
        criteria=interpreted.criteria,
        hospitals=[registered_hospital(), registered_hospital()],
    )

    response = build_comparison(request, two_subjects(), source_statuses=STATUSES)

    assert len(response.rows) == 14
    assert [row.criterion.origin for row in response.rows][:12] == ["standard"] * 12
    assert [row.criterion.origin for row in response.rows][12:] == ["custom", "custom"]
    assert response.warnings == []


def test_custom_only_scope_uses_only_the_interpreted_criteria() -> None:
    interpreted = interpret_prompt("compare parking availability and vegetarian meal options")
    request = ComparisonRequest(
        scope="custom_only",
        source="medcompare_server",
        criteria=interpreted.criteria,
        hospitals=[registered_hospital(), registered_hospital()],
    )

    response = build_comparison(request, two_subjects(), source_statuses=STATUSES)

    assert [row.criterion.label for row in response.rows] == [
        "Parking Availability",
        "Vegetarian Meal Options",
    ]
    for row in response.rows:
        for cell in row.cells:
            assert cell.status == "unavailable"
            assert "cannot be evaluated" in cell.explanation


def test_custom_only_without_criteria_is_rejected() -> None:
    request = ComparisonRequest(
        scope="custom_only",
        source="medcompare_server",
        criteria=[],
        hospitals=[registered_hospital(), registered_hospital()],
    )

    with pytest.raises(ValueError, match="custom_only"):
        resolve_criteria(request)


def test_conflicting_evidence_is_marked_conflicting() -> None:
    subjects = [
        ComparisonSubject(
            column=column("a", name="Northgate General Hospital"),
            evidence=(
                EvidenceItem(
                    criterion_key="treatment_facilities",
                    value="Total Knee Replacement",
                    source="website_crawl",
                    source_name="Hospital website (crawled)",
                    source_url="https://northgate.example/treatments",
                    retrieved_at=NOW,
                    status="conflicting",
                    note="The site states the service and also denies it elsewhere.",
                ),
            ),
        ),
        ComparisonSubject(column=column("b", name="Southwind Medical Centre"), evidence=()),
    ]
    request = standard_request()

    response = build_comparison(request, subjects, source_statuses=STATUSES)
    treatment_row = next(
        row for row in response.rows if row.criterion.key == "treatment_facilities"
    )

    conflicting, empty = treatment_row.cells
    assert conflicting.status == "conflicting"
    assert conflicting.source == "website_crawl"
    assert conflicting.source_url == "https://northgate.example/treatments"
    assert conflicting.retrieved_at == NOW
    assert "Conflicting information" in conflicting.explanation
    assert empty.status == "unavailable"


def test_outdated_evidence_is_reported_as_outdated() -> None:
    subjects = [
        ComparisonSubject(
            column=column("a", name="Northgate General Hospital"),
            evidence=(
                EvidenceItem(
                    criterion_key="location",
                    value="Old campus address",
                    source="public_research",
                    source_name="Example News",
                    source_url="https://news.example/hospital",
                    published_at=date(2019, 5, 1),
                    status="outdated",
                    note="Address last confirmed in 2019.",
                ),
            ),
        ),
        ComparisonSubject(column=column("b", name="Southwind Medical Centre"), evidence=()),
    ]

    response = build_comparison(
        standard_request(), subjects, source_statuses=ALL_STATUSES
    )
    location_row = next(row for row in response.rows if row.criterion.key == "location")

    outdated, empty = location_row.cells
    assert outdated.status == "outdated"
    assert outdated.published_at == date(2019, 5, 1)
    assert outdated.source_url == "https://news.example/hospital"
    assert empty.status == "unavailable"


def test_coverage_counts_only_criteria_with_evidence() -> None:
    response = build_comparison(standard_request(), two_subjects(), source_statuses=STATUSES)

    with_evidence, without_evidence = response.coverage
    assert with_evidence.criteria_total == 12
    assert with_evidence.criteria_with_evidence == 1
    assert with_evidence.coverage_ratio == round(1 / 12, 3)
    assert without_evidence.criteria_with_evidence == 0
    assert without_evidence.coverage_ratio == 0.0


def test_columns_keep_physical_facility_identity() -> None:
    response = build_comparison(standard_request(), two_subjects(), source_statuses=STATUSES)

    first, second = response.columns
    assert first.name != second.name
    assert first.organization_name == second.organization_name
    assert first.identity_note and second.identity_note
    assert first.column_id != second.column_id
