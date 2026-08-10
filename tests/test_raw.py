"""Tests for the raw HTML disk cache."""

from __future__ import annotations

from pathlib import Path

from hospital_extractor.crawler.crawler import FetchedPage
from hospital_extractor.storage.raw import RawStore


class TestRawStore:
    def test_save_writes_html_under_run_dir(self, tmp_path: Path) -> None:
        store = RawStore(tmp_path)
        page = FetchedPage(
            url="https://example.com/procedures/total-knee-replacement",
            status_code=200,
            html=b"<html>raw</html>",
            final_url="https://example.com/procedures/total-knee-replacement",
        )
        path = store.save("run-1", page)
        assert path.parent.parent == tmp_path
        assert path.parent.name == "run-1"
        assert path.suffix == ".html"
        assert path.read_bytes() == b"<html>raw</html>"

    def test_exists_true_after_save(self, tmp_path: Path) -> None:
        store = RawStore(tmp_path)
        page = FetchedPage(
            url="https://example.com/a",
            status_code=200,
            html=b"x",
            final_url="https://example.com/a",
        )
        store.save("run-1", page)
        assert store.exists("run-1", "https://example.com/a")

    def test_exists_false_when_not_cached(self, tmp_path: Path) -> None:
        store = RawStore(tmp_path)
        assert not store.exists("run-1", "https://example.com/never-fetched")

    def test_path_is_stable_across_calls(self, tmp_path: Path) -> None:
        store = RawStore(tmp_path)
        assert store.path_for("run-1", "https://example.com/a") == store.path_for(
            "run-1", "https://example.com/a"
        )

    def test_normalization_makes_urls_idempotent(self, tmp_path: Path) -> None:
        store = RawStore(tmp_path)
        assert store.path_for("run-1", "https://example.com/a") == store.path_for(
            "run-1", "https://example.com/a/"
        )

    def test_different_runs_get_different_paths(self, tmp_path: Path) -> None:
        store = RawStore(tmp_path)
        assert store.path_for("run-1", "https://example.com/a") != store.path_for(
            "run-2", "https://example.com/a"
        )
