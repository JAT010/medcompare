import pytest

from hospital_extractor.utils.hashing import sha256_bytes, sha256_text, short_hash


class TestSha256:
    def test_text_known_digest(self) -> None:
        expected = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        assert sha256_text("hello") == expected

    def test_bytes_known_digest(self) -> None:
        expected = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        assert sha256_bytes(b"hello") == expected

    def test_deterministic(self) -> None:
        assert sha256_text("abc") == sha256_text("abc")

    def test_differs_for_different_inputs(self) -> None:
        assert sha256_text("abc") != sha256_text("abd")


class TestShortHash:
    def test_default_length(self) -> None:
        assert len(short_hash("https://example.com/")) == 12

    def test_custom_length(self) -> None:
        assert len(short_hash("value", length=6)) == 6

    def test_deterministic(self) -> None:
        assert short_hash("value") == short_hash("value")

    def test_invalid_length(self) -> None:
        with pytest.raises(ValueError):
            short_hash("value", length=0)
