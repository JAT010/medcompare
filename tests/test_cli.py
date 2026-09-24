"""CLI regression tests for the non-empty comparison path.

The `hospital-extractor compare` table path previously crashed on any
non-empty result because `cli.py` accessed a non-existent
``ComparisonTarget.synonym`` attribute. These tests pin the fixed behaviour:
a non-empty comparison must render a table with YES/NO/UNKNOWN-compatible
decisions and a ``--json`` path must emit valid JSON. No live HTTP is used.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from hospital_extractor.cli import _print_table, _run_compare
from hospital_extractor.comparison.models import BooleanDecision, ComparisonResult
from hospital_extractor.comparison.service import compare_urls
from hospital_extractor.models.dataset import HospitalDataset
from hospital_extractor.models.hospital import HospitalOverview
from hospital_extractor.models.treatment import Treatment

_BASE = "https://test-hospital.example"


def _record(source_url: str = _BASE, **kwargs: object) -> dict[str, object]:
    return {"source_url": source_url, **kwargs}


def _make_dataset(name: str) -> HospitalDataset:
    return HospitalDataset(
        hospital=HospitalOverview(**_record(name=name, bed_count=200)),
        treatments=[Treatment(**_record(name="Knee Replacement"))],
    )


def _non_empty_result() -> ComparisonResult:
    """A real engine result with one hospital and several dimensions."""
    datasets = [
        ("https://a.test", _make_dataset("Hospital A")),
        ("https://b.test", _make_dataset("Hospital B")),
    ]
    return compare_urls(datasets, "Knee Replacement")


class TestPrintTableNonEmpty:
    def test_does_not_crash_and_prints_table(self, capsys) -> None:  # type: ignore[no-untyped-def]
        result = _non_empty_result()
        _print_table(result)
        out = capsys.readouterr().out
        assert "No hospitals to compare." not in out
        assert "Target: Total Knee Replacement" in out
        assert "Dimension" in out

    def test_prints_yes_unknown_decisions(self) -> None:
        result = _non_empty_result()
        assert result.hospitals
        decisions = {dim.decision for h in result.hospitals for dim in h.dimensions}
        assert decisions
        assert decisions <= set(BooleanDecision.__args__)


def _fake_context(ref: str, *_args: object, **_kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(
        target_url=ref,
        target_domain=ref.split("//", 1)[-1].split("/", 1)[0],
    )


class TestCompareCliNonEmpty:
    def test_json_file_path_writes_valid_json_and_table(
        self, tmp_path, capsys  # type: ignore[no-untyped-def]
    ) -> None:
        expected = _non_empty_result()
        json_path = tmp_path / "comparison.json"
        fake_report = SimpleNamespace(discovered=1, fetched=1, failed=0, skipped=0, pages=[])

        with (
            patch(
                "hospital_extractor.sources.website.run_crawl",
                new_callable=AsyncMock,
            ) as crawl,
            patch(
                "hospital_extractor.sources.website.build_context",
                side_effect=_fake_context,
            ) as context,
            patch(
                "hospital_extractor.comparison.service.compare_urls",
                return_value=expected,
            ) as compare,
        ):
            crawl.return_value = fake_report
            code = asyncio.run(
                _run_compare(
                    [
                        "https://a.test",
                        "https://b.test",
                        "--procedure",
                        "Knee Replacement",
                        "--json-file",
                        str(json_path),
                        "--output-dir",
                        str(tmp_path / "data"),
                        "--log-level",
                        "ERROR",
                    ]
                )
            )

        assert code == 0
        compare.assert_called_once()
        assert context.call_count == 2
        assert json_path.exists()

        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["target"]["name"] == "Total Knee Replacement"
        assert len(data["hospitals"]) == len(expected.hospitals)
        for hospital in data["hospitals"]:
            for dim in hospital["dimensions"]:
                assert dim["decision"] in BooleanDecision.__args__

        out = capsys.readouterr().out
        assert "Target: Total Knee Replacement" in out
        assert "YES" in out
        assert "UNKNOWN" in out

    def test_category_flag_passed_to_service(
        self, tmp_path, capsys  # type: ignore[no-untyped-def]
    ) -> None:
        expected = _non_empty_result()
        fake_report = SimpleNamespace(
            discovered=1, fetched=1, failed=0, skipped=0, pages=[]
        )

        with (
            patch(
                "hospital_extractor.sources.website.run_crawl",
                new_callable=AsyncMock,
            ) as crawl,
            patch(
                "hospital_extractor.sources.website.build_context",
                side_effect=_fake_context,
            ),
            patch(
                "hospital_extractor.comparison.service.compare_urls",
                return_value=expected,
            ) as compare,
        ):
            crawl.return_value = fake_report
            code = asyncio.run(
                _run_compare(
                    [
                        "https://a.test",
                        "https://b.test",
                        "--procedure",
                        "Cardiology",
                        "--category",
                        "department",
                        "--json",
                        "--output-dir",
                        str(tmp_path / "data"),
                        "--log-level",
                        "ERROR",
                    ]
                )
            )

        assert code == 0
        _args, kwargs = compare.call_args
        assert kwargs.get("category") == "department"

    def test_category_flag_rejects_invalid_choice(self) -> None:
        import pytest

        from hospital_extractor.cli import build_compare_parser

        parser = build_compare_parser()
        argv = [
            "--procedure", "x",
            "--category", "bogus",
            "https://a.test", "https://b.test",
        ]
        with pytest.raises(SystemExit) as excinfo:
            parser.parse_args(argv)
        assert excinfo.value.code == 2