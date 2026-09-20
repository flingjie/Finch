"""V2EX Source Connector（能力探测驱动；字段缺失时降级映射）。"""

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


class V2exConnector:
    source = Source.V2EX

    def probe(self, capabilities: OpenCliCapabilities) -> SourceStatus:
        if not capabilities.surfaces:
            return SourceStatus.DEGRADED
        if "v2ex" not in capabilities.surfaces:
            return SourceStatus.UNAVAILABLE
        return SourceStatus.READY

    def plan(
        self,
        context: DiscoveryContext,
        capabilities: OpenCliCapabilities | None = None,
    ) -> list[OpenCliRequest]:
        caps = capabilities or empty_capabilities()
        if context.mode == "hot":
            hot_cmd = resolve_command(caps, "v2ex", ["hot", "topics"])
            if not hot_cmd:
                return []
            return [
                OpenCliRequest(
                    surface="v2ex",
                    command=hot_cmd,
                    args=("-f", "json"),
                    timeout_seconds=60,
                )
            ]
        if context.queries:
            search_cmd = resolve_command(caps, "v2ex", ["search"])
            if search_cmd:
                return [
                    OpenCliRequest(
                        surface="v2ex",
                        command=search_cmd,
                        args=(q, "-f", "json"),
                        timeout_seconds=60,
                    )
                    for q in context.queries
                ]
            # The current V2EX adapter has no keyword search command. Falling
            # back to the hot list keeps query-mode sources useful instead of
            # reporting a false "no matching commands" failure.
            hot_cmd = resolve_command(caps, "v2ex", ["hot", "topics"])
            if not hot_cmd:
                return []
            return [
                OpenCliRequest(
                    surface="v2ex",
                    command=hot_cmd,
                    args=("-f", "json"),
                    timeout_seconds=60,
                )
            ]
        return []

    def normalize(self, result: OpenCliResult) -> list[RawArtifact]:
        now = datetime.now(UTC)
        out: list[RawArtifact] = []
        for row in result.rows:
            art = self._row_to_artifact(row, now)
            if art is not None:
                out.append(art)
        return out

    def _row_to_artifact(self, row: dict[str, Any], now: datetime) -> RawArtifact | None:
        sid = str(row.get("id") or row.get("topic_id") or "").strip()
        if not sid:
            return None
        title = str(row.get("title") or "")
        text = str(row.get("content") or row.get("text") or row.get("content_rendered") or "")
        body = f"{title}\n\n{text}".strip()
        url = str(row.get("url") or row.get("canonical_url") or "")
        member = row.get("member") if isinstance(row.get("member"), dict) else {}
        assert isinstance(member, dict)
        handle = str(
            row.get("author")
            or row.get("username")
            or member.get("username")
            or member.get("name")
            or ""
        )
        fp = content_fingerprint(body, url=url)
        return RawArtifact(
            artifact_id=artifact_id("v2ex", "topic", sid),
            source=Source.V2EX,
            source_type="topic",
            source_id=sid,
            canonical_url=url,
            author_identity=AuthorIdentity(
                platform="v2ex", external_id=handle, handle=handle
            ),
            title=title or None,
            text=body,
            published_at=None,
            metrics={"replies": row.get("replies") or 0},
            retrieved_at=now,
            capture_method="adapter",
            content_fingerprint=fp,
        )
