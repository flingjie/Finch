"""opencli Hacker News 只读封装 — 策略与 sources.policy 对齐。"""

from __future__ import annotations

import json
from typing import Any

from finch.github.gh_client import _run
from finch.sources.models import SourceCommandBlocked
from finch.sources.policy import check_allowlist as _shared_check_allowlist


class HackerNewsError(RuntimeError):
    """HN opencli 调用失败。"""


class HackerNewsCommandBlocked(HackerNewsError):
    """写命令或未允许命令被拒绝。"""


def _check_allowlist(argv: list[str]) -> None:
    try:
        _shared_check_allowlist(argv)
    except SourceCommandBlocked as exc:
        raise HackerNewsCommandBlocked(str(exc)) from exc


def _parse_rows(stdout: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise HackerNewsError(f"Invalid JSON from opencli: {exc}") from exc
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise HackerNewsError(f"Expected list from opencli, got {type(data).__name__}")
    return [item for item in data if isinstance(item, dict)]


def _call(argv: list[str], *, timeout: float = 60.0) -> list[dict[str, Any]]:
    _check_allowlist(argv)
    result = _run(argv, timeout=timeout)
    if not result.get("ok"):
        raise HackerNewsError(result.get("stderr") or "opencli hackernews failed")
    return _parse_rows(result.get("stdout") or "[]")


class HackerNewsOpenCliClient:
    """opencli hackernews 只读客户端。"""

    def search(self, query: str, *, sort: str = "date", limit: int = 20) -> list[dict[str, Any]]:
        argv = [
            "opencli",
            "hackernews",
            "search",
            query,
            "--sort",
            sort,
            "--limit",
            str(limit),
            "-f",
            "json",
        ]
        return _call(argv)

    def ask(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return _call(
            ["opencli", "hackernews", "ask", "--limit", str(limit), "-f", "json"]
        )

    def show(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return _call(
            ["opencli", "hackernews", "show", "--limit", str(limit), "-f", "json"]
        )

    def read(
        self, story_id: str, *, limit: int = 20, depth: int = 3
    ) -> list[dict[str, Any]]:
        """读取帖子与评论树；行内可含 parent / depth。"""
        return _call(
            [
                "opencli",
                "hackernews",
                "read",
                str(story_id),
                "--limit",
                str(limit),
                "--depth",
                str(depth),
                "-f",
                "json",
            ]
        )
