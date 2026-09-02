"""opencli 只读封装（spec 5.3）"""

import json

from finch.github.gh_client import _run

from .models import (
    Tweet,
    TwitterCommandBlocked,
    TwitterError,
    TwitterRateLimited,
    TwitterSourceUnavailable,
)

# spec 5.3: 允许的只读命令前缀（发现后更新为 19 个 read 命令）
_ALLOWLIST: set[str] = {
    "twitter search",
    "twitter thread",
    "twitter bookmarks",
    "twitter bookmark-folders",
    "twitter bookmark-folder",
    "twitter timeline",
    "twitter tweets",
    "twitter likes",
    "twitter profile",
    "twitter whoami",
    "twitter notifications",
    "twitter trending",
    "twitter followers",
    "twitter following",
    "twitter list-tweets",
    "twitter lists",
    "twitter article",
    "twitter download",
    "twitter device-follow",
}

# spec 5.3: 明确阻断的写命令（26 个 write 命令）
_DENYLIST: set[str] = {
    "twitter post",
    "twitter reply",
    "twitter quote",
    "twitter like",
    "twitter unlike",
    "twitter retweet",
    "twitter unretweet",
    "twitter follow",
    "twitter unfollow",
    "twitter follow-batch",
    "twitter block",
    "twitter unblock",
    "twitter bookmark",
    "twitter unbookmark",
    "twitter delete",
    "twitter hide-reply",
    "twitter login",
    "twitter reply-dm",
    "twitter accept",
    "twitter list-create",
    "twitter list-delete",
    "twitter list-add",
    "twitter list-add-batch",
    "twitter list-remove",
    "twitter list-remove-batch",
}


def _check_allowlist(argv: list[str]) -> None:
    """Defense in depth：检查命令前缀是否在 allowlist 中且不在 denylist 中."""
    twitter_cmd = " ".join(argv[1:3])
    if twitter_cmd in _DENYLIST:
        raise TwitterCommandBlocked(f"Command blocked by policy: {twitter_cmd}")
    if twitter_cmd not in _ALLOWLIST:
        raise TwitterCommandBlocked(f"Command not in allowlist: {twitter_cmd}")


def _parse_tweets(stdout: str) -> list[Tweet]:
    """解析 opencli JSON 输出为 Tweet 列表."""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise TwitterError(f"Invalid JSON from opencli: {exc}") from exc
    if not isinstance(data, list):
        # 某些命令可能返回单条
        if isinstance(data, dict):
            data = [data]
        else:
            raise TwitterError(f"Expected list from opencli, got {type(data).__name__}")
    tweets: list[Tweet] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            tweets.append(Tweet.model_validate(item))
        except Exception:  # noqa: BLE001
            # 单条解析失败不中断整批
            continue
    return tweets


def _call(argv: list[str], timeout: float = 60.0) -> list[Tweet]:
    """执行 opencli 命令并解析结果."""
    _check_allowlist(argv)
    r = _run(argv, timeout=timeout)
    if not r["ok"]:
        stderr = (r["stderr"] or "").strip()
        # spec 5.3: Bridge 离线/未登录/限流检测
        if "not logged in" in stderr.lower() or "login" in stderr.lower():
            raise TwitterSourceUnavailable(f"Twitter not logged in: {stderr}")
        if "bridge" in stderr.lower() or "daemon" in stderr.lower():
            raise TwitterSourceUnavailable(f"Browser bridge unavailable: {stderr}")
        if "rate" in stderr.lower() or "too many" in stderr.lower():
            raise TwitterRateLimited(f"Rate limited: {stderr}")
        raise TwitterError(f"opencli failed (exit={r['exit_code']}): {stderr}")
    return _parse_tweets(r["stdout"])


class OpenCliClient:
    """opencli Twitter 只读客户端."""

    def version(self) -> str:
        r = _run(["opencli", "--version"], timeout=10.0)
        return r["stdout"].strip() if r["ok"] else ""

    def doctor(self) -> dict:
        r = _run(["opencli", "doctor"], timeout=30.0)
        detail = (r["stderr"] or r["stdout"]).strip()
        return {"ok": r["ok"], "exit_code": r["exit_code"], "detail": detail}

    def search(self, query: str, *, product: str = "top", limit: int = 20) -> list[Tweet]:
        """搜索推文（spec 5.3）."""
        argv = [
            "opencli", "twitter", "search",
            query,
            "--product", product,
            "--limit", str(limit),
            "-f", "json",
        ]
        return _call(argv, timeout=60.0)

    def thread(self, url: str, *, limit: int = 50) -> list[Tweet]:
        """读取推文线程."""
        argv = [
            "opencli", "twitter", "thread",
            url,
            "--limit", str(limit),
            "-f", "json",
        ]
        return _call(argv, timeout=60.0)

    def bookmarks(self, *, limit: int = 20) -> list[Tweet]:
        """读取书签."""
        argv = [
            "opencli", "twitter", "bookmarks",
            "--limit", str(limit),
            "-f", "json",
        ]
        return _call(argv, timeout=60.0)

    def timeline(self, *, limit: int = 20) -> list[Tweet]:
        """读取时间线."""
        argv = [
            "opencli", "twitter", "timeline",
            "--limit", str(limit),
            "-f", "json",
        ]
        return _call(argv, timeout=60.0)

    def profile(self, username: str) -> Tweet | None:
        """读取用户资料（返回 profile 信息包装为 Tweet-like）."""
        argv = [
            "opencli", "twitter", "profile",
            username,
            "-f", "json",
        ]
        tweets = _call(argv, timeout=30.0)
        return tweets[0] if tweets else None
