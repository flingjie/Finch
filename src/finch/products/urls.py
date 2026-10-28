"""产品身份抽取（启发式）：把原始工件映射到一个稳定的产品 key。

规则：
- 优先 ``canonical_url`` 若它是产品页（GitHub repo / Product Hunt / 非社媒域名）。
- 否则扫正文里的产品链接（GitHub repo、Product Hunt、非社媒域名）。
- 兜底用工件自身 URL / artifact_id（自包含 launch 帖也是候选）。
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from finch.sources.models import RawArtifact

_SOCIAL_HOSTS = {
    "x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
    "reddit.com",
    "www.reddit.com",
    "v2ex.com",
    "www.v2ex.com",
    "weixin.qq.com",
    "mp.weixin.qq.com",
    "xiaohongshu.com",
    "www.xiaohongshu.com",
    "xhslink.com",
    "t.co",
    "bit.ly",
    "tinyurl.com",
}

_URL_RE = re.compile(r"https?://[^\s\)\]\"'<>]+", re.IGNORECASE)


def normalize_product_key(url: str) -> str:
    """规范化一个产品 URL 为稳定 key：去 scheme、去尾部斜杠、小写。"""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/").lower()
    if not host:
        return url.strip().lower()
    return f"{host}{path}"


def is_social_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in _SOCIAL_HOSTS


def classify_product_url(url: str) -> str | None:
    """判断 URL 是否指向产品；是则返回规范化 key，否则 None。"""
    if not url or not url.strip().lower().startswith(("http://", "https://")):
        return None
    if is_social_url(url):
        return None
    return normalize_product_key(url)


def extract_urls(text: str) -> list[str]:
    return [m.group(0).rstrip(".,;") for m in _URL_RE.finditer(text or "")]


def profile_url_for(platform: str, *, handle: str = "", external_id: str = "") -> str | None:
    """由平台身份构造公开主页 URL；产品领域独立实现，不依赖 peers 领域。"""
    ident = (handle or external_id).strip().lstrip("@")
    if not ident:
        return None
    if platform == "x":
        return f"https://x.com/{ident}"
    if platform == "reddit":
        return f"https://www.reddit.com/user/{ident}"
    if platform == "github":
        return f"https://github.com/{ident}"
    if platform == "v2ex":
        return f"https://www.v2ex.com/member/{ident}"
    if platform == "xiaohongshu":
        return f"https://www.xiaohongshu.com/user/profile/{ident}"
    return None


def product_url_for_key(key: str) -> str | None:
    """由规范化 key 重建产品 URL；社媒/短链 key 不当作产品页。"""
    key = (key or "").strip()
    if not key:
        return None
    if key.lower().startswith(("http://", "https://")):
        return None if is_social_url(key) else key
    if is_social_url(f"https://{key}"):
        return None
    return f"https://{key.lstrip('/')}"


def first_product_url(artifacts: list[RawArtifact]) -> str | None:
    """从一批工件中挑出第一个非社媒产品 URL；找不到返回 None。"""
    for art in artifacts:
        candidates = [art.canonical_url, *extract_urls((art.title or "") + "\n" + art.text)]
        for url in candidates:
            if classify_product_url(url):
                return url.strip()
    return None


def product_key_for(artifact: RawArtifact) -> str:
    """返回该工件归属的产品 key（永不返回空；兜底 artifact_id 保证单工件成簇）。"""
    canonical = classify_product_url(artifact.canonical_url)
    if canonical:
        return canonical

    text = (artifact.title or "") + "\n" + artifact.text
    for url in extract_urls(text):
        key = classify_product_url(url)
        if key:
            return key

    if artifact.canonical_url.strip():
        return normalize_product_key(artifact.canonical_url)
    return artifact.artifact_id
