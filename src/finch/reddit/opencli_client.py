"""opencli Reddit 只读封装（spec 2026-09-05）。"""

import json
import os

from finch.github.gh_client import _run

from .models import (
    RedditCommandBlocked,
    RedditError,
    RedditPost,
    RedditRateLimited,
    RedditSourceUnavailable,
)

# 允许的只读命令前缀
_ALLOWLIST: set[str] = {
    "reddit search",
    "reddit hot",
    "reddit subreddit",
    "reddit frontpage",
    "reddit home",
    "reddit popular",
    "reddit user",
    "reddit read",
    "reddit saved",
    "reddit upvoted",
    "reddit subscribed",
    "reddit subreddit-info",
    "reddit whoami",
    "reddit user-posts",
    "reddit user-comments",
}

# 明确阻断的写命令
_DENYLIST: set[str] = {
    "reddit comment",
    "reddit reply",
    "reddit save",
    "reddit upvote",
    "reddit subscribe",
    "reddit login",
}


def _check_allowlist(argv: list[str]) -> None:
    """Defense in depth：检查命令前缀是否在 allowlist 中且不在 denylist 中."""
    reddit_cmd = " ".join(argv[1:3])
    if reddit_cmd in _DENYLIST:
        raise RedditCommandBlocked(f"Command blocked by policy: {reddit_cmd}")
    if reddit_cmd not in _ALLOWLIST:
        raise RedditCommandBlocked(f"Command not in allowlist: {reddit_cmd}")


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


def _call(argv: list[str], timeout: float = 60.0) -> list[RedditPost]:
    """执行 opencli 命令并解析结果."""
    _check_allowlist(argv)
    r = _run([*argv, *_browser_flags()], timeout=timeout)
    if not r["ok"]:
        stderr = (r["stderr"] or "").strip()
        if "not logged in" in stderr.lower() or "login" in stderr.lower():
            raise RedditSourceUnavailable(f"Reddit not logged in: {stderr}")
        if "bridge" in stderr.lower() or "daemon" in stderr.lower():
            raise RedditSourceUnavailable(f"Browser bridge unavailable: {stderr}")
        if "rate" in stderr.lower() or "too many" in stderr.lower():
            raise RedditRateLimited(f"Rate limited: {stderr}")
        raise RedditError(f"opencli failed (exit={r['exit_code']}): {stderr}")
    return _parse_posts(r["stdout"])


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
