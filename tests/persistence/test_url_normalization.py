"""Tests for deterministic canonical URL normalization."""

from __future__ import annotations

import pytest

from hospital_extractor.persistence.exceptions import InvalidFacilityUrlError
from hospital_extractor.persistence.url_normalization import normalize_canonical_url


def test_scheme_and_hostname_normalization() -> None:
    result = normalize_canonical_url("HTTPS://WWW.Example-Hospital.COM/path")
    assert result.normalized == "https://www.example-hospital.com/path"
    assert result.domain == "www.example-hospital.com"


def test_default_ports_removed() -> None:
    assert normalize_canonical_url("http://example.com:80/path").normalized == "http://example.com/path"
    assert (
        normalize_canonical_url("https://example.com:443/path").normalized
        == "https://example.com/path"
    )
    assert (
        normalize_canonical_url("https://example.com:8443/path").normalized
        == "https://example.com:8443/path"
    )


def test_fragments_removed() -> None:
    assert (
        normalize_canonical_url("https://example.com/path#section").normalized
        == "https://example.com/path"
    )


def test_trailing_slash_behavior_deterministic() -> None:
    # Root keeps slash; non-root paths normalize to no trailing slash for stable identity.
    assert normalize_canonical_url("https://example.com").normalized == "https://example.com/"
    assert normalize_canonical_url("https://example.com/").normalized == "https://example.com/"
    assert (
        normalize_canonical_url("https://example.com/delhi").normalized
        == "https://example.com/delhi"
    )
    assert (
        normalize_canonical_url("https://example.com/delhi/").normalized
        == "https://example.com/delhi"
    )


def test_query_parameter_preservation() -> None:
    url = "https://example.com/locations?branch=delhi&utm=1"
    assert (
        normalize_canonical_url(url).normalized
        == "https://example.com/locations?branch=delhi&utm=1"
    )


def test_idn_normalization_consistent() -> None:
    result = normalize_canonical_url("https://münchen.example/klinik")
    assert result.domain == "xn--mnchen-3ya.example"
    assert result.normalized.startswith("https://xn--mnchen-3ya.example/")


def test_invalid_scheme_rejected() -> None:
    with pytest.raises(InvalidFacilityUrlError):
        normalize_canonical_url("ftp://example.com/hospital")
    with pytest.raises(InvalidFacilityUrlError):
        normalize_canonical_url("javascript:alert(1)")


def test_malformed_or_empty_urls_rejected() -> None:
    for bad in ["", "   ", "not-a-url", "http:///path", "https://"]:
        with pytest.raises(InvalidFacilityUrlError):
            normalize_canonical_url(bad)


def test_shared_domain_different_paths_not_same_facility_url() -> None:
    a = normalize_canonical_url("https://apollohospitals.com/delhi")
    b = normalize_canonical_url("https://apollohospitals.com/chennai")
    assert a.domain == b.domain == "apollohospitals.com"
    assert a.normalized != b.normalized


def test_whitespace_trimmed() -> None:
    assert (
        normalize_canonical_url("  https://example.com/delhi  ").normalized
        == "https://example.com/delhi"
    )
