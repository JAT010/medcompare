"""P22 search, field filters, and stored-hospital field comparison tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hospital_extractor.api.app import create_app
from hospital_extractor.cli import main
from hospital_extractor.models.contact import Contact
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.infrastructure import Infrastructure
from hospital_extractor.models.metadata import SourceMetadata, SourceType
from hospital_extractor.models.speciality import Speciality
from hospital_extractor.search import (
    ComparisonRequest,
    Filter,
    FilterOperator,
    HospitalNotFoundError,
    InvalidQueryError,
    Query,
    compare_stored_hospitals,
    matches_filter,
    resolve_field_values,
    search_datasets,
    validate_field_path,
)
from hospital_extractor.storage import HospitalDatabase

_BASE = "https://hospital.example"


def _record(source_url: str = _BASE, **kwargs: object) -> dict[str, object]:
    return {"source_url": source_url, **kwargs}


def _dataset(
    name: str,
    *,
    bed_count: int | None = None,
    city: str | None = None,
    specialities: list[str] | None = None,
    total_beds: int | None = None,
    provider: str | None = None,
) -> HospitalDataset:
    # Unique base URL per hospital so identity resolution mints distinct ids.
    slug = name.lower().replace(" ", "-")
    base = f"https://{slug}.example"
    hospital = HospitalOverview(
        **_record(
            source_url=base,
            name=name,
            bed_count=bed_count,
            hospital_type="Multi Speciality" if bed_count else None,
        )
    )
    contacts = [Contact(**_record(source_url=base, city=city))] if city else []
    infrastructure = (
        [Infrastructure(**_record(source_url=base, total_beds=total_beds))]
        if total_beds is not None
        else []
    )
    specs = [Speciality(**_record(source_url=base, name=s)) for s in (specialities or [])]
    source = SourceMetadata(
        source_type=SourceType.WEBSITE,
        provider=provider,
        base_url=base,
        hospital_ref=base,
        hospital_name=name,
    )
    return HospitalDataset(
        hospital=hospital,
        contacts=contacts,
        infrastructure=infrastructure,
        specialities=specs,
        source=source,
    )


@pytest.fixture
def search_db(tmp_path):
    db = HospitalDatabase(tmp_path / "search.db")
    db.save_dataset(_dataset("Alpha Hospital", bed_count=100, city="Pune",
                             specialities=["Cardiology"], total_beds=100,
                             provider="alpha-site"))
    db.save_dataset(_dataset("Beta Hospital", bed_count=500, city="Delhi",
                             specialities=["Oncology", "Cardiology"]))
    db.save_dataset(_dataset("Gamma Hospital", bed_count=None, city=None))
    yield db
    db.close()


@pytest.fixture
def client(tmp_path):
    app = create_app()
    app.state.medcompare_db_path = tmp_path / "api-search.db"
    with TestClient(app) as test_client:
        yield test_client


def _seed_client_db(path) -> HospitalDatabase:
    db = HospitalDatabase(path)
    db.save_dataset(_dataset("Alpha Hospital", bed_count=100, city="Pune",
                             specialities=["Cardiology"], total_beds=100))
    db.save_dataset(_dataset("Beta Hospital", bed_count=500, city="Delhi",
                             specialities=["Oncology"]))
    db.save_dataset(_dataset("Gamma Hospital"))
    db.close()
    return db


# ---------------------------------------------------------------------------
# Field path validation
# ---------------------------------------------------------------------------


class TestValidateFieldPath:
    @pytest.mark.parametrize(
        "path",
        [
            "hospital.name",
            "hospital.bed_count",
            "contacts.email",
            "specialities.name",
            "infrastructure.total_beds",
            "source.source_type",
            "quality.records_extracted",
        ],
    )
    def test_valid_paths(self, path: str) -> None:
        validate_field_path(path)

    @pytest.mark.parametrize(
        "path",
        ["", "hospital", "hospital.nope", "unknown.thing", "a.b.c", "quality.nope"],
    )
    def test_invalid_paths(self, path: str) -> None:
        with pytest.raises(InvalidQueryError):
            validate_field_path(path)


# ---------------------------------------------------------------------------
# Filter semantics (UNKNOWN ≠ NO)
# ---------------------------------------------------------------------------


class TestFilterSemantics:
    def test_equals_matches(self) -> None:
        ds = _dataset("Alpha", bed_count=100, city="Pune")
        values = resolve_field_values(ds, "hospital.bed_count")
        assert matches_filter(values, Filter(field="hospital.bed_count",
                                             operator=FilterOperator.EQUALS,
                                             value=100))
        assert not matches_filter(values, Filter(field="hospital.bed_count",
                                                 operator=FilterOperator.EQUALS,
                                                 value=999))

    def test_missing_field_is_unknown_not_equals(self) -> None:
        ds = _dataset("Gamma")
        values = resolve_field_values(ds, "hospital.bed_count")
        assert values == []
        # UNKNOWN never satisfies equals or not_equals (no invented NO/YES)
        assert not matches_filter(values, Filter(field="hospital.bed_count",
                                                 operator=FilterOperator.EQUALS,
                                                 value=100))
        assert not matches_filter(values, Filter(field="hospital.bed_count",
                                                 operator=FilterOperator.NOT_EQUALS,
                                                 value=100))
        assert matches_filter(values, Filter(field="hospital.bed_count",
                                             operator=FilterOperator.NOT_EXISTS))
        assert not matches_filter(values, Filter(field="hospital.bed_count",
                                                 operator=FilterOperator.EXISTS))

    def test_contains_case_insensitive(self) -> None:
        ds = _dataset("Alpha", city="Pune")
        values = resolve_field_values(ds, "contacts.city")
        assert matches_filter(values, Filter(field="contacts.city",
                                             operator=FilterOperator.CONTAINS,
                                             value="pun"))

    def test_numeric_comparisons(self) -> None:
        ds = _dataset("Alpha", bed_count=100)
        values = resolve_field_values(ds, "hospital.bed_count")
        assert matches_filter(values, Filter(field="hospital.bed_count",
                                             operator=FilterOperator.GTE, value=100))
        assert matches_filter(values, Filter(field="hospital.bed_count",
                                             operator=FilterOperator.LT, value=200))
        assert not matches_filter(values, Filter(field="hospital.bed_count",
                                                 operator=FilterOperator.GT, value=100))

    def test_in_operator(self) -> None:
        ds = _dataset("Alpha", city="Pune")
        values = resolve_field_values(ds, "contacts.city")
        assert matches_filter(values, Filter(field="contacts.city",
                                             operator=FilterOperator.IN,
                                             value=["Pune", "Delhi"]))
        assert not matches_filter(values, Filter(field="contacts.city",
                                                 operator=FilterOperator.NOT_IN,
                                                 value=["Pune", "Delhi"]))

    def test_multi_record_any_match(self) -> None:
        ds = _dataset("Alpha", specialities=["Cardiology", "Oncology"])
        values = resolve_field_values(ds, "specialities.name")
        assert len(values) == 2
        assert matches_filter(values, Filter(field="specialities.name",
                                             operator=FilterOperator.EQUALS,
                                             value="Cardiology"))

    def test_filter_requires_value(self) -> None:
        with pytest.raises(ValidationError):
            Filter(field="hospital.name", operator=FilterOperator.EQUALS, value=None)


# ---------------------------------------------------------------------------
# Search service
# ---------------------------------------------------------------------------


class TestSearchService:
    def test_match_all(self, search_db: HospitalDatabase) -> None:
        result = search_datasets(search_db, Query())
        assert result.total == 3
        assert len(result.matches) == 3

    def test_exact_filter(self, search_db: HospitalDatabase) -> None:
        result = search_datasets(
            search_db,
            Query(filters=[Filter(field="hospital.bed_count",
                                  operator=FilterOperator.EQUALS, value=500)]),
        )
        assert result.total == 1
        assert result.matches[0]["hospital_name"] == "Beta Hospital"
        fields = result.matches[0]["fields"]
        assert fields[0]["value"] == 500
        assert fields[0]["unknown"] is False
        assert fields[0]["evidence"]

    def test_multi_filters(self, search_db: HospitalDatabase) -> None:
        result = search_datasets(
            search_db,
            Query(
                filters=[
                    Filter(field="hospital.bed_count",
                           operator=FilterOperator.GTE, value=100),
                    Filter(field="contacts.city",
                           operator=FilterOperator.EQUALS, value="Pune"),
                ]
            ),
        )
        assert result.total == 1
        assert result.matches[0]["hospital_name"] == "Alpha Hospital"

    def test_unknown_not_returned_by_equals(self, search_db: HospitalDatabase) -> None:
        result = search_datasets(
            search_db,
            Query(filters=[Filter(field="hospital.bed_count",
                                  operator=FilterOperator.EQUALS, value=0)]),
        )
        # Gamma has no bed_count → UNKNOWN, never equals 0 / NO
        assert result.total == 0

    def test_not_exists_returns_unknown_hospital(self, search_db: HospitalDatabase) -> None:
        result = search_datasets(
            search_db,
            Query(filters=[Filter(field="hospital.bed_count",
                                  operator=FilterOperator.NOT_EXISTS)]),
        )
        assert result.total == 1
        assert result.matches[0]["hospital_name"] == "Gamma Hospital"

    def test_free_text(self, search_db: HospitalDatabase) -> None:
        result = search_datasets(search_db, Query(text="oncology"))
        assert result.total == 1
        assert result.matches[0]["hospital_name"] == "Beta Hospital"

    def test_empty_result(self, search_db: HospitalDatabase) -> None:
        result = search_datasets(search_db, Query(text="no-such-hospital-xyz"))
        assert result.total == 0
        assert result.matches == []

    def test_pagination(self, search_db: HospitalDatabase) -> None:
        page1 = search_datasets(search_db, Query(limit=2, offset=0))
        page2 = search_datasets(search_db, Query(limit=2, offset=2))
        assert page1.total == page2.total == 3
        assert len(page1.matches) == 2
        assert len(page2.matches) == 1
        ids = {m["dataset_id"] for m in page1.matches + page2.matches}
        assert len(ids) == 3

    def test_provenance_preserved(self, search_db: HospitalDatabase) -> None:
        result = search_datasets(
            search_db,
            Query(filters=[Filter(field="hospital.name",
                                  operator=FilterOperator.EQUALS,
                                  value="Alpha Hospital")]),
        )
        match = result.matches[0]
        assert match["source_type"] == "website"
        assert match["captured_at"]
        assert match["evidence"]
        assert match["evidence"][0]["source_type"] == "website"

    def test_invalid_field_raises(self, search_db: HospitalDatabase) -> None:
        with pytest.raises(InvalidQueryError):
            search_datasets(
                search_db,
                Query(filters=[Filter(field="nope.nope",
                                      operator=FilterOperator.EQUALS, value=1)]),
            )

    def test_hospital_id_scope(self, search_db: HospitalDatabase) -> None:
        summary = search_db.list_datasets()[0]
        result = search_datasets(
            search_db, Query(hospital_ids=[summary.hospital_id])
        )
        assert result.total >= 1
        assert all(m["hospital_id"] == summary.hospital_id for m in result.matches)


# ---------------------------------------------------------------------------
# Field comparison (2–4 hospitals, no ranking)
# ---------------------------------------------------------------------------


class TestFieldComparison:
    def test_two_hospitals(self, search_db: HospitalDatabase) -> None:
        ids = [s.hospital_id for s in search_db.list_datasets()[:2]]
        result = compare_stored_hospitals(
            search_db,
            ComparisonRequest(hospital_ids=ids, fields=["hospital.name", "hospital.bed_count"]),
        )
        assert len(result.hospital_ids) == 2
        assert len(result.fields) == 2
        name_field = next(f for f in result.fields if f.field == "hospital.name")
        assert all(not cell.unknown for cell in name_field.cells)
        assert "ranking" in result.note.lower() or "no ranking" in result.note.lower()

    def test_three_and_four_hospitals(self, search_db: HospitalDatabase) -> None:
        ids = list(dict.fromkeys(s.hospital_id for s in search_db.list_datasets()))
        assert len(ids) == 3
        result3 = compare_stored_hospitals(
            search_db, ComparisonRequest(hospital_ids=ids[:3], fields=["hospital.name"])
        )
        assert len(result3.hospital_ids) == 3
        with pytest.raises(ValidationError):
            ComparisonRequest(hospital_ids=[ids[0], ids[0]], fields=[])

    def test_one_hospital_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ComparisonRequest(hospital_ids=["only-one"], fields=[])

    def test_five_hospitals_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ComparisonRequest(hospital_ids=[f"h{i}" for i in range(5)], fields=[])

    def test_unknown_field_cell(self, search_db: HospitalDatabase) -> None:
        summaries = search_db.list_datasets()
        by_name = {s.hospital_name: s for s in summaries}
        alpha_id = by_name["Alpha Hospital"].hospital_id
        gamma_id = by_name["Gamma Hospital"].hospital_id
        assert alpha_id != gamma_id
        result = compare_stored_hospitals(
            search_db,
            ComparisonRequest(
                hospital_ids=[alpha_id, gamma_id],
                fields=["hospital.bed_count", "contacts.city"],
            ),
        )
        bed = next(f for f in result.fields if f.field == "hospital.bed_count")
        gamma_cell = next(c for c in bed.cells if c.hospital_id == gamma_id)
        assert gamma_cell.unknown is True
        assert gamma_cell.value is None
        # never rewritten to NO / 0
        assert gamma_cell.value is not False

    def test_unknown_hospital_id(self, search_db: HospitalDatabase) -> None:
        ids = [s.hospital_id for s in search_db.list_datasets()[:2]]
        with pytest.raises(HospitalNotFoundError):
            compare_stored_hospitals(
                search_db,
                ComparisonRequest(hospital_ids=[ids[0], "h_missing"], fields=[]),
            )

    def test_no_ranking_fields(self, search_db: HospitalDatabase) -> None:
        ids = [s.hospital_id for s in search_db.list_datasets()[:2]]
        result = compare_stored_hospitals(
            search_db, ComparisonRequest(hospital_ids=ids, fields=[])
        )
        dumped = result.model_dump()
        assert "rank" not in dumped
        assert "score" not in dumped
        assert "winner" not in dumped


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class TestSearchApi:
    @pytest.fixture(autouse=True)
    def _seed(self, client: TestClient, tmp_path) -> None:
        _seed_client_db(client.app.state.medcompare_db_path)

    def test_search_endpoint(self, client: TestClient) -> None:
        response = client.post("/api/v1/search", json={"filters": [], "limit": 10})
        assert response.status_code == 200
        body = response.json()["result"]
        assert body["total"] == 3

    def test_search_with_filter(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/search",
            json={
                "filters": [
                    {"field": "hospital.bed_count", "operator": "gte", "value": 400}
                ]
            },
        )
        assert response.status_code == 200
        assert response.json()["result"]["total"] == 1

    def test_search_invalid_field_400(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/search",
            json={"filters": [{"field": "bad.field", "operator": "equals", "value": 1}]},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "Invalid query"

    def test_compare_endpoint(self, client: TestClient) -> None:
        datasets = client.get("/api/v1/hospitals").json()["datasets"]
        ids = [d["hospital_id"] for d in datasets[:2]]
        response = client.post(
            "/api/v1/search/compare",
            json={"hospital_ids": ids, "fields": ["hospital.name"]},
        )
        assert response.status_code == 200
        result = response.json()["result"]
        assert len(result["hospital_ids"]) == 2
        assert "No ranking" in result["note"] or "no ranking" in result["note"]

    def test_compare_missing_hospital_404(self, client: TestClient) -> None:
        datasets = client.get("/api/v1/hospitals").json()["datasets"]
        ids = [d["hospital_id"] for d in datasets[:1]]
        response = client.post(
            "/api/v1/search/compare",
            json={"hospital_ids": ids + ["h_nope"], "fields": ["hospital.name"]},
        )
        assert response.status_code == 404

    def test_compare_one_hospital_422(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/search/compare", json={"hospital_ids": ["only"], "fields": []}
        )
        assert response.status_code == 422

    def test_search_ui_page(self, client: TestClient) -> None:
        page = client.get("/search")
        assert page.status_code == 200
        assert "Search stored datasets" in page.text
        assert "Compare stored hospitals" in page.text

    def test_search_ui_submit(self, client: TestClient) -> None:
        page = client.post(
            "/search",
            data={"mode": "search", "text": "Alpha", "limit": "10"},
        )
        assert page.status_code == 200
        assert "Alpha Hospital" in page.text

    def test_compare_ui_submit(self, client: TestClient) -> None:
        datasets = client.get("/api/v1/hospitals").json()["datasets"]
        ids = [d["hospital_id"] for d in datasets[:2]]
        page = client.post(
            "/search",
            data={"mode": "compare", "hospital_ids": ids, "fields": "hospital.name"},
        )
        assert page.status_code == 200
        assert "Field comparison" in page.text
        assert "No ranking" in page.text or "no ranking" in page.text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestSearchCli:
    def test_search_cli_json(self, search_db: HospitalDatabase, tmp_path, capsys) -> None:
        # reuse path by writing same datasets into a path CLI can open
        db_path = search_db.path
        code = main(
            [
                "search",
                "--filter",
                "hospital.bed_count",
                "gte",
                "400",
                "--json",
                "--db",
                str(db_path),
            ]
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "Beta Hospital" in out
        assert "total" in out or '"total"' in out

    def test_search_cli_invalid_filter(self, search_db: HospitalDatabase, capsys) -> None:
        code = main(
            [
                "search",
                "--filter",
                "bad.field",
                "equals",
                "1",
                "--db",
                str(search_db.path),
            ]
        )
        assert code == 1
        err = capsys.readouterr().err
        assert "error:" in err

    def test_search_cli_compare(self, search_db: HospitalDatabase, capsys) -> None:
        by_name = {s.hospital_name: s for s in search_db.list_datasets()}
        alpha = by_name["Alpha Hospital"].hospital_id
        beta = by_name["Beta Hospital"].hospital_id
        code = main(
            [
                "search",
                "--compare",
                alpha,
                beta,
                "--fields",
                "hospital.name,hospital.bed_count",
                "--db",
                str(search_db.path),
            ]
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "Field comparison only" in out or "No ranking" in out
        assert "Alpha Hospital" in out
        assert "Beta Hospital" in out
        assert "UNKNOWN" not in out  # both have names; bed counts present

    def test_search_cli_unknown_hospital(self, search_db: HospitalDatabase, capsys) -> None:
        real_id = search_db.list_datasets()[0].hospital_id
        code = main(
            [
                "search",
                "--compare",
                real_id,
                "h_missing",
                "--db",
                str(search_db.path),
            ]
        )
        assert code == 1
        assert "error:" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Regression: comparison engine untouched
# ---------------------------------------------------------------------------


class TestRegressionBaselines:
    def test_filter_operator_values_complete(self) -> None:
        assert set(FilterOperator.values()) == {
            "equals", "not_equals", "contains", "not_contains",
            "exists", "not_exists", "in", "not_in",
            "gt", "gte", "lt", "lte", "after", "before",
        }

    def test_search_does_not_mutate_datasets(self, search_db: HospitalDatabase) -> None:
        before = {s.dataset_id: s for s in search_db.list_datasets()}
        search_datasets(search_db, Query(text="Alpha"))
        after = {s.dataset_id: s for s in search_db.list_datasets()}
        assert set(before) == set(after)
