"""OpenCliGateway：统一执行、退出码映射、只读门禁、日志脱敏。"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

from finch.github.gh_client import _run
from finch.opencli import run_opencli
from finch.sources.models import (
    OpenCliCapabilities,
    OpenCliRequest,
    OpenCliResult,
    ResultKind,
    SourceCommandBlocked,
    map_exit_code,
)
from finch.sources.policy import check_allowlist

_REDACT_PATTERNS = (
    re.compile(r"(?i)((?:cookie|authorization|token|api[_-]?key)\s*[:=]\s*)\S+"),
    re.compile(r"(?i)(bearer\s+)[a-z0-9._\-]+"),
)


def redact_text(text: str | None, *, max_len: int = 500) -> str | None:
    """脱敏并截断 stderr/日志摘要，避免写入 Cookie/Token。"""
    if text is None:
        return None
    out = text
    for pat in _REDACT_PATTERNS:
        out = pat.sub(r"\1[REDACTED]", out)
    out = out.strip()
    if len(out) > max_len:
        out = out[: max_len - 3] + "..."
    return out or None


def _parse_rows(stdout: str) -> list[dict[str, Any]]:
    if not stdout or not stdout.strip():
        return []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


class OpenCliGateway:
    """程序以参数数组调用 opencli；默认仅 read；浏览器命令走进程锁。"""

    def __init__(
        self,
        *,
        run_fn: Callable[[list[str], float], dict] | None = None,
        profile: str | None = None,
        default_timeout: int = 60,
        capability_snapshot_id: str = "",
    ) -> None:
        self._run_fn = run_fn or _run
        self.profile = profile
        self.default_timeout = default_timeout
        self.capability_snapshot_id = capability_snapshot_id

    def capabilities(self) -> OpenCliCapabilities:
        """拉取 ``opencli list -f json``；失败时返回空快照。"""
        from finch.sources.capabilities import fetch_capabilities

        caps = fetch_capabilities(run_fn=self._run_fn, profile=self.profile)
        self.capability_snapshot_id = caps.snapshot_id
        return caps

    def run(self, request: OpenCliRequest) -> OpenCliResult:
        """执行一次只读请求；写命令在此之前抛 SourceCommandBlocked。"""
        if request.access != "read":
            raise SourceCommandBlocked("Only read access is permitted")
        argv = self._build_argv(request)
        check_allowlist(argv)
        timeout = float(request.timeout_seconds or self.default_timeout)
        started = time.monotonic()
        if request.use_browser_lock:
            raw = run_opencli(argv, run_fn=self._run_fn, timeout=timeout)
        else:
            raw = self._run_fn(argv, timeout)
        duration_ms = int((time.monotonic() - started) * 1000)
        exit_code = raw.get("exit_code")
        stderr = raw.get("stderr") or ""
        stdout = raw.get("stdout") or ""
        kind = map_exit_code(exit_code, stderr=stderr)
        rows = _parse_rows(stdout) if kind in {ResultKind.SUCCESS, ResultKind.EMPTY} else []
        if kind == ResultKind.SUCCESS and not rows and not stdout.strip():
            kind = ResultKind.EMPTY
        return OpenCliResult(
            rows=rows,
            exit_code=exit_code if isinstance(exit_code, int) else None,
            kind=kind,
            duration_ms=duration_ms,
            stderr_summary=redact_text(stderr),
            capability_snapshot_id=self.capability_snapshot_id,
            stdout_raw=stdout if kind == ResultKind.SUCCESS else "",
        )

    def run_argv(
        self,
        argv: list[str],
        *,
        timeout: float = 60.0,
        use_browser_lock: bool = True,
    ) -> OpenCliResult:
        """兼容现有 twitter/reddit 客户端：直接传入完整 argv。"""
        if len(argv) < 2 or argv[0] != "opencli":
            raise SourceCommandBlocked(f"Invalid opencli argv: {argv!r}")
        # doctor / list / --version
        if argv[1] in {"doctor", "list", "--version", "help", "-h"} or argv[1].startswith("-"):
            surface, command = "meta", argv[1].lstrip("-") or "help"
            if argv[1] == "--version":
                command = "version"
            args = tuple(argv[2:])
        else:
            surface = argv[1]
            command = argv[2] if len(argv) > 2 else ""
            args = tuple(argv[3:])
        return self.run(
            OpenCliRequest(
                surface=surface,
                command=command,
                args=args,
                profile=self.profile,
                timeout_seconds=int(timeout),
                use_browser_lock=use_browser_lock,
            )
        )

    def _build_argv(self, request: OpenCliRequest) -> list[str]:
        if request.surface == "meta":
            if request.command == "version":
                return ["opencli", "--version"]
            argv = ["opencli", request.command, *request.args]
        else:
            argv = ["opencli", request.surface, request.command, *request.args]
        if self.profile and "--profile" not in argv:
            argv = [*argv, "--profile", self.profile]
        return argv
