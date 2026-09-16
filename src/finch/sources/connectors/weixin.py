"""微信公众号 Connector — MVP 围绕文章链接导入（无全局站内搜索）。"""

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


class WeixinConnector:
    source = Source.WEIXIN

    def probe(self, capabilities: OpenCliCapabilities) -> SourceStatus:
        surfaces = capabilities.surfaces
        if not surfaces:
            return SourceStatus.DEGRADED
        if "weixin" in surfaces or "web" in surfaces:
            return SourceStatus.READY
        return SourceStatus.UNAVAILABLE

    def plan(self, context: DiscoveryContext) -> list[OpenCliRequest]:
        """仅对已知文章 URL 生成下载/阅读请求。"""
        reqs: list[OpenCliRequest] = []
        for url in context.urls:
            if "weixin" in url or "mp.weixin.qq.com" in url:
                reqs.append(
                    OpenCliRequest(
                        surface="weixin",
                        command="download",
                        args=(url, "-f", "json"),
                        timeout_seconds=60,
                    )
                )
            else:
                reqs.append(
                    OpenCliRequest(
                        surface="web",
                        command="read",
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
        text = str(row.get("content") or row.get("text") or row.get("body") or "")
        if not url and not text:
            return None
        sid = str(row.get("id") or content_fingerprint(url or text))
        author = str(
            row.get("author")
            or row.get("account")
            or row.get("biz")
            or ""
        )
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
            capture_method="adapter",
            content_fingerprint=fp,
        )
