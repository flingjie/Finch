"""Reddit Source Connector。"""

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


class RedditConnector:
    source = Source.REDDIT

    def probe(self, capabilities: OpenCliCapabilities) -> SourceStatus:
        if not capabilities.surfaces:
            return SourceStatus.DEGRADED
        if "reddit" not in capabilities.surfaces:
            return SourceStatus.UNAVAILABLE
        return SourceStatus.READY

    def plan(self, context: DiscoveryContext) -> list[OpenCliRequest]:
        reqs: list[OpenCliRequest] = []
        for q in context.queries:
            reqs.append(
                OpenCliRequest(
                    surface="reddit",
                    command="search",
                    args=(q, "--limit", str(context.limit), "-f", "json"),
                    timeout_seconds=60,
                )
            )
        for url in context.urls:
            reqs.append(
                OpenCliRequest(
                    surface="reddit",
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

    def _row_to_artifact(self, row: dict[str, Any], now: datetime) -> RawArtifact | None:
        sid = str(row.get("id") or "").strip()
        if not sid:
            return None
        title = str(row.get("title") or "")
        text = str(row.get("selftext") or row.get("text") or "")
        body = f"{title}\n\n{text}".strip()
        url = str(row.get("url") or "")
        author = str(row.get("author") or "")
        fp = content_fingerprint(body, url=url)
        published = None
        created = row.get("created_utc")
        if created is not None:
            try:
                published = datetime.fromtimestamp(float(created), tz=UTC)
            except (ValueError, TypeError, OSError):
                published = None
        return RawArtifact(
            artifact_id=artifact_id("reddit", "post", sid),
            source=Source.REDDIT,
            source_type="post",
            source_id=sid,
            canonical_url=url,
            author_identity=AuthorIdentity(
                platform="reddit", external_id=author, handle=author
            ),
            title=title or None,
            text=body,
            published_at=published,
            metrics={
                "score": row.get("score") or 0,
                "comments": row.get("comments") or row.get("num_comments") or 0,
            },
            retrieved_at=now,
            capture_method="adapter",
            content_fingerprint=fp,
        )
