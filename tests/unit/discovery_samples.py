"""P0 representative discovery samples for problem-led connection (A–D).

Frozen scenarios used as regression fixtures — not live network/LLM output.
Covers: familiar view, new author, old author with new experiment, cross-domain
method, learn-only content, missing source, duplicate opportunity, platform failure.
"""

from __future__ import annotations

from datetime import UTC, datetime

from finch.engagement.models import ExternalPost
from finch.engagement.search import PostSearchFailure

_NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def _post(
    *,
    id: str,
    author_id: str,
    content: str,
    platform: str = "x",
    matched_topics: list[str] | None = None,
    url: str | None = None,
) -> ExternalPost:
    return ExternalPost(
        id=id,
        platform=platform,  # type: ignore[arg-type]
        url=url or f"https://x.com/{author_id}/status/{id}",
        author_id=author_id,
        author_name=author_id,
        content=content,
        published_at=_NOW,
        matched_topics=matched_topics or [],
    )


# Familiar viewpoint (known peer, known framing)
FAMILIAR_VIEW = _post(
    id="fam_1",
    author_id="alice",
    content="Agent reliability still means replaying the same failure until the graph is deterministic.",
    matched_topics=["agent reliability"],
)

# Brand-new author
NEW_AUTHOR = _post(
    id="new_1",
    author_id="newbie_carol",
    content="First write-up: we checkpoint tool calls before each compensation step. Looking for critique.",
    matched_topics=["agent reliability", "checkpoint"],
)

# Known author, new experiment / evidence
OLD_AUTHOR_NEW_EXPERIMENT = _post(
    id="old_exp_2",
    author_id="alice",
    content="New experiment: failure replay + diff of tool traces cut MTTD by 40% in one week.",
    matched_topics=["agent reliability", "failure replay"],
)

# Cross-domain method (not agent keywords; mechanism-relevant)
CROSS_DOMAIN_METHOD = _post(
    id="cross_1",
    author_id="db_dave",
    content="Saga compensation tables taught us to separate intent from side effects — same pattern as agent handoff.",
    matched_topics=["distributed systems", "compensation"],
)

# Learn-only: worth knowing, no immediate reply opening
LEARN_ONLY = _post(
    id="learn_1",
    author_id="observer_erin",
    content="Annotated notes on human–agent handoff checklists from three production outages (no ask).",
    matched_topics=["human handoff"],
)

# Missing / empty source (should fail quality gate)
MISSING_SOURCE = ExternalPost(
    id="missing_1",
    platform="x",
    url="",
    author_id="ghost",
    author_name="ghost",
    content="Vague take with no checkable link.",
    published_at=_NOW,
    matched_topics=["agent reliability"],
)

# Duplicate of FAMILIAR_VIEW content (same fingerprint intent)
DUPLICATE_OF_FAMILIAR = _post(
    id="fam_1_dup",
    author_id="alice",
    content="Agent reliability still means replaying the same failure until the graph is deterministic.",
    matched_topics=["agent reliability"],
)

# Practice release that must not be excluded solely for 「发布」
PRACTICE_RELEASE = _post(
    id="ship_1",
    author_id="shipper_fran",
    content="发布了失败复盘实验：checkpoint + 补偿表，附实测 diff 与代码片段。",
    matched_topics=["checkpoint"],
)

PLATFORM_FAILURE = PostSearchFailure(
    platform="x",
    query="agent reliability",
    reason="opencli timeout",
)

ALL_SAMPLE_POSTS: list[ExternalPost] = [
    FAMILIAR_VIEW,
    NEW_AUTHOR,
    OLD_AUTHOR_NEW_EXPERIMENT,
    CROSS_DOMAIN_METHOD,
    LEARN_ONLY,
    MISSING_SOURCE,
    DUPLICATE_OF_FAMILIAR,
    PRACTICE_RELEASE,
]


# Baseline snapshot of pre-A daily shape (documented expectations, not live capture).
BASELINE_DAILY_SHAPE = {
    "show_peers_cap": 3,
    "pool_max_peers_per_run": 5,
    "max_reply_drafts": 3,
    "always_runs_discovery": True,
    "generates_drafts_on_daily": True,
}
