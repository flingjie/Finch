"""OpenCLI 能力快照：``opencli list -f json``。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from finch.github.gh_client import _run
from finch.sources.models import OpenCliCapabilities


def _snapshot_id(payload: str) -> str:
    return "caps_" + hashlib.sha256(payload.encode()).hexdigest()[:12]


def parse_capability_list(data: Any) -> dict[str, list[str]]:
    """将 ``opencli list`` JSON 归一化为 surface → command names。"""
    surfaces: dict[str, list[str]] = {}
    rows: list[Any]
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        # Some builds nest under "commands" / "sites"
        rows = data.get("commands") or data.get("sites") or data.get("adapters") or [data]
    else:
        return surfaces

    for row in rows:
        if not isinstance(row, dict):
            continue
        surface = (
            row.get("site")
            or row.get("surface")
            or row.get("name")
            or row.get("adapter")
            or ""
        )
        surface = str(surface).strip().lower()
        if not surface:
            continue
        cmds: list[str] = []
        raw_cmds = row.get("commands") or row.get("cmds") or []
        if isinstance(raw_cmds, list):
            for c in raw_cmds:
                if isinstance(c, str):
                    cmds.append(c.rsplit("/", 1)[-1])
                elif isinstance(c, dict):
                    name = c.get("name") or c.get("command") or ""
                    if name:
                        cmds.append(str(name).rsplit("/", 1)[-1])
        # Single-command rows
        if not cmds:
            single = row.get("name") or row.get("command") or ""
            if single:
                cmds.append(str(single).rsplit("/", 1)[-1])
        existing = surfaces.setdefault(surface, [])
        for c in cmds:
            if c not in existing:
                existing.append(c)
    return surfaces


def fetch_capabilities(
    *,
    run_fn: Callable[[list[str], float], dict] | None = None,
    profile: str | None = None,
    timeout: float = 30.0,
) -> OpenCliCapabilities:
    """执行 ``opencli list -f json`` 并返回结构化快照。"""
    runner = run_fn or _run
    argv = ["opencli"]
    if profile:
        argv.extend(["--profile", profile])
    argv.extend(["list", "-f", "json"])
    raw = runner(argv, timeout)
    now = datetime.now(UTC)
    if not raw.get("ok"):
        empty = "[]"
        return OpenCliCapabilities(
            snapshot_id=_snapshot_id(empty + "|failed"),
            captured_at=now,
            surfaces={},
            raw=[],
        )
    stdout = raw.get("stdout") or "[]"
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        data = []
    surfaces = parse_capability_list(data)
    raw_list = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    return OpenCliCapabilities(
        snapshot_id=_snapshot_id(stdout),
        captured_at=now,
        surfaces=surfaces,
        raw=[r for r in raw_list if isinstance(r, dict)],
    )
