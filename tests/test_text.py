from hospital_extractor.utils.text import (
    clean_text,
    collapse_whitespace,
    dedupe_sequential,
    is_blank,
    normalize_unicode_spaces,
    strip_html,
    truncate,
)


class TestIsBlank:
    def test_none(self) -> None:
        assert is_blank(None)

    def test_empty(self) -> None:
        assert is_blank("")

    def test_whitespace_only(self) -> None:
        assert is_blank("  \t  ")

    def test_content(self) -> None:
        assert not is_blank(" doctors ")


class TestNormalizeUnicodeSpaces:
    def test_nbsp_replaced(self) -> None:
        assert normalize_unicode_spaces("a\u00a0b") == "a b"

    def test_thin_and_zero_width(self) -> None:
        assert normalize_unicode_spaces("a\u2009b\u200bc") == "a b c"


class TestCollapseWhitespace:
    def test_collapses_runs(self) -> None:
        assert collapse_whitespace("  a   b\n\t c ") == "a b c"

    def test_strips_edges(self) -> None:
        assert collapse_whitespace("   value  ") == "value"

    def test_handles_nbsp(self) -> None:
        assert collapse_whitespace("a\u00a0\u00a0b") == "a b"


class TestStripHtml:
    def test_removes_tags(self) -> None:
        assert strip_html("<p>Hello <b>World</b></p>") == "Hello World"

    def test_unescapes_entities(self) -> None:
        assert strip_html("Dr. &amp; Team") == "Dr. & Team"

    def test_empty_input(self) -> None:
        assert strip_html("") == ""


class TestTruncate:
    def test_short_value_unchanged(self) -> None:
        assert truncate("short", 10) == "short"

    def test_long_value_truncated(self) -> None:
        assert truncate("abcdefghij", 5) == "ab..."

    def test_zero_limit(self) -> None:
        assert truncate("abc", 0) == "abc"


class TestDedupeSequential:
    def test_removes_immediate_duplicates(self) -> None:
        assert dedupe_sequential(["a", "a", "b", "b", "c"]) == ["a", "b", "c"]

    def test_preserves_order_and_non_adjacent_duplicates(self) -> None:
        assert dedupe_sequential(["a", "b", "a"]) == ["a", "b", "a"]

    def test_drops_blank_entries(self) -> None:
        assert dedupe_sequential(["a", "  ", "b"]) == ["a", "b"]

    def test_ignores_whitespace_differences(self) -> None:
        assert dedupe_sequential(["a  b", "a b"]) == ["a  b"]


class TestCleanText:
    def test_full_pipeline(self) -> None:
        assert clean_text("<p>  Max  &amp;   Son \u00a0</p>") == "Max & Son"
