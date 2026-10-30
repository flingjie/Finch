"""Hacker News connector normalize / plan / policy tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from finch.sources.connectors import DiscoveryContext
from finch.sources.connectors.hackernews import HackerNewsConnector
from finch.sources.models import (
    OpenCliCapabilities,
    OpenCliResult,
    ResultKind,
    Source,
    SourceCommandBlocked,
)
from finch.sources.policy import check_allowlist


def test_hn_normalize_story_and_nested_comments():
    result = OpenCliResult(
        rows=[
            {
                "id": "100",
                "title": "Ask HN: agent stuck?",
                "text": "tools hang",
                "author": "alice",
                "score": 10,
                "comments": [
                    {
                        "id": "101",
                        "text": "same here",
                        "author": "bob",
                        "children": [
                            {"id": "102", "text": "workaround: retry", "author": "carol"}
                        ],
                    }
                ],
            }
        ],
        kind=ResultKind.SUCCESS,
    )
    arts = HackerNewsConnector().normalize(result)
    by_id = {a.source_id: a for a in arts}
    assert set(by_id) == {"100", "101", "102"}
    assert by_id["100"].source == Source.HACKERNEWS
    assert by_id["100"].source_type == "story"
    assert by_id["101"].metrics["parent_id"] == "100"
    assert by_id["101"].metrics["depth"] == 1
    assert by_id["102"].metrics["parent_id"] == "101"
    assert by_id["102"].metrics["depth"] == 2
    assert "workaround" in by_id["102"].text


def test_hn_plan_search_ask_and_read():
    caps = OpenCliCapabilities(
        snapshot_id="s1",
        captured_at=datetime.now(UTC),
        surfaces={
            "hackernews": ["search", "ask", "show", "read", "top"],
        },
    )
    ctx = DiscoveryContext(
        queries=["agent stuck"],
        urls=["47999636"],
        limit=5,
        extra={"entrances": ["search", "ask"]},
    )
    reqs = HackerNewsConnector().plan(ctx, caps)
    commands = [r.command for r in reqs]
    assert "search" in commands
    assert "ask" in commands
    assert "read" in commands
    read = next(r for r in reqs if r.command == "read")
    assert read.args[0] == "47999636"
    assert read.use_browser_lock is False


def test_hn_policy_allows_read_denies_submit():
    check_allowlist(["opencli", "hackernews", "search", "q", "-f", "json"])
    check_allowlist(["opencli", "hackernews", "read", "1", "-f", "json"])
    with pytest.raises(SourceCommandBlocked):
        check_allowlist(["opencli", "hackernews", "submit", "title"])
