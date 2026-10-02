"""GitHub URL 提取与归一化（设计 §4.3）。

短链展开：限制跳转次数、超时、响应大小；禁止私网地址。
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

# github.com/owner/repo 及子路径（issues/pull/tree/blob/releases/...）
_GITHUB_REPO_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"(?:/(?:issues|pull|tree|blob|releases|commit|commits|actions|wiki|settings"
    r"|projects|security|network|pulse|graphs|compare)(?:/|\b)|/?\b|/|$|\?|#)",
    re.IGNORECASE,
)
_GITHUB_NON_REPO_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/"
    r"(?:about|features|pricing|enterprise|marketplace|explore|topics|collections|"
    r"trending|events|sponsors|settings|notifications|login|signup|orgs|"
    r"(?P<user>[A-Za-z0-9_.-]+)/?)(?:\s|$|/|\?|#)",
    re.IGNORECASE,
)
_GIST_RE = re.compile(r"(?:https?://)?gist\.github\.com/", re.IGNORECASE)
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


@dataclass(frozen=True)
class ExtractedLink:
    """从推文中抽出的一条链接分类结果。"""

    raw_url: str
    kind: str  # repo | non_repo | short | other
    owner: str = ""
    repo: str = ""

    @property
    def repo_key(self) -> str:
        if self.kind != "repo":
            return ""
        return f"{self.owner}/{self.repo}".lower()

    @property
    def canonical_url(self) -> str:
        if self.kind != "repo":
            return self.raw_url
        return f"https://github.com/{self.owner}/{self.repo}"


def _strip_git_suffix(name: str) -> str:
    return name[:-4] if name.lower().endswith(".git") else name


def classify_github_url(url: str) -> ExtractedLink:
    """把一条 URL 分类为 repo / non_repo / other。"""
    cleaned = url.strip().rstrip(").,;]")
    if _GIST_RE.search(cleaned):
        return ExtractedLink(raw_url=cleaned, kind="non_repo")
    m = _GITHUB_REPO_RE.search(cleaned)
    if m:
        owner = m.group("owner")
        repo = _strip_git_suffix(m.group("repo"))
        # 单段路径可能是用户主页，被 non-repo 规则更准；双段才是仓库。
        if owner.lower() in {
            "about",
            "features",
            "pricing",
            "marketplace",
            "explore",
            "topics",
            "settings",
            "login",
            "signup",
            "orgs",
            "sponsors",
        }:
            return ExtractedLink(raw_url=cleaned, kind="non_repo")
        return ExtractedLink(raw_url=cleaned, kind="repo", owner=owner, repo=repo)
    if "github.com" in cleaned.lower():
        return ExtractedLink(raw_url=cleaned, kind="non_repo")
    return ExtractedLink(raw_url=cleaned, kind="other")


def extract_urls_from_text(text: str) -> list[str]:
    """从正文抽出 http(s) URL。"""
    return [m.group(0).rstrip(").,;]") for m in _URL_IN_TEXT_RE.finditer(text or "")]


def extract_github_links(
    text: str, *, entity_urls: list[str] | None = None
) -> list[ExtractedLink]:
    """优先实体展开 URL，再补正文提取；去重保留顺序。"""
    seen: set[str] = set()
    out: list[ExtractedLink] = []
    for raw in list(entity_urls or []) + extract_urls_from_text(text):
        key = raw.lower()
        if key in seen:
            continue
        seen.add(key)
        link = classify_github_url(raw)
        if link.kind == "other" and _looks_like_short_link(raw):
            link = ExtractedLink(raw_url=raw, kind="short")
        out.append(link)
    return out


def _looks_like_short_link(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"t.co", "bit.ly", "tinyurl.com", "buff.ly", "ow.ly"} or host.endswith(
        ".link"
    )


def _is_private_host(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        return True
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if any(ip in net for net in _PRIVATE_NETS):
            return True
    return False


def expand_short_url(
    url: str,
    *,
    max_redirects: int = 5,
    timeout_seconds: float = 5.0,
    max_bytes: int = 64_000,
) -> str | None:
    """有界 HTTP 重定向展开；失败返回 None（不抛）。

    使用 urllib（stdlib）；禁止私网；只读 HEAD/GET 跟随 Location。
    """
    import urllib.error
    import urllib.request

    current = url
    for _ in range(max_redirects):
        parsed = urlparse(current)
        if parsed.scheme not in {"http", "https"}:
            return None
        host = parsed.hostname
        if not host or _is_private_host(host):
            return None
        req = urllib.request.Request(
            current,
            method="HEAD",
            headers={"User-Agent": "Finch-repo-discovery/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                # 最终 URL（urllib 已跟随部分重定向）
                final = resp.geturl()
                # 限制读取（HEAD 通常无 body）
                _ = resp.read(max_bytes)
                if final != current:
                    current = final
                    # 若已不是短链，可返回
                    if not _looks_like_short_link(final):
                        return final
                    continue
                return final
        except urllib.error.HTTPError as exc:
            # 某些短链对 HEAD 返回 405 → 改 GET 但不读大 body
            if exc.code in {405, 403}:
                req = urllib.request.Request(
                    current,
                    method="GET",
                    headers={"User-Agent": "Finch-repo-discovery/1.0"},
                )
                try:
                    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                        final = resp.geturl()
                        _ = resp.read(max_bytes)
                        return final
                except (urllib.error.URLError, TimeoutError, OSError):
                    return None
            loc = exc.headers.get("Location") if exc.headers else None
            if loc and exc.code in {301, 302, 303, 307, 308}:
                current = loc
                continue
            return None
        except (urllib.error.URLError, TimeoutError, OSError):
            return None
    return current
