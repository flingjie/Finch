"""opencli Reddit 只读封装 — 执行经 OpenCliGateway，策略与 sources.policy 对齐。"""

import json
import os
import re

from pydantic import ValidationError

from finch.github.gh_client import _run
from finch.sources.models import ResultKind, SourceCommandBlocked
from finch.sources.opencli_gateway import OpenCliGateway
from finch.sources.policy import _ALLOW as _POLICY_ALLOW
from finch.sources.policy import _DENY as _POLICY_DENY
from finch.sources.policy import check_allowlist as _shared_check_allowlist

from .models import (
    RedditCommandBlocked,
    RedditError,
    RedditPost,
    RedditRateLimited,
    RedditSourceUnavailable,
)

_ALLOWLIST: set[str] = set(_POLICY_ALLOW.get("reddit", frozenset()))
_DENYLIST: set[str] = set(_POLICY_DENY.get("reddit", frozenset()))

# ``reddit read`` 单条正文/评论上限；默认 2000 会把长帖截成片段，截断会置 truncated。
_READ_MAX_LENGTH = 40000
_READ_TRUNCATION_MARKER = "[truncated]"
_POST_ID_RE = re.compile(r"/comments/([A-Za-z0-9]+)")


def _check_allowlist(argv: list[str]) -> None:
    """Defense in depth：委托共享策略，保留 RedditCommandBlocked 类型。"""
    try:
        _shared_check_allowlist(argv)
    except SourceCommandBlocked as exc:
        raise RedditCommandBlocked(str(exc)) from exc


def _parse_rows(stdout: str) -> list[dict]:
    """解析 opencli JSON 输出为原始行（不做字段校验）。"""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RedditError(f"Invalid JSON from opencli: {exc}") from exc
    if not isinstance(data, list):
        if isinstance(data, dict):
            data = [data]
        else:
            raise RedditError(f"Expected list from opencli, got {type(data).__name__}")
    return [item for item in data if isinstance(item, dict)]


def _parse_posts(stdout: str) -> list[RedditPost]:
    """解析 opencli ``search`` JSON 输出为 RedditPost 列表."""
    return _to_posts(_parse_rows(stdout))


def _to_posts(rows: list[dict]) -> list[RedditPost]:
    """逐行校验为 RedditPost；单条字段缺失不中断整批。"""
    posts: list[RedditPost] = []
    for row in rows:
        try:
            posts.append(RedditPost.model_validate(row))
        except ValidationError:
            continue
    return posts


def _row_text(row: dict) -> str:
    text = row.get("text")
    return text if isinstance(text, str) else ""


def _post_id_from_url(url: str) -> str:
    match = _POST_ID_RE.search(url)
    return match.group(1) if match else url


def _post_from_read_rows(rows: list[dict], url: str) -> RedditPost | None:
    """把 ``opencli reddit read`` 的行映射成 RedditPost。

    ``read`` 的输出与 ``search`` 不同：只有 ``type``/``author``/``score``/``text``，
    POST 行的 ``text`` 是「标题 + 空行 + 正文（+ 外链）」，评论行是 ``L0``/``L1``…，
    id 与 url 只能由请求 URL 推导。行结构再次漂移时抛 RedditError，不静默返回 None。
    """
    if not rows:
        return None
    post_row = next((row for row in rows if _row_type(row) == "POST"), None)
    if post_row is None:
        raise RedditError(
            f"opencli reddit read returned no POST row (got: "
            f"{sorted({_row_type(row) for row in rows})})",
            "REDDIT_READ_SHAPE_DRIFT",
        )
    lines = _row_text(post_row).strip().split("\n")
    title = lines[0].strip()
    body = "\n".join(lines[1:]).strip()
    comment_rows = [
        text
        for text in (_row_text(row).strip() for row in rows if _row_type(row) != "POST")
        if text
    ]
    return RedditPost(
        id=_post_id_from_url(url),
        title=title,
        author=str(post_row.get("author") or "[deleted]"),
        score=post_row.get("score") or 0,
        url=url,
        selftext=body,
        comments=len(comment_rows),
        comment_rows=comment_rows,
        truncated=any(_READ_TRUNCATION_MARKER in _row_text(row) for row in rows),
    )


def _row_type(row: dict) -> str:
    kind = row.get("type")
    return kind.upper() if isinstance(kind, str) else ""


def _browser_flags() -> list[str]:
    """opencli 浏览器通用选项：默认后台窗口 + 复用登录会话（可用 OPENCLI_WINDOW 覆盖）。"""
    window = os.environ.get("OPENCLI_WINDOW", "background")
    return ["--window", window, "--site-session", "persistent"]


def _raise_for_result(result) -> None:
    if result.kind in {ResultKind.SUCCESS, ResultKind.EMPTY}:
        return
    stderr = result.stderr_summary or ""
    if result.kind == ResultKind.AUTH_REQUIRED:
        raise RedditSourceUnavailable(f"Reddit not logged in: {stderr}")
    if result.kind == ResultKind.BRIDGE_DOWN:
        raise RedditSourceUnavailable(f"Browser bridge unavailable: {stderr}")
    if "rate" in stderr.lower() or "too many" in stderr.lower():
        raise RedditRateLimited(f"Rate limited: {stderr}")
    raise RedditError(f"opencli failed (exit={result.exit_code}): {stderr}")


def _call(argv: list[str], timeout: float = 60.0) -> list[dict]:
    """执行 opencli 命令，返回原始 JSON 行；字段映射由调用方决定。"""
    _check_allowlist(argv)
    result = OpenCliGateway(run_fn=_run).run_argv(
        [*argv, *_browser_flags()], timeout=timeout
    )
    _raise_for_result(result)
    if result.kind == ResultKind.EMPTY:
        return []
    if result.rows:
        return result.rows
    return _parse_rows(result.stdout_raw or "[]")


class RedditOpenCliClient:
    """opencli Reddit 只读客户端（search / subreddit / read）。"""

    def search(
        self, query: str, *, sort: str = "relevance", limit: int = 20
    ) -> list[RedditPost]:
        """搜索 Reddit 帖子."""
        argv = [
            "opencli", "reddit", "search",
            query,
            "--sort", sort,
            "--limit", str(limit),
            "-f", "json",
        ]
        return _to_posts(_call(argv, timeout=60.0))

    def subreddit(
        self, name: str, *, sort: str = "new", limit: int = 20
    ) -> list[RedditPost]:
        """指定社区列表（new / top / hot）。"""
        argv = [
            "opencli", "reddit", "subreddit",
            name.lstrip("r/"),
            "--sort", sort,
            "--limit", str(limit),
            "-f", "json",
        ]
        return _to_posts(_call(argv, timeout=60.0))

    def post(self, url: str) -> RedditPost | None:
        """按 URL 读取单帖正文与评论（只读 ``reddit read``）。

        无结果返回 None；行结构漂移或来源不可用抛 Reddit 异常。
        """
        argv = [
            "opencli", "reddit", "read", url,
            "-f", "json",
            "--max-length", str(_READ_MAX_LENGTH),
        ]
        return _post_from_read_rows(_call(argv, timeout=60.0), url)
