import pytest

from hospital_extractor.utils.urls import (
    InvalidURLError,
    is_fetchable,
    is_same_domain,
    is_same_page,
    normalize_url,
    resolve_url,
    validate_url,
)


class TestValidateUrl:
    def test_valid_url_is_normalized(self) -> None:
        assert validate_url("https://www.maxhealthcare.in/") == "https://www.maxhealthcare.in/"

    def test_rejects_empty(self) -> None:
        with pytest.raises(InvalidURLError):
            validate_url("")

    def test_rejects_blank(self) -> None:
        with pytest.raises(InvalidURLError):
            validate_url("   ")

    def test_rejects_missing_host(self) -> None:
        with pytest.raises(InvalidURLError):
            validate_url("/no-host-here")

    def test_rejects_non_http_scheme(self) -> None:
        with pytest.raises(InvalidURLError):
            validate_url("ftp://example.com/file")

    def test_rejects_javascript(self) -> None:
        with pytest.raises(InvalidURLError):
            validate_url("javascript:void(0)")


class TestNormalizeUrl:
    def test_lowercases_scheme_and_host(self) -> None:
        assert normalize_url("HTTPS://WWW.Example.COM/Path") == "https://www.example.com/Path"

    def test_removes_default_port(self) -> None:
        assert normalize_url("https://www.example.com:443/path") == "https://www.example.com/path"
        assert normalize_url("http://www.example.com:80/path") == "http://www.example.com/path"

    def test_keeps_non_default_port(self) -> None:
        assert normalize_url("http://localhost:8000/x") == "http://localhost:8000/x"

    def test_removes_fragment(self) -> None:
        assert normalize_url("https://example.com/page#section") == "https://example.com/page"

    def test_strips_trailing_slash(self) -> None:
        assert normalize_url("https://example.com/a/b/") == "https://example.com/a/b"

    def test_keeps_root_slash(self) -> None:
        assert normalize_url("https://example.com") == "https://example.com/"

    def test_resolves_dot_segments(self) -> None:
        assert normalize_url("https://example.com/a/../b") == "https://example.com/b"

    def test_sorts_query_parameters(self) -> None:
        assert normalize_url("https://example.com/p?b=2&a=1") == "https://example.com/p?a=1&b=2"

    def test_raises_without_host(self) -> None:
        with pytest.raises(InvalidURLError):
            normalize_url("relative/path")


class TestResolveUrl:
    def test_resolves_relative(self) -> None:
        assert resolve_url("https://example.com/a/", "../b") == "https://example.com/b"

    def test_resolves_absolute_path(self) -> None:
        assert resolve_url("https://example.com/a", "/b") == "https://example.com/b"

    def test_resolves_absolute_url(self) -> None:
        assert resolve_url("https://example.com/", "https://other.com/x") == "https://other.com/x"

    def test_resolves_protocol_relative(self) -> None:
        assert resolve_url("https://example.com/", "//other.com/x") == "https://other.com/x"

    @pytest.mark.parametrize(
        "href",
        ["", "#", "#anchor", "javascript:void(0)", "mailto:a@b.c", "tel:+911234"],
    )
    def test_ignores_non_navigable_hrefs(self, href: str) -> None:
        assert resolve_url("https://example.com/", href) is None


class TestIsSameDomain:
    def test_identical_host(self) -> None:
        assert is_same_domain("https://example.com/x", "https://example.com/")

    def test_www_equivalent(self) -> None:
        assert is_same_domain("https://example.com/", "https://www.example.com/")
        assert is_same_domain("https://www.example.com/", "https://example.com/")

    def test_different_domain(self) -> None:
        assert not is_same_domain("https://other.com/", "https://example.com/")

    def test_subdomain_rejected_by_default(self) -> None:
        assert not is_same_domain("https://sub.example.com/", "https://example.com/")

    def test_subdomain_allowed_when_opted_in(self) -> None:
        assert is_same_domain(
            "https://sub.example.com/", "https://example.com/", allow_subdomains=True
        )

    def test_missing_hosts(self) -> None:
        assert not is_same_domain("not-a-url", "https://example.com/")


class TestIsFetchable:
    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/",
            "https://example.com/hospital-network/max-smart-super-speciality-hospital-saket",
            "https://example.com/page.html",
        ],
    )
    def test_fetchable(self, url: str) -> None:
        assert is_fetchable(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/logo.png",
            "https://example.com/video.mp4",
            "https://example.com/site.css",
            "https://example.com/app.js",
            "https://example.com/doc.pdf",
            "ftp://example.com/",
        ],
    )
    def test_not_fetchable(self, url: str) -> None:
        assert not is_fetchable(url)


class TestIsSamePage:
    def test_ignores_query_parameters(self) -> None:
        assert is_same_page(
            "https://example.com/doctors?page=2", "https://example.com/doctors?page=1"
        )

    def test_different_paths(self) -> None:
        assert not is_same_page("https://example.com/a", "https://example.com/b")

    def test_fragment_ignored(self) -> None:
        assert is_same_page("https://example.com/a#one", "https://example.com/a")
