"""P23 data-quality & observability report tests."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from hospital_extractor.api.app import create_app
from hospital_extractor.cli import main
from hospital_extractor.models.contact import Contact
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.metadata import SourceMetadata, SourceType
from hospital_extractor.models.quality import DataQuality
from hospital_extractor.models.speciality import Speciality
from hospital_extractor.quality import (
    DatasetQualityReport,
    HospitalQualityNotFoundError,
    HospitalQualityReport,
    PipelineQualityReport,
    QualityDimension,
    ReportScope,
    SourceQualityReport,
    build_dataset_quality_report,
    build_hospital_quality_report,
    build_pipeline_quality_report,
    build_source_quality_reports,
    format_quality_json,
    format_quality_markdown,
    no_overall_score_note,
)
from hospital_extractor.storage import HospitalDatabase

_BASE = "https://quality.example"


def _record(source_url: str = _BASE, **kwargs: object) -> dict[str, object]:
    return {"source_url": source_url, **kwargs}


def _dataset(
    name: str,
    *,
    bed_count: int | None = None,
    city: str | None = None,
    specialities: list[str] | None = None,
    provider: str | None = None,
    quality: DataQuality | None = None,
) -> HospitalDataset:
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
        specialities=specs,
        source=source,
        quality=quality or DataQuality(),
    )


@pytest.fixture
def quality_db(tmp_path):
    db = HospitalDatabase(tmp_path / "quality.db")
    db.save_dataset(
        _dataset(
            "Alpha Hospital",
            bed_count=100,
            city="Pune",
            specialities=["Cardiology"],
            quality=DataQuality(
                pages_crawled=6,
                pages_with_information=4,
                records_extracted=10,
                records_rejected=1,
                duplicates_removed=2,
                warnings=["missing email"],
            ),
        )
    )
    db.save_dataset(
        _dataset(
            "Beta Hospital",
            bed_count=500,
            city="Delhi",
            specialities=["Oncology", "Cardiology"],
        )
    )
    db.save_dataset(_dataset("Gamma Hospital"))
    yield db
    db.close()


@pytest.fixture
def client(tmp_path):
    app = create_app()
    app.state.medcompare_db_path = tmp_path / "api-quality.db"
    with TestClient(app) as test_client:
        yield test_client


def _seed_client_db(path) -> str:
    db = HospitalDatabase(path)
    db.save_dataset(
        _dataset("Alpha Hospital", bed_count=100, city="Pune", specialities=["Cardiology"])
    )
    summaries = db.list_datasets()
    hospital_id = summaries[0].hospital_id
    db.close()
    return hospital_id


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestQualityModels:
    def test_no_overall_score_on_all_scopes(self, quality_db):
        summaries = quality_db.list_datasets()
        dataset_report = build_dataset_quality_report(quality_db, summaries[0])
        assert dataset_report.overall_score is None
        assert dataset_report.note == no_overall_score_note
        assert dataset_report.scope == ReportScope.DATASET

        hospital_report = build_hospital_quality_report(
            quality_db, summaries[0].hospital_id
        )
        assert hospital_report.overall_score is None
        assert hospital_report.scope == ReportScope.HOSPITAL

        pipeline = build_pipeline_quality_report(quality_db)
        assert pipeline.overall_score is None
        assert pipeline.scope == ReportScope.PIPELINE

        sources = build_source_quality_reports(quality_db, summaries[0].hospital_id)
        assert sources
        for source in sources:
            assert source.overall_score is None
            assert source.scope == ReportScope.SOURCE

    def test_metric_lookup(self, quality_db):
        pipeline = build_pipeline_quality_report(quality_db)
        metric = pipeline.metric("dataset_count")
        assert metric is not None
        assert metric.value == 3
        assert metric.definition
        assert pipeline.metric_value("dataset_count") == 3
        assert pipeline.metric("does_not_exist") is None
        assert pipeline.metric_value("does_not_exist", default=9) == 9

    def test_metrics_have_dimensions_and_definitions(self, quality_db):
        summaries = quality_db.list_datasets()
        report = build_dataset_quality_report(quality_db, summaries[0])
        assert report.metrics
        for metric in report.metrics:
            assert isinstance(metric.dimension, QualityDimension)
            assert metric.definition
            assert metric.name

    def test_no_unknown_score_fields_in_dump(self, quality_db):
        pipeline = build_pipeline_quality_report(quality_db)
        payload = json.loads(format_quality_json(pipeline))
        assert payload["overall_score"] is None
        assert "score" not in payload or payload.get("score") is None


# ---------------------------------------------------------------------------
# Dataset quality metrics
# ---------------------------------------------------------------------------


class TestDatasetQualityReport:
    def test_counts_from_data_quality_block(self, quality_db):
        summaries = quality_db.list_datasets()
        alpha = next(s for s in summaries if s.hospital_name == "Alpha Hospital")
        report = build_dataset_quality_report(quality_db, alpha)
        assert report.metric_value("records_extracted") == 10
        assert report.metric_value("records_rejected") == 1
        assert report.metric_value("duplicates_removed") == 2
        assert report.metric_value("pages_crawled") == 6
        assert report.metric_value("pages_with_information") == 4
        assert report.metric_value("crawl_success_rate") == pytest.approx(4 / 6, rel=1e-6)
        assert report.metric_value("extraction_warning_count") == 1

    def test_field_coverage_and_unknown_rate(self, quality_db):
        summaries = quality_db.list_datasets()
        gamma = next(s for s in summaries if s.hospital_name == "Gamma Hospital")
        report = build_dataset_quality_report(quality_db, gamma)
        # Gamma has only name + hospital_type-like fields sparse.
        coverage = report.metric_value("hospital_field_coverage_rate")
        assert coverage is not None
        assert 0.0 <= coverage <= 1.0
        unknown_rate = report.metric_value("unknown_field_rate")
        assert unknown_rate is not None
        assert 0.0 <= unknown_rate <= 1.0
        # Alpha has more fields populated than Gamma.
        alpha = next(s for s in summaries if s.hospital_name == "Alpha Hospital")
        alpha_report = build_dataset_quality_report(quality_db, alpha)
        assert (
            alpha_report.metric_value("hospital_field_coverage_rate") >= coverage
        )

    def test_duplicate_and_conflict_rates(self, quality_db):
        summaries = quality_db.list_datasets()
        alpha = next(s for s in summaries if s.hospital_name == "Alpha Hospital")
        report = build_dataset_quality_report(quality_db, alpha)
        # 2 duplicates / (10 + 2) ≈ 0.166667 (metrics round to 6 dp).
        assert report.metric_value("duplicate_rate") == pytest.approx(0.166667, abs=1e-5)
        assert report.metric_value("conflicts_detected") == 0
        assert report.metric_value("conflict_rate") == 0.0

    def test_provenance_metrics(self, quality_db):
        summaries = quality_db.list_datasets()
        alpha = next(s for s in summaries if s.hospital_name == "Alpha Hospital")
        report = build_dataset_quality_report(quality_db, alpha)
        assert report.metric_value("provenance_dataset_source") is True
        assert report.metric_value("provenance_hospital_source_url") is True
        assert report.metric_value("entity_records_total", 0) >= 1
        assert report.metric_value("entity_records_with_source_url", 0) >= 1
        rate = report.metric_value("entity_provenance_rate")
        assert rate is not None
        assert 0.0 <= rate <= 1.0

    def test_freshness_non_negative(self, quality_db):
        summaries = quality_db.list_datasets()
        report = build_dataset_quality_report(quality_db, summaries[0])
        age = report.metric_value("freshness_age_days")
        assert age is not None
        assert age >= 0

    def test_empty_store_crawl_rate_is_null(self, quality_db):
        from datetime import UTC, datetime

        from hospital_extractor.storage import DatasetSummary

        empty = HospitalDataset()
        summary = DatasetSummary(
            dataset_id="ds_empty",
            hospital_ref="https://empty.example",
            hospital_name=None,
            hospital_key="empty",
            hospital_id="h_empty",
            source_type="website",
            provider=None,
            created_at=datetime.now(UTC),
            record_count=0,
            quality_records=0,
        )
        report = build_dataset_quality_report(quality_db, summary, empty)
        assert report.metric_value("crawl_success_rate") is None
        assert report.metric_value("records_extracted") == 0
        assert report.overall_score is None


# ---------------------------------------------------------------------------
# Source quality
# ---------------------------------------------------------------------------


class TestSourceQualityReport:
    def test_one_report_per_source(self, quality_db):
        summaries = quality_db.list_datasets()
        hospital_id = summaries[0].hospital_id
        reports = build_source_quality_reports(quality_db, hospital_id)
        assert len(reports) >= 1
        source_types = {r.source_type for r in reports}
        assert "website" in source_types
        for report in reports:
            assert report.dataset_count >= 1
            assert report.metric_value("dataset_count") == report.dataset_count
            assert report.overall_score is None

    def test_unknown_hospital_raises(self, quality_db):
        with pytest.raises(HospitalQualityNotFoundError):
            build_source_quality_reports(quality_db, "h_missing")


# ---------------------------------------------------------------------------
# Hospital quality
# ---------------------------------------------------------------------------


class TestHospitalQualityReport:
    def test_aggregates_datasets_and_sources(self, quality_db):
        summaries = quality_db.list_datasets()
        alpha = next(s for s in summaries if s.hospital_name == "Alpha Hospital")
        hospital_id = alpha.hospital_id
        report = build_hospital_quality_report(quality_db, hospital_id)
        assert report.hospital_id == hospital_id
        assert report.dataset_count >= 1
        assert report.source_count >= 1
        assert len(report.dataset_reports) == report.dataset_count
        assert len(report.source_reports) == report.source_count
        assert report.metric_value("dataset_count") == report.dataset_count
        assert report.metric_value("records_extracted_sum", 0) >= 10
        assert report.overall_score is None
        # Pipeline counters present even when empty.
        assert report.metric_value("snapshot_count") == 0
        assert report.metric_value("change_set_count") == 0
        assert report.metric_value("verification_job_count") == 0

    def test_unknown_hospital_raises(self, quality_db):
        with pytest.raises(HospitalQualityNotFoundError):
            build_hospital_quality_report(quality_db, "h_missing")


# ---------------------------------------------------------------------------
# Pipeline quality
# ---------------------------------------------------------------------------


class TestPipelineQualityReport:
    def test_counts_datasets_and_hospitals(self, quality_db):
        report = build_pipeline_quality_report(quality_db)
        assert report.dataset_count == 3
        # Three distinct hospital identities (unique base URLs).
        assert report.hospital_count == 3
        assert report.metric_value("hospital_count") == 3
        assert report.metric_value("dataset_count") == 3
        assert report.metric_value("records_extracted_sum") == 10
        assert report.overall_score is None

    def test_empty_store(self, tmp_path):
        db = HospitalDatabase(tmp_path / "empty-quality.db")
        try:
            report = build_pipeline_quality_report(db)
            assert report.dataset_count == 0
            assert report.hospital_count == 0
            assert report.metric_value("average_hospital_field_coverage_rate") is None
            assert report.metric_value("newest_freshness_age_days") is None
            assert report.overall_score is None
        finally:
            db.close()

    def test_snapshot_and_pipeline_counters_start_zero(self, quality_db):
        report = build_pipeline_quality_report(quality_db)
        assert report.snapshot_count == 0
        assert report.change_set_count == 0
        assert report.verification_job_count == 0
        assert report.reconciliation_case_count == 0
        assert report.monitoring_policy_count == 0
        assert report.report_count == 0
        assert report.orchestration_run_count == 0


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


class TestQualityFormatters:
    def test_json_roundtrip(self, quality_db):
        pipeline = build_pipeline_quality_report(quality_db)
        text = format_quality_json(pipeline)
        payload = json.loads(text)
        assert payload["scope"] == "pipeline"
        assert payload["overall_score"] is None
        assert isinstance(payload["metrics"], list)
        assert payload["metrics"]
        assert payload["note"] == no_overall_score_note

    def test_markdown_contains_metrics_and_disclaimer(self, quality_db):
        summaries = quality_db.list_datasets()
        alpha = next(s for s in summaries if s.hospital_name == "Alpha Hospital")
        report = build_hospital_quality_report(quality_db, alpha.hospital_id)
        text = format_quality_markdown(report)
        assert text.startswith("# Quality report")
        assert "Overall score:** not computed" in text
        assert no_overall_score_note in text
        assert "| Metric |" in text
        assert "`dataset_count`" in text

    def test_markdown_dataset_scope(self, quality_db):
        summaries = quality_db.list_datasets()
        alpha = next(s for s in summaries if s.hospital_name == "Alpha Hospital")
        report = build_dataset_quality_report(quality_db, alpha)
        text = format_quality_markdown(report)
        assert "Dataset `" in text
        assert "`records_extracted`" in text

    def test_markdown_pipeline_scope(self, quality_db):
        report = build_pipeline_quality_report(quality_db)
        text = format_quality_markdown(report)
        assert "Pipeline (whole store)" in text
        assert "**Hospitals:** 3" in text


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class TestQualityAPI:
    def test_pipeline_json(self, client, tmp_path):
        _seed_client_db(client.app.state.medcompare_db_path)
        response = client.get("/api/v1/quality/pipeline")
        assert response.status_code == 200
        body = response.json()
        assert body["report"]["scope"] == "pipeline"
        assert body["report"]["overall_score"] is None
        assert body["report"]["dataset_count"] == 1

    def test_hospital_json_and_markdown(self, client, tmp_path):
        hospital_id = _seed_client_db(client.app.state.medcompare_db_path)
        response = client.get(f"/api/v1/quality/hospitals/{hospital_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["report"]["hospital_id"] == hospital_id
        assert body["report"]["overall_score"] is None

        md = client.get(f"/api/v1/quality/hospitals/{hospital_id}?format=markdown")
        assert md.status_code == 200
        assert md.headers["content-type"].startswith("text/markdown")
        assert "Overall score:** not computed" in md.text

    def test_hospital_not_found(self, client):
        response = client.get("/api/v1/quality/hospitals/h_missing")
        assert response.status_code == 404
        assert response.json()["error"] == "Hospital not found"

    def test_sources_endpoint(self, client, tmp_path):
        hospital_id = _seed_client_db(client.app.state.medcompare_db_path)
        response = client.get(f"/api/v1/quality/hospitals/{hospital_id}/sources")
        assert response.status_code == 200
        body = response.json()
        assert body["reports"]
        assert body["reports"][0]["source_type"] == "website"
        assert body["reports"][0]["overall_score"] is None

    def test_dataset_json(self, client, tmp_path):
        from hospital_extractor.storage import HospitalDatabase

        _seed_client_db(client.app.state.medcompare_db_path)
        db = HospitalDatabase(client.app.state.medcompare_db_path)
        summaries = db.list_datasets()
        dataset_id = summaries[0].dataset_id
        db.close()

        response = client.get(f"/api/v1/quality/datasets/{dataset_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["report"]["dataset_id"] == dataset_id
        assert body["report"]["overall_score"] is None

        missing = client.get("/api/v1/quality/datasets/ds_missing")
        assert missing.status_code == 404

    def test_quality_index_page(self, client, tmp_path):
        _seed_client_db(client.app.state.medcompare_db_path)
        response = client.get("/quality")
        assert response.status_code == 200
        assert "Quality" in response.text
        assert "no overall score" in response.text.lower() or "not computed" in response.text

    def test_format_endpoint_markdown_and_json(self, client, tmp_path):
        _seed_client_db(client.app.state.medcompare_db_path)
        md = client.get("/api/v1/quality/format/pipeline?format=markdown")
        assert md.status_code == 200
        assert md.text.startswith("# Quality report")

        js = client.get("/api/v1/quality/format/pipeline?format=json")
        assert js.status_code == 200
        payload = json.loads(js.text)
        assert payload["overall_score"] is None

        bad = client.get("/api/v1/quality/format/nope")
        assert bad.status_code == 400

        missing_id = client.get("/api/v1/quality/format/hospital")
        assert missing_id.status_code == 400

        not_found = client.get("/api/v1/quality/format/hospital?id=h_missing")
        assert not_found.status_code == 404

    def test_profile_page_includes_quality_panel(self, client, tmp_path):
        _seed_client_db(client.app.state.medcompare_db_path)
        from hospital_extractor.storage import HospitalDatabase

        db = HospitalDatabase(client.app.state.medcompare_db_path)
        dataset_id = db.list_datasets()[0].dataset_id
        db.close()

        response = client.get(f"/hospitals/{dataset_id}")
        assert response.status_code == 200
        assert "Quality metrics (P23)" in response.text
        assert "hospital_field_coverage_rate" in response.text
        assert "not computed" in response.text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestQualityCLI:
    def test_pipeline_json(self, quality_db, capsys):
        path = str(quality_db._connection.execute("PRAGMA database_list").fetchone()[2])
        code = main(["quality-report", "--scope", "pipeline", "--format", "json", "--db", path])
        assert code == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["scope"] == "pipeline"
        assert payload["overall_score"] is None

    def test_hospital_markdown(self, quality_db, capsys):
        path = str(quality_db._connection.execute("PRAGMA database_list").fetchone()[2])
        hospital_id = quality_db.list_datasets()[0].hospital_id
        code = main(
            [
                "quality-report",
                "--scope",
                "hospital",
                "--id",
                hospital_id,
                "--format",
                "markdown",
                "--db",
                path,
            ]
        )
        assert code == 0
        out = capsys.readouterr().out
        assert out.startswith("# Quality report")
        assert "Overall score:** not computed" in out

    def test_hospital_requires_id(self, quality_db, capsys):
        path = str(quality_db._connection.execute("PRAGMA database_list").fetchone()[2])
        code = main(["quality-report", "--scope", "hospital", "--db", path])
        assert code == 1
        err = capsys.readouterr().err
        assert "--id is required" in err

    def test_unknown_hospital(self, quality_db, capsys):
        path = str(quality_db._connection.execute("PRAGMA database_list").fetchone()[2])
        code = main(
            ["quality-report", "--scope", "hospital", "--id", "h_missing", "--db", path]
        )
        assert code == 1
        assert "no stored datasets" in capsys.readouterr().err

    def test_dataset_scope(self, quality_db, capsys):
        path = str(quality_db._connection.execute("PRAGMA database_list").fetchone()[2])
        dataset_id = quality_db.list_datasets()[0].dataset_id
        code = main(
            [
                "quality-report",
                "--scope",
                "dataset",
                "--id",
                dataset_id,
                "--format",
                "json",
                "--db",
                path,
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["scope"] == "dataset"
        assert payload["dataset_id"] == dataset_id

    def test_unknown_dataset(self, quality_db, capsys):
        path = str(quality_db._connection.execute("PRAGMA database_list").fetchone()[2])
        code = main(
            ["quality-report", "--scope", "dataset", "--id", "ds_missing", "--db", path]
        )
        assert code == 1
        assert "unknown dataset" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Regression guards
# ---------------------------------------------------------------------------


class TestQualityRegression:
    def test_import_from_package_root(self):
        import hospital_extractor.quality as q

        assert hasattr(q, "build_pipeline_quality_report")
        assert hasattr(q, "format_quality_markdown")
        assert "overall_score" in HospitalQualityReport.model_fields
        assert "overall_score" in DatasetQualityReport.model_fields
        assert "overall_score" in SourceQualityReport.model_fields
        assert "overall_score" in PipelineQualityReport.model_fields

    def test_reports_are_pydantic_models(self):
        for cls in (
            HospitalQualityReport,
            DatasetQualityReport,
            SourceQualityReport,
            PipelineQualityReport,
        ):
            assert hasattr(cls, "model_validate")
            assert cls.model_fields["overall_score"].default is None
