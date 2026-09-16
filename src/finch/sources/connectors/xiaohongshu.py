"""小红书 Source Connector。"""

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


class XiaohongshuConnector:
    source = Source.XIAOHONGSHU

    def probe(self, capabilities: OpenCliCapabilities) -> SourceStatus:
        if not capabilities.surfaces:
            return SourceStatus.DEGRADED
        if "xiaohongshu" not in capabilities.surfaces:
            return SourceStatus.UNAVAILABLE
        return SourceStatus.READY

    def plan(self, context: DiscoveryContext) -> list[OpenCliRequest]:
        reqs: list[OpenCliRequest] = []
        for q in context.queries:
            reqs.append(
                OpenCliRequest(
                    surface="xiaohongshu",
                    command="search",
                    args=(q, "--limit", str(context.limit), "-f", "json"),
                    timeout_seconds=60,
                )
            )
        for url in context.urls:
            reqs.append(
                OpenCliRequest(
                    surface="xiaohongshu",
                    command="note",
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

    def _row_to_artifact(self, row: dict[str, Any], now: datetime) -> RawArtifact | None:
        sid = str(
            row.get("id") or row.get("note_id") or row.get("noteId") or ""
        ).strip()
        if not sid:
            return None
        title = str(row.get("title") or row.get("display_title") or "")
        text = str(row.get("desc") or row.get("text") or row.get("content") or "")
        body = f"{title}\n\n{text}".strip()
        url = str(row.get("url") or row.get("share_url") or "")
        user = row.get("user") if isinstance(row.get("user"), dict) else {}
        assert isinstance(user, dict)
        handle = str(
            row.get("author")
            or user.get("nickname")
            or user.get("name")
            or ""
        )
        uid = str(user.get("user_id") or user.get("id") or handle)
        fp = content_fingerprint(body, url=url)
        return RawArtifact(
            artifact_id=artifact_id("xiaohongshu", "note", sid),
            source=Source.XIAOHONGSHU,
            source_type="note",
            source_id=sid,
            canonical_url=url,
            author_identity=AuthorIdentity(
                platform="xiaohongshu", external_id=uid, handle=handle
            ),
            title=title or None,
            text=body,
            published_at=None,
            metrics={
                "likes": row.get("liked_count") or row.get("likes") or 0,
            },
            retrieved_at=now,
            capture_method="adapter",
            content_fingerprint=fp,
        )
