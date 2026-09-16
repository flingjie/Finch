"""opencli Reddit 只读封装 — 执行经 OpenCliGateway，策略与 sources.policy 对齐。"""

import json
import os

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


def _check_allowlist(argv: list[str]) -> None:
    """Defense in depth：委托共享策略，保留 RedditCommandBlocked 类型。"""
    try:
        _shared_check_allowlist(argv)
    except SourceCommandBlocked as exc:
        raise RedditCommandBlocked(str(exc)) from exc


def _parse_posts(stdout: str) -> list[RedditPost]:
    """解析 opencli JSON 输出为 RedditPost 列表."""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RedditError(f"Invalid JSON from opencli: {exc}") from exc
    if not isinstance(data, list):
        if isinstance(data, dict):
            data = [data]
        else:
            raise RedditError(f"Expected list from opencli, got {type(data).__name__}")
    posts: list[RedditPost] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            posts.append(RedditPost.model_validate(item))
        except Exception:  # noqa: BLE001 - 单条解析失败不中断整批
            continue
    return posts


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


def _call(argv: list[str], timeout: float = 60.0) -> list[RedditPost]:
    """执行 opencli 命令并解析结果."""
    _check_allowlist(argv)
    result = OpenCliGateway(run_fn=_run).run_argv(
        [*argv, *_browser_flags()], timeout=timeout
    )
    _raise_for_result(result)
    if result.kind == ResultKind.EMPTY:
        return []
    if result.rows:
        posts: list[RedditPost] = []
        for item in result.rows:
            try:
                posts.append(RedditPost.model_validate(item))
            except Exception:  # noqa: BLE001
                continue
        return posts
    return _parse_posts(result.stdout_raw or "[]")


class RedditOpenCliClient:
    """opencli Reddit 只读客户端（仅 search，见 spec §1）。"""

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
        return _call(argv, timeout=60.0)

    def post(self, url: str) -> RedditPost | None:
        """按 URL 读取单帖（只读 ``reddit read``），失败返回 None（或抛来源异常）。"""
        argv = ["opencli", "reddit", "read", url, "-f", "json"]
        posts = _call(argv, timeout=60.0)
        return posts[0] if posts else None
