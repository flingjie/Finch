"""Twitter/X Source Connector（存储身份 platform 仍为 x）。"""

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


class TwitterConnector:
    source = Source.TWITTER

    def probe(self, capabilities: OpenCliCapabilities) -> SourceStatus:
        cmds = set(capabilities.surfaces.get("twitter", []))
        if not capabilities.surfaces:
            return SourceStatus.DEGRADED
        if "twitter" not in capabilities.surfaces:
            return SourceStatus.UNAVAILABLE
        if {"search", "profile"} & cmds or any("search" in c for c in cmds):
            return SourceStatus.READY
        return SourceStatus.DEGRADED

    def plan(
        self,
        context: DiscoveryContext,
        capabilities: OpenCliCapabilities | None = None,
    ) -> list[OpenCliRequest]:
        caps = capabilities or empty_capabilities()
        reqs: list[OpenCliRequest] = []
        search_cmd = resolve_command(caps, "twitter", ["search"])
        thread_cmd = resolve_command(caps, "twitter", ["thread", "read"])
        if search_cmd:
            for q in context.queries:
                reqs.append(
                    OpenCliRequest(
                        surface="twitter",
                        command=search_cmd,
                        args=(q, "--limit", str(context.limit), "-f", "json"),
                        timeout_seconds=60,
                    )
                )
        if thread_cmd:
            for url in context.urls:
                reqs.append(
                    OpenCliRequest(
                        surface="twitter",
                        command=thread_cmd,
                        args=(url, "--limit", str(context.limit), "-f", "json"),
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
        text = str(row.get("text") or "")
        url = str(row.get("url") or "")
        author = str(row.get("author") or "")
        fp = content_fingerprint(text, url=url)
        published = None
        created = row.get("created_at")
        if isinstance(created, str) and created:
            try:
                published = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y")
            except ValueError:
                published = None
        return RawArtifact(
            artifact_id=artifact_id("twitter", "post", sid),
            source=Source.TWITTER,
            source_type="post",
            source_id=sid,
            canonical_url=url,
            author_identity=AuthorIdentity(
                platform="x", external_id=author, handle=author
            ),
            title=None,
            text=text,
            published_at=published,
            metrics={
                "likes": row.get("likes") or 0,
                "views": row.get("views") or 0,
            },
            retrieved_at=now,
            capture_method="adapter",
            content_fingerprint=fp,
        )
