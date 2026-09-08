"""输入解析：text/file/url → 规范化正文 + content_hash + 样本数。

多篇文本用 ``---``（整行）分隔；``--url`` 按域名路由到 X thread / Reddit post /
webfetch，取不到由各 adapter 抛来源异常，不猜测内容。
"""

import hashlib
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel

from finch.reddit.opencli_client import RedditOpenCliClient
from finch.twitter.models import Tweet
from finch.twitter.opencli_client import OpenCliClient
from finch.webfetch.fetcher import WebFetcher

_SAMPLE_SPLIT = re.compile(r"\n\s*---\s*\n")


class ResolvedSource(BaseModel):
    """规范化后的分析输入。"""

    body: str
    content_hash: str
    sample_size: int
    source_type: Literal["text", "file", "url"]
    source_ref: str | None = None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_samples(text: str) -> list[str]:
    return [p.strip() for p in _SAMPLE_SPLIT.split(text) if p.strip()]


class SourceResolver:
    """把 text/file/url 解析成统一的 ResolvedSource（纯领域逻辑，注入只读 adapter）。"""

    def __init__(
        self,
        x: OpenCliClient,
        reddit: RedditOpenCliClient,
        web: WebFetcher,
    ) -> None:
        self.x = x
        self.reddit = reddit
        self.web = web

    def resolve_text(self, text: str) -> ResolvedSource:
        return ResolvedSource(
            body=text, content_hash=_hash(text), sample_size=1,
            source_type="text", source_ref=None,
        )

    def resolve_file(self, path: str) -> ResolvedSource:
        text = Path(path).read_text()
        samples = _split_samples(text)
        body = "\n\n---\n\n".join(samples) if len(samples) > 1 else text
        return ResolvedSource(
            body=body, content_hash=_hash(body), sample_size=len(samples),
            source_type="file", source_ref=path,
        )

    def resolve_url(self, url: str) -> ResolvedSource:
        # 按解析后的 host 精确路由（不用子串匹配），避免 http://evil.com/x.com 误路由。
        host = (urlparse(url).hostname or "").lower()
        if host == "x.com" or host == "twitter.com" or host.endswith((".x.com", ".twitter.com")):
            body = self._x_thread(url)
        elif host == "reddit.com" or host.endswith(".reddit.com"):
            body = self._reddit_post(url)
        else:
            body = self.web.fetch(url)
        return ResolvedSource(
            body=body, content_hash=_hash(body), sample_size=1,
            source_type="url", source_ref=url,
        )

    def _x_thread(self, url: str) -> str:
        tweets: list[Tweet] = self.x.thread(url)
        body = "\n\n".join(t.text for t in tweets).strip()
        if not body:
            raise RuntimeError("x source unavailable: empty thread")
        return body

    def _reddit_post(self, url: str) -> str:
        post = self.reddit.post(url)
        if post is None:
            raise RuntimeError("reddit source unavailable: post not found")
        return post.content()
