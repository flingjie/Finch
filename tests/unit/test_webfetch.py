"""webfetch：HTML → 可读正文，fail-closed。"""

import urllib.request

import pytest

from finch.webfetch.fetcher import WebFetcher, WebSourceUnavailable, _extract_text


def test_extract_text_strips_script_and_style():
    html = (
        "<html><head><style>.x{}</style><script>var a=1;</script></head>"
        "<body><p>你好</p><nav>菜单</nav><p>世界</p></body></html>"
    )
    assert _extract_text(html) == "你好 世界"


def test_extract_text_empty_raises():
    with pytest.raises(WebSourceUnavailable):
        _extract_text("<html><body></body></html>")


def test_fetch_http_error_raises(monkeypatch):
    def boom(url, timeout):
        raise urllib.error.URLError("no host")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(WebSourceUnavailable):
        WebFetcher().fetch("https://example.invalid")
