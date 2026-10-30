"""Reddit subreddit / sort / comment normalize / expand plan tests."""

from __future__ import annotations

from datetime import UTC, datetime

from finch.sources.connectors import DiscoveryContext
from finch.sources.connectors.reddit import RedditConnector
from finch.sources.models import (
    AuthorIdentity,
    OpenCliCapabilities,
    OpenCliResult,
    RawArtifact,
    ResultKind,
    Source,
)


def _caps() -> OpenCliCapabilities:
    return OpenCliCapabilities(
        snapshot_id="s",
        captured_at=datetime.now(UTC),
        surfaces={"reddit": ["search", "subreddit", "read"]},
    )


def test_reddit_plan_emits_sorts_and_subreddits():
    ctx = DiscoveryContext(
        queries=["agent stuck"],
        limit=10,
        extra={
            "subreddits": ["LocalLLaMA", "r/MachineLearning"],
            "sorts": ["new", "top", "comments"],
            "max_expand": 3,
        },
    )
    reqs = RedditConnector().plan(ctx, _caps())
    search = [r for r in reqs if r.command == "search"]
    sub = [r for r in reqs if r.command == "subreddit"]
    assert len(search) == 3  # one per sort
    assert {r.args[2] for r in search} == {"new", "top", "comments"}
    # comments/relevance map to new for subreddit → deduped with explicit new
    assert len(sub) == 4  # 2 subs × {new, top}
    assert {r.args[0] for r in sub} == {"LocalLLaMA", "MachineLearning"}
    assert {r.args[2] for r in sub} == {"new", "top"}


def test_reddit_normalize_comment_keeps_parent():
    result = OpenCliResult(
        rows=[
            {
                "id": "c1",
                "type": "COMMENT",
                "text": "still broken",
                "author": "u1",
                "parent_id": "t3_abc",
                "score": 2,
            }
        ],
        kind=ResultKind.SUCCESS,
    )
    arts = RedditConnector().normalize(result)
    assert len(arts) == 1
    assert arts[0].source_type == "comment"
    assert arts[0].metrics["parent_id"] == "t3_abc"
    assert "still broken" in arts[0].text


def test_reddit_expand_read_requests_bounded():
    arts = [
        RawArtifact(
            artifact_id=f"reddit:post:{i}",
            source=Source.REDDIT,
            source_type="post",
            source_id=str(i),
            canonical_url=f"https://www.reddit.com/r/x/comments/{i}/t/",
            author_identity=AuthorIdentity(platform="reddit", handle="u"),
            text="body",
            retrieved_at=datetime.now(UTC),
            capture_method="adapter",
        )
        for i in range(5)
    ]
    reqs = RedditConnector().expand_read_requests(arts, max_expand=2, capabilities=_caps())
    assert len(reqs) == 2
    assert all(r.command == "read" for r in reqs)
