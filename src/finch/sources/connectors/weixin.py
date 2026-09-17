"""微信公众号 Connector：query 走搜狗微信搜索，url 走已知文章导入。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from finch.sources.connectors import DiscoveryContext
from finch.sources.fingerprint import artifact_id, content_fingerprint
from finch.sources.models import (
    AuthorIdentity,
    OpenCliCapabilities,
    OpenCliRequest,
    OpenCliResult,
    RawArtifact,
    Source,
    SourceStatus,
)
from finch.sources.plan_util import empty_capabilities, resolve_command


def _parse_publish_time(value: Any) -> datetime | None:
    """尽力解析搜狗/公众号发布时间；无法解析返回 None（投影时回退 retrieved_at）。"""
    s = str(value or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


class WeixinConnector:
    source = Source.WEIXIN

    def probe(self, capabilities: OpenCliCapabilities) -> SourceStatus:
        surfaces = capabilities.surfaces
        if not surfaces:
            return SourceStatus.DEGRADED
        if "weixin" in surfaces or "web" in surfaces:
            return SourceStatus.READY
        return SourceStatus.UNAVAILABLE

    def plan(
        self,
        context: DiscoveryContext,
        capabilities: OpenCliCapabilities | None = None,
    ) -> list[OpenCliRequest]:
        """query 模式走搜狗微信搜索；url 模式对已知文章 URL 生成下载/阅读请求。"""
        caps = capabilities or empty_capabilities()
        reqs: list[OpenCliRequest] = []
        if context.queries:
            search_cmd = resolve_command(caps, "weixin", ["search"])
            if not search_cmd and not caps.surfaces:
                search_cmd = "search"
            if search_cmd:
                for q in context.queries:
                    reqs.append(
                        OpenCliRequest(
                            surface="weixin",
                            command=search_cmd,
                            args=(q, "-f", "json"),
                            timeout_seconds=60,
                        )
                    )
        for url in context.urls:
            if "weixin" in url or "mp.weixin.qq.com" in url:
                cmd = resolve_command(caps, "weixin", ["download", "read", "article"])
                if not cmd and not caps.surfaces:
                    cmd = "download"
                if not cmd:
                    continue
                reqs.append(
                    OpenCliRequest(
                        surface="weixin",
                        command=cmd,
                        args=(url, "-f", "json"),
                        timeout_seconds=60,
                    )
                )
            else:
                cmd = resolve_command(caps, "web", ["read", "fetch"])
                if not cmd and not caps.surfaces:
                    cmd = "read"
                if not cmd:
                    continue
                reqs.append(
                    OpenCliRequest(
                        surface="web",
                        command=cmd,
                        args=(url, "-f", "json"),
                        timeout_seconds=60,
                    )
                )
        return reqs

    def normalize(self, result: OpenCliResult) -> list[RawArtifact]:
        now = datetime.now(UTC)
        out: list[RawArtifact] = []
        for row in result.rows:
            art = self._row_to_artifact(row, now)
            if art is not None:
                out.append(art)
        return out

    def normalize_url_import(
        self, url: str, title: str, text: str, author: str = ""
    ) -> RawArtifact:
        """URL 粘贴导入路径（WebFetcher / 用户粘贴）。"""
        now = datetime.now(UTC)
        sid = content_fingerprint(url)
        body = f"{title}\n\n{text}".strip()
        fp = content_fingerprint(body, url=url)
        return RawArtifact(
            artifact_id=artifact_id("weixin", "article", sid),
            source=Source.WEIXIN,
            source_type="article",
            source_id=sid,
            canonical_url=url,
            author_identity=AuthorIdentity(
                platform="weixin", external_id=author, handle=author
            ),
            title=title or None,
            text=body,
            published_at=None,
            metrics={},
            retrieved_at=now,
            capture_method="url_import",
            content_fingerprint=fp,
        )

    def _row_to_artifact(self, row: dict[str, Any], now: datetime) -> RawArtifact | None:
        url = str(row.get("url") or row.get("canonical_url") or "").strip()
        title = str(row.get("title") or "")
        text = str(
            row.get("content")
            or row.get("text")
            or row.get("body")
            or row.get("summary")
            or ""
        )
        if not url and not text:
            return None
        sid = str(row.get("id") or content_fingerprint(url or text))
        author = str(
            row.get("author")
            or row.get("account")
            or row.get("biz")
            or ""
        )
        if not author and url:
            # 搜狗搜索结果不含公众号名；用文章 URL 作为临时身份。后续可在 adapter 中
            # 提取 ``.s-p a``（公众号名）以得到账户级身份。
            author = url
        body = f"{title}\n\n{text}".strip()
        fp = content_fingerprint(body, url=url)
        return RawArtifact(
            artifact_id=artifact_id("weixin", "article", sid),
            source=Source.WEIXIN,
            source_type="article",
            source_id=sid,
            canonical_url=url,
            author_identity=AuthorIdentity(
                platform="weixin", external_id=author, handle=author
            ),
            title=title or None,
            text=body,
            published_at=_parse_publish_time(row.get("publish_time")),
            metrics={},
            retrieved_at=now,
            capture_method="adapter",
            content_fingerprint=fp,
        )
