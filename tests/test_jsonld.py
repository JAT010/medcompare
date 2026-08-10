"""Tests for the JSON-LD block extractor."""

from __future__ import annotations

from hospital_extractor.extraction.jsonld import JsonLdBlock, extract_jsonld


class TestExtractJsonLd:
    def test_single_object(self) -> None:
        html = (
            '<html><head><script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"Hospital","name":"Saket Hospital"}'
            "</script></head></html>"
        )
        result = extract_jsonld(html)
        assert not result.empty
        assert result.types == frozenset({"Hospital"})
        assert result.blocks[0].primary_type == "Hospital"
        assert result.blocks[0].data["name"] == "Saket Hospital"

    def test_array_of_objects(self) -> None:
        html = (
            '<html><script type="application/ld+json">'
            '[{"@type":"Hospital"},{"@type":"Physician"}]'
            "</script></html>"
        )
        result = extract_jsonld(html)
        assert result.types == frozenset({"Hospital", "Physician"})
        assert len(result.blocks) == 2

    def test_at_graph_flattened(self) -> None:
        html = (
            '<html><script type="application/ld+json">'
            '{"@graph":[{"@type":"Hospital"},{"@type":"Person"}]}'
            "</script></html>"
        )
        result = extract_jsonld(html)
        assert result.types == frozenset({"Hospital", "Person"})
        assert len(result.blocks) == 2

    def test_multiple_script_blocks(self) -> None:
        html = (
            "<html>"
            '<script type="application/ld+json">{"@type":"Hospital"}</script>'
            '<script type="application/ld+json">{"@type":"FAQPage"}</script>'
            "</html>"
        )
        result = extract_jsonld(html)
        assert result.types == frozenset({"Hospital", "FAQPage"})
        assert len(result.blocks) == 2

    def test_malformed_json_skipped(self) -> None:
        html = '<html><script type="application/ld+json">{not json}</script></html>'
        result = extract_jsonld(html)
        assert result.empty
        assert result.types == frozenset()

    def test_type_list_supported(self) -> None:
        html = (
            '<html><script type="application/ld+json">'
            '{"@type":["Organization","MedicalOrganization"]}'
            "</script></html>"
        )
        result = extract_jsonld(html)
        assert result.types == frozenset({"Organization", "MedicalOrganization"})

    def test_no_jsonld_yields_empty(self) -> None:
        assert extract_jsonld("<html><body>plain</body></html>").empty

    def test_node_without_type_kept(self) -> None:
        html = '<html><script type="application/ld+json">{"name":"x"}</script></html>'
        result = extract_jsonld(html)
        assert len(result.blocks) == 1
        assert result.blocks[0].types == ()
        assert isinstance(result.blocks[0], JsonLdBlock)
