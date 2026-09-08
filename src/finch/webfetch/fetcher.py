"""只读网页正文提取：HTML → 可读正文，fail-closed（stdlib，不渲染 JS）。

SSRF 防护：只允许 http/https；host 解析后拒绝回环/内网/链路本地/保留/组播/元数据 IP；
重定向目标逐跳复检。登录墙/付费墙/空正文/网络错误/被阻断 host 一律抛
``WebSourceUnavailable``，不让调用方猜测内容。正文是不可信数据，仅供下游 prompt 数据区。
"""

import ipaddress
import socket
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urlparse

_SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside"}


class WebSourceUnavailable(RuntimeError):
    """网页无法访问、登录受限、正文为空，或 host/scheme 被 SSRF 防护阻断。"""


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


def _is_blocked_ip(ip_str: str) -> bool:
    """IP 是否属于 SSRF 敏感范围（回环/内网/链路本地/保留/组播/未指定）。"""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # 解析不了就拒绝
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _validate_url(url: str) -> None:
    """校验 scheme（仅 http/https）与 host（拒绝 SSRF 敏感 IP），否则抛异常。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebSourceUnavailable(f"unsupported scheme: {parsed.scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise WebSourceUnavailable("url missing host")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise WebSourceUnavailable(f"cannot resolve host: {host}") from exc
    ips = {str(info[4][0]) for info in infos}
    if not ips or any(_is_blocked_ip(ip) for ip in ips):
        raise WebSourceUnavailable(f"blocked host: {host}")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """重定向目标逐跳复检，防止重定向绕过 SSRF 校验。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class WebFetcher:
    """只读网页正文提取器（带 SSRF 防护）。"""

    def fetch(self, url: str) -> str:
        """GET 网页并提取正文；失败/空正文/被阻断抛 ``WebSourceUnavailable``。"""
        _validate_url(url)
        opener = urllib.request.build_opener(_SafeRedirectHandler())
        try:
            with opener.open(url, timeout=15.0) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                html = response.read().decode(charset, errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            raise WebSourceUnavailable(f"web source unavailable: {exc}") from exc
        return _extract_text(html)
