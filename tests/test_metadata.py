"""Tests for per-page metadata construction."""

from __future__ import annotations

from pathlib import Path

from hospital_extractor.crawler.classify import PageClassification
from hospital_extractor.crawler.crawler import FetchedPage
from hospital_extractor.crawler.metadata import PageMetadata, build_metadata

PAGE = b"""\
<html>
<head>
  <title>  Saket  Hospital | About Us </title>
  <meta name="description" content="   Official hospital overview.  ">
  <link rel="canonical" href="/hospital-network/saket-hospital">
</head>
<body>content</body>
</html>
"""


def _page(**overrides: object) -> FetchedPage:
    values: dict[str, object] = {
        "url": "https://example.com/hospital-network/saket-hospital",
        "status_code": 200,
        "html": PAGE,
        "final_url": "https://example.com/hospital-network/saket-hospital",
    }
    values.update(overrides)
    return FetchedPage(**values)  # type: ignore[arg-type]


class TestBuildMetadata:
    def test_extracts_document_signals(self) -> None:
        classification = PageClassification(category="hospital", confidence=0.9, signals=("u",))
        metadata = build_metadata(
            _page(),
            depth=0,
            parent_url=None,
            discovery_source="sitemap",
            classification=classification,
            raw_content_reference=None,
        )
        assert metadata.title == "Saket Hospital | About Us"
        assert metadata.meta_description == "Official hospital overview."
        assert metadata.canonical_url == "https://example.com/hospital-network/saket-hospital"
        assert metadata.page_category == "hospital"
        assert metadata.classification_confidence == 0.9
        assert metadata.status_code == 200
        assert metadata.depth == 0
        assert metadata.discovery_source == "sitemap"

    def test_carries_crawl_position(self) -> None:
        metadata = build_metadata(
            _page(),
            depth=2,
            parent_url="https://example.com/doctor/x",
            discovery_source="internal_link",
            classification=None,
            raw_content_reference=None,
        )
        assert metadata.depth == 2
        assert metadata.parent_url == "https://example.com/doctor/x"
        assert metadata.discovery_source == "internal_link"
        assert metadata.page_category is None
        assert metadata.classification_confidence is None

    def test_absolutely_referenced_canonical_kept(self) -> None:
        page = _page(html=b'<html><link rel="canonical" href="https://example.com/canon"></html>')
        metadata = build_metadata(
            page,
            depth=0,
            parent_url=None,
            discovery_source="sitemap",
            classification=None,
            raw_content_reference=None,
        )
        assert metadata.canonical_url == "https://example.com/canon"

    def test_missing_signals_are_none(self) -> None:
        metadata = build_metadata(
            _page(html=b"<html><body>bare</body></html>"),
            depth=0,
            parent_url=None,
            discovery_source="sitemap",
            classification=None,
            raw_content_reference=None,
        )
        assert metadata.title is None
        assert metadata.meta_description is None
        assert metadata.canonical_url is None

    def test_content_type_and_raw_reference_recorded(self) -> None:
        raw_path = Path("/tmp/raw/run1/abc.html")
        metadata = build_metadata(
            _page(content_type="text/html; charset=utf-8"),
            depth=0,
            parent_url=None,
            discovery_source="sitemap",
            classification=None,
            raw_content_reference=raw_path,
        )
        assert metadata.content_type == "text/html; charset=utf-8"
        assert metadata.raw_content_reference == raw_path
        assert isinstance(metadata, PageMetadata)
