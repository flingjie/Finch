"""Hacker News Source Connector（opencli hackernews；公开 API，无需浏览器）。"""

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

_ENTRANCE_COMMANDS = ("search", "ask", "show", "new", "top")


class HackerNewsConnector:
    source = Source.HACKERNEWS

    def probe(self, capabilities: OpenCliCapabilities) -> SourceStatus:
        if not capabilities.surfaces:
            return SourceStatus.DEGRADED
        if "hackernews" not in capabilities.surfaces:
            return SourceStatus.UNAVAILABLE
        return SourceStatus.READY

    def plan(
        self,
        context: DiscoveryContext,
        capabilities: OpenCliCapabilities | None = None,
    ) -> list[OpenCliRequest]:
        caps = capabilities or empty_capabilities()
        reqs: list[OpenCliRequest] = []
        entrances = list(context.extra.get("entrances") or ["search"])
        search_cmd = resolve_command(caps, "hackernews", ["search"])
        if search_cmd and context.queries:
            for q in context.queries:
                if "search" in entrances or not entrances:
                    reqs.append(
                        OpenCliRequest(
                            surface="hackernews",
                            command=search_cmd,
                            args=(q, "--limit", str(context.limit), "-f", "json"),
                            timeout_seconds=60,
                            use_browser_lock=False,
                        )
                    )
        for entrance in entrances:
            if entrance == "search":
                continue
            if entrance not in _ENTRANCE_COMMANDS:
                continue
            cmd = resolve_command(caps, "hackernews", [entrance])
            if not cmd:
                continue
            reqs.append(
                OpenCliRequest(
                    surface="hackernews",
                    command=cmd,
                    args=("--limit", str(context.limit), "-f", "json"),
                    timeout_seconds=60,
                    use_browser_lock=False,
                )
            )
        read_cmd = resolve_command(caps, "hackernews", ["read"])
        if read_cmd:
            for url_or_id in context.urls:
                sid = _story_id(url_or_id)
                if not sid:
                    continue
                reqs.append(
                    OpenCliRequest(
                        surface="hackernews",
                        command=read_cmd,
                        args=(sid, "--limit", str(context.limit), "--depth", "3", "-f", "json"),
                        timeout_seconds=60,
                        use_browser_lock=False,
                    )
                )
        return reqs

    def normalize(self, result: OpenCliResult) -> list[RawArtifact]:
        now = datetime.now(UTC)
        out: list[RawArtifact] = []
        for row in result.rows:
            for art in self._rows_to_artifacts(row, now):
                out.append(art)
        return out

    def _rows_to_artifacts(self, row: dict[str, Any], now: datetime) -> list[RawArtifact]:
        """单行可能是 story，也可能是带 comments 嵌套的 read 结果。"""
        arts: list[RawArtifact] = []
        story = self._story_artifact(row, now)
        if story is not None:
            arts.append(story)
        comments = row.get("comments") or row.get("children") or []
        if isinstance(comments, list):
            parent_id = str(row.get("id") or story.source_id if story else "")
            arts.extend(self._flatten_comments(comments, now, default_parent=parent_id))
        # Flat comment row (type=comment / parent present).
        if story is None and (
            row.get("parent") is not None
            or str(row.get("type") or "").lower() in {"comment", "l0", "l1"}
        ):
            comment = self._comment_artifact(row, now)
            if comment is not None:
                arts.append(comment)
        return arts

    def _flatten_comments(
        self,
        comments: list[Any],
        now: datetime,
        *,
        default_parent: str,
        depth: int = 1,
    ) -> list[RawArtifact]:
        out: list[RawArtifact] = []
        for item in comments:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row.setdefault("parent", default_parent)
            row.setdefault("depth", depth)
            art = self._comment_artifact(row, now)
            if art is not None:
                out.append(art)
            kids = item.get("comments") or item.get("children") or []
            if isinstance(kids, list) and kids:
                sid = str(item.get("id") or art.source_id if art else default_parent)
                out.extend(
                    self._flatten_comments(
                        kids, now, default_parent=sid, depth=depth + 1
                    )
                )
        return out

    def _story_artifact(self, row: dict[str, Any], now: datetime) -> RawArtifact | None:
        if str(row.get("type") or "").lower() in {"comment", "l0", "l1"}:
            return None
        if row.get("parent") is not None and not row.get("title"):
            return None
        sid = str(row.get("id") or "").strip()
        if not sid:
            return None
        title = str(row.get("title") or "")
        text = str(row.get("text") or row.get("body") or row.get("content") or "")
        body = f"{title}\n\n{text}".strip() if title else text.strip()
        if not body:
            return None
        url = str(
            row.get("url")
            or row.get("canonical_url")
            or f"https://news.ycombinator.com/item?id={sid}"
        )
        author = str(row.get("author") or row.get("by") or row.get("user") or "")
        fp = content_fingerprint(body, url=url)
        return RawArtifact(
            artifact_id=artifact_id("hackernews", "story", sid),
            source=Source.HACKERNEWS,
            source_type="story",
            source_id=sid,
            canonical_url=url,
            author_identity=AuthorIdentity(
                platform="hackernews", external_id=author, handle=author
            ),
            title=title or None,
            text=body,
            published_at=_parse_ts(row.get("created_at") or row.get("time")),
            metrics={
                "score": row.get("score") or row.get("points") or 0,
                "comments": row.get("descendants") or row.get("comments_count") or 0,
            },
            retrieved_at=now,
            capture_method="adapter",
            content_fingerprint=fp,
        )

    def _comment_artifact(self, row: dict[str, Any], now: datetime) -> RawArtifact | None:
        sid = str(row.get("id") or "").strip()
        text = str(row.get("text") or row.get("body") or row.get("content") or "").strip()
        if not sid or not text:
            return None
        parent = str(row.get("parent") or row.get("parent_id") or "")
        depth = int(row.get("depth") or 0)
        url = str(
            row.get("url") or f"https://news.ycombinator.com/item?id={sid}"
        )
        author = str(row.get("author") or row.get("by") or row.get("user") or "")
        # Preserve parent/child in text prefix for evidence quoting + metrics.
        body = f"[parent={parent} depth={depth}]\n{text}" if parent else text
        fp = content_fingerprint(body, url=url)
        return RawArtifact(
            artifact_id=artifact_id("hackernews", "comment", sid),
            source=Source.HACKERNEWS,
            source_type="comment",
            source_id=sid,
            canonical_url=url,
            author_identity=AuthorIdentity(
                platform="hackernews", external_id=author, handle=author
            ),
            title=None,
            text=body,
            published_at=_parse_ts(row.get("created_at") or row.get("time")),
            metrics={"parent_id": parent, "depth": depth, "score": row.get("score") or 0},
            retrieved_at=now,
            capture_method="adapter",
            content_fingerprint=fp,
        )


def _story_id(url_or_id: str) -> str:
    value = (url_or_id or "").strip()
    if not value:
        return ""
    if value.isdigit():
        return value
    marker = "item?id="
    if marker in value:
        return value.split(marker, 1)[1].split("&", 1)[0].strip()
    return value


def _parse_ts(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (ValueError, OSError):
            return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
