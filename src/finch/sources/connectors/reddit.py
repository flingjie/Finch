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
from finch.sources.plan_util import empty_capabilities, resolve_command

_VALID_SORTS = frozenset({"relevance", "new", "top", "hot", "comments", "comment"})


class RedditConnector:
    source = Source.REDDIT

    def probe(self, capabilities: OpenCliCapabilities) -> SourceStatus:
        if not capabilities.surfaces:
            return SourceStatus.DEGRADED
        if "reddit" not in capabilities.surfaces:
            return SourceStatus.UNAVAILABLE
        return SourceStatus.READY

    def plan(
        self,
        context: DiscoveryContext,
        capabilities: OpenCliCapabilities | None = None,
    ) -> list[OpenCliRequest]:
        caps = capabilities or empty_capabilities()
        reqs: list[OpenCliRequest] = []
        search_cmd = resolve_command(caps, "reddit", ["search"])
        subreddit_cmd = resolve_command(caps, "reddit", ["subreddit"])
        read_cmd = resolve_command(caps, "reddit", ["read", "thread"])
        sorts = _normalize_sorts(context.extra.get("sorts"))
        subreddits = [
            str(s).strip().lstrip("r/")
            for s in (context.extra.get("subreddits") or [])
            if str(s).strip()
        ]

        if search_cmd:
            for q in context.queries:
                for sort in sorts:
                    reqs.append(
                        OpenCliRequest(
                            surface="reddit",
                            command=search_cmd,
                            args=(
                                q,
                                "--sort",
                                sort,
                                "--limit",
                                str(context.limit),
                                "-f",
                                "json",
                            ),
                            timeout_seconds=60,
                        )
                    )

        if subreddit_cmd:
            seen_sub: set[tuple[str, str]] = set()
            for name in subreddits:
                for sort in sorts:
                    sort_arg = sort if sort in {"new", "top", "hot"} else "new"
                    key = (name, sort_arg)
                    if key in seen_sub:
                        continue
                    seen_sub.add(key)
                    reqs.append(
                        OpenCliRequest(
                            surface="reddit",
                            command=subreddit_cmd,
                            args=(
                                name,
                                "--sort",
                                sort_arg,
                                "--limit",
                                str(context.limit),
                                "-f",
                                "json",
                            ),
                            timeout_seconds=60,
                        )
                    )

        if read_cmd:
            for url in context.urls:
                reqs.append(
                    OpenCliRequest(
                        surface="reddit",
                        command=read_cmd,
                        args=(url, "-f", "json"),
                        timeout_seconds=60,
                    )
                )
        return reqs

    def expand_read_requests(
        self,
        artifacts: list[RawArtifact],
        *,
        max_expand: int,
        capabilities: OpenCliCapabilities | None = None,
    ) -> list[OpenCliRequest]:
        """对搜索结果 URL 做有界 ``reddit read``，拉取正文与评论上下文。"""
        if max_expand <= 0:
            return []
        caps = capabilities or empty_capabilities()
        read_cmd = resolve_command(caps, "reddit", ["read", "thread"])
        if not read_cmd:
            return []
        urls: list[str] = []
        for art in artifacts:
            url = (art.canonical_url or "").strip()
            if not url or url in urls:
                continue
            if "reddit.com" not in url and "redd.it" not in url:
                continue
            urls.append(url)
            if len(urls) >= max_expand:
                break
        return [
            OpenCliRequest(
                surface="reddit",
                command=read_cmd,
                args=(url, "-f", "json"),
                timeout_seconds=60,
            )
            for url in urls
        ]

    def normalize(self, result: OpenCliResult) -> list[RawArtifact]:
        now = datetime.now(UTC)
        out: list[RawArtifact] = []
        for row in result.rows:
            arts = self._rows_to_artifacts(row, now)
            out.extend(arts)
        return out

    def _rows_to_artifacts(self, row: dict[str, Any], now: datetime) -> list[RawArtifact]:
        row_type = str(row.get("type") or "").upper()
        if row_type in {"COMMENT", "L0", "L1", "L2"} or row.get("parent_id"):
            art = self._comment_artifact(row, now)
            return [art] if art is not None else []
        art = self._post_artifact(row, now)
        return [art] if art is not None else []

    def _post_artifact(self, row: dict[str, Any], now: datetime) -> RawArtifact | None:
        sid = str(row.get("id") or "").strip()
        if not sid:
            return None
        title = str(row.get("title") or "")
        text = str(row.get("selftext") or row.get("text") or "")
        # read 输出：text 常为「标题 + 空行 + 正文」
        if not title and text:
            lines = text.strip().split("\n")
            title = lines[0].strip()
            text = "\n".join(lines[1:]).strip()
        body = f"{title}\n\n{text}".strip()
        url = str(row.get("url") or row.get("permalink") or "")
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

    def _comment_artifact(self, row: dict[str, Any], now: datetime) -> RawArtifact | None:
        sid = str(row.get("id") or "").strip()
        text = str(row.get("text") or row.get("body") or "").strip()
        if not sid or not text:
            return None
        parent = str(row.get("parent_id") or row.get("parent") or "")
        depth = int(row.get("depth") or 0)
        if not depth:
            row_type = str(row.get("type") or "")
            if row_type.upper().startswith("L") and row_type[1:].isdigit():
                depth = int(row_type[1:])
        author = str(row.get("author") or "")
        url = str(row.get("url") or "")
        body = f"[parent={parent} depth={depth}]\n{text}" if parent else text
        fp = content_fingerprint(body, url=url)
        return RawArtifact(
            artifact_id=artifact_id("reddit", "comment", sid),
            source=Source.REDDIT,
            source_type="comment",
            source_id=sid,
            canonical_url=url,
            author_identity=AuthorIdentity(
                platform="reddit", external_id=author, handle=author
            ),
            title=None,
            text=body,
            published_at=None,
            metrics={
                "parent_id": parent,
                "depth": depth,
                "score": row.get("score") or 0,
            },
            retrieved_at=now,
            capture_method="adapter",
            content_fingerprint=fp,
        )


def _normalize_sorts(raw: Any) -> list[str]:
    if not raw:
        return ["relevance"]
    out: list[str] = []
    for item in raw:
        sort = str(item).strip().lower()
        if sort == "comment":
            sort = "comments"
        if sort in _VALID_SORTS and sort not in out:
            out.append(sort)
    return out or ["relevance"]
