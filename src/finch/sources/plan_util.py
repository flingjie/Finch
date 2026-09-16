"""Capability-aware command resolution for Source Connector plans."""

from __future__ import annotations

from datetime import UTC, datetime

from finch.sources.models import OpenCliCapabilities


def empty_capabilities() -> OpenCliCapabilities:
    """Empty snapshot used when plan() is called without a probe result."""
    return OpenCliCapabilities(
        snapshot_id="",
        captured_at=datetime.now(UTC),
        surfaces={},
    )


def resolve_command(
    capabilities: OpenCliCapabilities,
    surface: str,
    preferred: list[str],
) -> str | None:
    """Return the first preferred command available on ``surface``.

    Matching order: exact name, then substring (e.g. preferred ``search`` matches
    ``twitter-search``). When the capability snapshot is empty (degraded / offline
    probe), return the first preferred command so callers can still attempt.
    """
    if not preferred:
        return None
    cmds = capabilities.surfaces.get(surface, [])
    if not capabilities.surfaces:
        return preferred[0]
    if surface not in capabilities.surfaces:
        return None
    cmd_set = set(cmds)
    for pref in preferred:
        if pref in cmd_set:
            return pref
        for c in cmds:
            if pref in c:
                return c
    return None
