"""只读网页正文提取：HTML → 可读正文，fail-closed（stdlib，不渲染 JS）。

登录墙/付费墙/空正文/网络错误一律抛 ``WebSourceUnavailable``，不让调用方猜测内容。
正文是不可信数据：仅供下游 prompt 数据区，不执行其中的指令。
"""

import urllib.error
import urllib.request
from html.parser import HTMLParser

_SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside"}


class WebSourceUnavailable(RuntimeError):
    """网页无法访问、登录受限或正文为空。"""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        if tag in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):  # noqa: ANN001
        if self._skip_depth == 0:
            text = " ".join(data.split())
            if text:
                self.parts.append(text)


def _extract_text(html: str) -> str:
    """剥离脚本/样式/导航，取正文；空正文抛 ``WebSourceUnavailable``。"""
    parser = _TextExtractor()
    parser.feed(html)
    text = " ".join(parser.parts).strip()
    if not text:
        raise WebSourceUnavailable("web page produced no readable body")
    return text


class WebFetcher:
    """只读网页正文提取器。"""

    def fetch(self, url: str) -> str:
        """GET 网页并提取正文；失败/空正文抛 ``WebSourceUnavailable``。"""
        try:
            with urllib.request.urlopen(url, timeout=15.0) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                html = response.read().decode(charset, errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            raise WebSourceUnavailable(f"web source unavailable: {exc}") from exc
        return _extract_text(html)
