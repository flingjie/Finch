"""Heat ranking + agent tagging for repo-discovery."""

from datetime import UTC, datetime, timedelta

from finch.repos.heat import build_ranking, tweet_heat
from finch.repos.models import (
    FORMULA_VERSION_LIKES_ONLY,
    RepoRecord,
    SortKey,
    TopicTag,
    TweetRecord,
)
from finch.repos.tagging import tag_repo


def _tw(
    tid: str,
    *,
    likes: int | None = 1,
    repos: list[str] | None = None,
    published: datetime | None = None,
    is_rt: bool = False,
) -> TweetRecord:
    return TweetRecord(
        tweet_id=tid,
        likes=likes,
        repo_refs=repos or ["acme/widget"],
        published_at=published or datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
        is_native_retweet=is_rt,
        text="github.com/acme/widget agent harness",
        url=f"https://x.com/u/status/{tid}",
    )


def test_tweet_heat_likes_only_and_native_rt_skipped():
    assert tweet_heat(_tw("1", likes=5)) == 5
    assert tweet_heat(_tw("2", likes=None)) is None
    assert tweet_heat(_tw("3", likes=9, is_rt=True)) is None


def test_same_tweet_across_queries_not_double_counted_in_ranking():
    # One tweet id saved once; mentions=1 even if conceptually hit by two queries.
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = start + timedelta(hours=24)
    tweets = [_tw("t1", likes=10)]
    repos = [
        RepoRecord(
            key="acme/widget",
            owner="acme",
            name="widget",
            tweet_ids=["t1"],
            source_share_urls=["https://x.com/u/status/t1"],
            topic_tag=TopicTag.AGENT,
            stars=100,
        )
    ]
    snap = build_ranking(
        run_id="r1",
        window_start=start,
        window_end=end,
        tweets=tweets,
        repos=repos,
    )
    assert snap.entries[0].x_heat == 10
    assert snap.entries[0].mentions == 1
    assert snap.formula_version == FORMULA_VERSION_LIKES_ONLY


def test_multi_repo_tweet_gives_same_heat_to_each():
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = start + timedelta(hours=24)
    tw = _tw("t1", likes=7, repos=["a/one", "b/two"])
    repos = [
        RepoRecord(key="a/one", owner="a", name="one", tweet_ids=["t1"]),
        RepoRecord(key="b/two", owner="b", name="two", tweet_ids=["t1"]),
    ]
    snap = build_ranking(
        run_id="r1", window_start=start, window_end=end, tweets=[tw], repos=repos
    )
    by_key = {e.repo_key: e for e in snap.entries}
    assert by_key["a/one"].x_heat == 7
    assert by_key["b/two"].x_heat == 7


def test_null_heat_sorts_after_known():
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = start + timedelta(hours=24)
    tweets = [
        _tw("t1", likes=3, repos=["hot/repo"]),
        TweetRecord(
            tweet_id="t2",
            likes=None,
            repo_refs=["cold/repo"],
            published_at=start + timedelta(hours=1),
        ),
    ]
    repos = [
        RepoRecord(key="hot/repo", owner="hot", name="repo", tweet_ids=["t1"], stars=1),
        RepoRecord(key="cold/repo", owner="cold", name="repo", tweet_ids=["t2"], stars=999),
    ]
    snap = build_ranking(
        run_id="r1", window_start=start, window_end=end, tweets=tweets, repos=repos
    )
    assert snap.entries[0].repo_key == "hot/repo"
    assert snap.entries[1].repo_key == "cold/repo"
    assert snap.entries[1].x_heat is None


def test_stable_tiebreak_by_key():
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = start + timedelta(hours=24)
    tweets = [
        _tw("t1", likes=5, repos=["b/repo"]),
        _tw("t2", likes=5, repos=["a/repo"]),
    ]
    repos = [
        RepoRecord(key="b/repo", owner="b", name="repo", tweet_ids=["t1"], stars=0),
        RepoRecord(key="a/repo", owner="a", name="repo", tweet_ids=["t2"], stars=0),
    ]
    snap = build_ranking(
        run_id="r1", window_start=start, window_end=end, tweets=tweets, repos=repos
    )
    # same heat, same mentions, same stars → alphabetical key
    assert [e.repo_key for e in snap.entries] == ["a/repo", "b/repo"]


def test_sort_github_stars_and_agent_filter():
    start = datetime(2026, 10, 1, tzinfo=UTC)
    end = start + timedelta(hours=24)
    tweets = [_tw("t1", likes=1, repos=["x/a"]), _tw("t2", likes=9, repos=["y/b"])]
    repos = [
        RepoRecord(
            key="x/a",
            owner="x",
            name="a",
            tweet_ids=["t1"],
            stars=50,
            topic_tag=TopicTag.OTHER,
        ),
        RepoRecord(
            key="y/b",
            owner="y",
            name="b",
            tweet_ids=["t2"],
            stars=10,
            topic_tag=TopicTag.AGENT,
        ),
    ]
    by_stars = build_ranking(
        run_id="r1",
        window_start=start,
        window_end=end,
        tweets=tweets,
        repos=repos,
        sort=SortKey.GITHUB_STARS,
    )
    assert by_stars.entries[0].repo_key == "x/a"
    agent_only = build_ranking(
        run_id="r1",
        window_start=start,
        window_end=end,
        tweets=tweets,
        repos=repos,
        topic_filter="agent",
    )
    assert [e.repo_key for e in agent_only.entries] == ["y/b"]


def test_tag_repo_keywords():
    repo = RepoRecord(key="acme/agent-kit", owner="acme", name="agent-kit", description="")
    tw = _tw("1", repos=["acme/agent-kit"])
    assert tag_repo(repo, [tw], keywords=["agent", "harness"]) == TopicTag.AGENT
    plain = RepoRecord(key="acme/csv-tool", owner="acme", name="csv-tool", description="CSV")
    assert (
        tag_repo(plain, [], keywords=["agent", "harness"]) == TopicTag.OTHER
    )
