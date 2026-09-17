"""跨平台 OpenCLI 只读允许/拒绝列表。

写命令与未知命令在进入子进程前被拒绝。各 surface 的 allow/deny 合并于此，
Twitter/Reddit 客户端可委托本模块，避免策略漂移。
"""

from __future__ import annotations

from finch.sources.models import SourceCommandBlocked, command_prefix

# surface -> frozenset of "surface command" keys
_ALLOW: dict[str, frozenset[str]] = {
    "twitter": frozenset(
        {
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
    ),
    "reddit": frozenset(
        {
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
    ),
    "v2ex": frozenset(
        {
            "v2ex topic",
            "v2ex member",
            "v2ex node",
            "v2ex latest",
            "v2ex hot",
            "v2ex search",
            "v2ex replies",
        }
    ),
    "weixin": frozenset(
        {
            "weixin download",
            "weixin read",
            "weixin article",
        }
    ),
    "web": frozenset(
        {
            "web read",
        }
    ),
    "xiaohongshu": frozenset(
        {
            "xiaohongshu search",
            "xiaohongshu feed",
            "xiaohongshu user",
            "xiaohongshu note",
            "xiaohongshu creator-notes",
            "xiaohongshu creator-profile",
        }
    ),
    # Meta / discovery commands (no write surface).
    "meta": frozenset(
        {
            "meta doctor",
            "meta list",
            "meta version",
            "meta help",
        }
    ),
}

_DENY: dict[str, frozenset[str]] = {
    "twitter": frozenset(
        {
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
    ),
    "reddit": frozenset(
        {
            "reddit comment",
            "reddit reply",
            "reddit save",
            "reddit upvote",
            "reddit subscribe",
            "reddit login",
        }
    ),
    "v2ex": frozenset(
        {
            "v2ex reply",
            "v2ex post",
            "v2ex create",
            "v2ex thank",
            "v2ex login",
        }
    ),
    "weixin": frozenset(
        {
            "weixin publish",
            "weixin post",
            "weixin send",
            "weixin login",
        }
    ),
    "xiaohongshu": frozenset(
        {
            "xiaohongshu post",
            "xiaohongshu reply",
            "xiaohongshu comment",
            "xiaohongshu like",
            "xiaohongshu follow",
            "xiaohongshu publish",
            "xiaohongshu login",
        }
    ),
}

# Verbs that are never allowed regardless of surface registration.
_GLOBAL_WRITE_VERBS = frozenset(
    {
        "post",
        "reply",
        "quote",
        "like",
        "unlike",
        "retweet",
        "unretweet",
        "follow",
        "unfollow",
        "comment",
        "upvote",
        "subscribe",
        "publish",
        "send",
        "delete",
        "create",
        "login",
        "block",
        "unblock",
        "bookmark",
        "unbookmark",
        "thank",
        "click",
        "type",
        "fill",
    }
)


def _prefix_from_argv(argv: list[str]) -> tuple[str, str]:
    """从 ``['opencli', (global flags), surface, command, ...]`` 提取 (surface, command)。"""
    if len(argv) < 2:
        raise SourceCommandBlocked("Command too short")
    if argv[0] != "opencli":
        raise SourceCommandBlocked(f"Expected opencli binary, got {argv[0]!r}")
    idx = 1
    while idx < len(argv) and argv[idx] == "--profile" and idx + 1 < len(argv):
        idx += 2
    if idx >= len(argv):
        raise SourceCommandBlocked("Command too short")
    token = argv[idx]
    # opencli doctor / opencli list / opencli --version
    if idx == len(argv) - 1 or token in {"doctor", "list", "--version", "help", "-h"}:
        cmd = token.lstrip("-")
        if cmd == "version" or token == "--version":
            return "meta", "version"
        return "meta", cmd if cmd != "h" else "help"
    surface = token
    command = argv[idx + 1] if idx + 1 < len(argv) else ""
    return surface, command


def check_allowlist(argv: list[str]) -> None:
    """Defense in depth：denylist 优先，再 allowlist；未知命令拒绝。"""
    surface, command = _prefix_from_argv(argv)
    if command in _GLOBAL_WRITE_VERBS:
        raise SourceCommandBlocked(
            f"Command blocked by policy: {command_prefix(surface, command)}"
        )
    key = command_prefix(surface, command)
    deny = _DENY.get(surface, frozenset())
    if key in deny:
        raise SourceCommandBlocked(f"Command blocked by policy: {key}")
    # Meta commands always allowed when listed.
    if surface == "meta":
        allow_meta = _ALLOW["meta"]
        if key not in allow_meta:
            raise SourceCommandBlocked(f"Command not in allowlist: {key}")
        return
    allow = _ALLOW.get(surface)
    if allow is None:
        # Unknown surface: block unless explicitly opened later via capability probe
        # for read-only browser exploration (Phase 1+). Hard-block writes already
        # caught by global verbs; unknown surface still blocked here.
        raise SourceCommandBlocked(f"Unknown surface not in allowlist: {key}")
    if key not in allow:
        raise SourceCommandBlocked(f"Command not in allowlist: {key}")


def is_command_allowed(surface: str, command: str) -> bool:
    """查询 (surface, command) 是否在允许列表中。"""
    try:
        check_allowlist(["opencli", surface, command])
        return True
    except SourceCommandBlocked:
        return False


def allowed_commands_for(surface: str) -> frozenset[str]:
    """返回某 surface 的允许命令集合。"""
    return _ALLOW.get(surface, frozenset())
