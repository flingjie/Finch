"""opencli 只读封装（spec 5.3）— 执行经 OpenCliGateway，策略与 sources.policy 对齐。"""

import json
import os

from finch.github.gh_client import _run
from finch.sources.models import ResultKind, SourceCommandBlocked
from finch.sources.opencli_gateway import OpenCliGateway
from finch.sources.policy import _ALLOW as _POLICY_ALLOW
from finch.sources.policy import _DENY as _POLICY_DENY
from finch.sources.policy import check_allowlist as _shared_check_allowlist

from .models import (
    Tweet,
    TwitterCommandBlocked,
    TwitterError,
    TwitterRateLimited,
    TwitterSourceUnavailable,
)

_ALLOWLIST: set[str] = set(_POLICY_ALLOW.get("twitter", frozenset()))
_DENYLIST: set[str] = set(_POLICY_DENY.get("twitter", frozenset()))


def _check_allowlist(argv: list[str]) -> None:
    """Defense in depth：委托共享策略，保留 TwitterCommandBlocked 类型。"""
    try:
        _shared_check_allowlist(argv)
    except SourceCommandBlocked as exc:
        raise TwitterCommandBlocked(str(exc)) from exc


def _parse_json(stdout: str) -> list:
    """解析 opencli JSON 并归一化为 list（dict → [dict]），校验必须是 list."""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise TwitterError(f"Invalid JSON from opencli: {exc}") from exc
    if not isinstance(data, list):
        if isinstance(data, dict):
            data = [data]
        else:
            raise TwitterError(f"Expected list from opencli, got {type(data).__name__}")
    return data


def _tolerant_tweets(items: list) -> list[Tweet]:
    """把原始 JSON 列表解析为 Tweet 列表；单条解析失败不中断整批."""
    tweets: list[Tweet] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            tweets.append(Tweet.model_validate(item))
        except Exception:  # noqa: BLE001
            continue
    return tweets


def _parse_tweets(stdout: str) -> list[Tweet]:
    """解析 opencli JSON 输出为 Tweet 列表."""
    return _tolerant_tweets(_parse_json(stdout))


def _browser_flags() -> list[str]:
    """opencli 浏览器通用选项：默认后台窗口 + 复用登录会话."""
    window = os.environ.get("OPENCLI_WINDOW", "background")
    return ["--window", window, "--site-session", "persistent"]


def _raise_for_result(result) -> None:
    """将 gateway ResultKind 映射为 Twitter 异常。"""
    if result.kind in {ResultKind.SUCCESS, ResultKind.EMPTY}:
        return
    stderr = result.stderr_summary or ""
    if result.kind == ResultKind.AUTH_REQUIRED:
        raise TwitterSourceUnavailable(f"Twitter not logged in: {stderr}")
    if result.kind == ResultKind.BRIDGE_DOWN:
        raise TwitterSourceUnavailable(f"Browser bridge unavailable: {stderr}")
    if "rate" in stderr.lower() or "too many" in stderr.lower():
        raise TwitterRateLimited(f"Rate limited: {stderr}")
    raise TwitterError(f"opencli failed (exit={result.exit_code}): {stderr}")


def _raw_json(argv: list[str], timeout: float = 60.0) -> list[dict]:
    """执行 opencli 命令并返回原始 JSON 列表（不做 Tweet 模型转换）。"""
    _check_allowlist(argv)
    result = OpenCliGateway(run_fn=_run).run_argv(
        [*argv, *_browser_flags()], timeout=timeout
    )
    _raise_for_result(result)
    if result.kind == ResultKind.EMPTY:
        return []
    return list(result.rows) if result.rows else _parse_json(result.stdout_raw or "[]")


def _call(argv: list[str], timeout: float = 60.0) -> list[Tweet]:
    """执行 opencli 命令并解析结果."""
    return _tolerant_tweets(_raw_json(argv, timeout))


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
            "opencli",
            "twitter",
            "search",
            query,
            "--product",
            product,
            "--limit",
            str(limit),
            "-f",
            "json",
        ]
        return _call(argv, timeout=60.0)

    def thread(self, url: str, *, limit: int = 50) -> list[Tweet]:
        """读取推文线程."""
        argv = [
            "opencli",
            "twitter",
            "thread",
            url,
            "--limit",
            str(limit),
            "-f",
            "json",
        ]
        return _call(argv, timeout=60.0)

    def bookmarks(self, *, limit: int = 20) -> list[Tweet]:
        """读取书签."""
        argv = [
            "opencli",
            "twitter",
            "bookmarks",
            "--limit",
            str(limit),
            "-f",
            "json",
        ]
        return _call(argv, timeout=60.0)

    def timeline(self, *, limit: int = 20) -> list[Tweet]:
        """读取时间线."""
        argv = [
            "opencli",
            "twitter",
            "timeline",
            "--limit",
            str(limit),
            "-f",
            "json",
        ]
        return _call(argv, timeout=60.0)

    def profile(self, username: str) -> Tweet | None:
        """读取用户资料（返回 profile 信息包装为 Tweet-like）."""
        argv = [
            "opencli",
            "twitter",
            "profile",
            username,
            "-f",
            "json",
        ]
        tweets = _call(argv, timeout=30.0)
        return tweets[0] if tweets else None

    def tweets(self, username: str, *, limit: int = 20) -> list[Tweet]:
        """读取用户近期推文（只读）。"""
        argv = [
            "opencli",
            "twitter",
            "tweets",
            username,
            "--limit",
            str(limit),
            "-f",
            "json",
        ]
        return _call(argv, timeout=60.0)

    def whoami(self) -> dict:
        """读取当前登录账号（stable user_id + handle）。"""
        argv = ["opencli", "twitter", "whoami", "-f", "json"]
        tweets = _call(argv, timeout=30.0)
        if not tweets:
            raise TwitterSourceUnavailable("whoami returned no data")
        t = tweets[0]
        return {"user_id": t.id, "handle": t.author}
