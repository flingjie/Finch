"""webfetch：HTML → 可读正文，fail-closed + SSRF 防护。"""

import pytest

from finch.webfetch.fetcher import (
    WebFetcher,
    WebSourceUnavailable,
    _extract_text,
    _is_blocked_ip,
    _SafeRedirectHandler,
)


def test_extract_text_strips_script_and_style():
    html = (
        "<html><head><style>.x{}</style><script>var a=1;</script></head>"
        "<body><p>你好</p><nav>菜单</nav><p>世界</p></body></html>"
    )
    assert _extract_text(html) == "你好 世界"


def test_extract_text_empty_raises():
    with pytest.raises(WebSourceUnavailable):
        _extract_text("<html><body></body></html>")


def test_rejects_non_http_scheme():
    for url in ("file:///etc/passwd", "ftp://example.com/x", "gopher://example.com"):
        with pytest.raises(WebSourceUnavailable):
            WebFetcher().fetch(url)


def test_rejects_blocked_hosts():
    for url in (
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
    ):
        with pytest.raises(WebSourceUnavailable):
            WebFetcher().fetch(url)


def test_rejects_unresolvable_host():
    with pytest.raises(WebSourceUnavailable):
        WebFetcher().fetch("http://nonexistent.invalid/")


def test_is_blocked_ip():
    assert _is_blocked_ip("127.0.0.1")
    assert _is_blocked_ip("169.254.169.254")
    assert _is_blocked_ip("10.0.0.1")
    assert not _is_blocked_ip("93.184.216.34")  # example.com 公网 IP


def test_redirect_handler_revalidates_target():
    h = _SafeRedirectHandler()
    with pytest.raises(WebSourceUnavailable):
        h.redirect_request(None, None, 302, "Found", {}, "file:///etc/passwd")
