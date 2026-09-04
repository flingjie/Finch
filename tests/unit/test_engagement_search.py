"""外部帖子搜索适配器单元测试（无网络、无 opencli）。"""

from datetime import UTC, datetime

import pytest

from finch.engagement.models import ExternalPost
from finch.engagement.search import (
    ProviderUnavailableError,
    RedditPostSearchProvider,
    XPostSearchProvider,
    build_queries,
    dedupe,
    is_excluded,
    search_engagement_posts,
)
from finch.settings import EngagementSettings, InterestsSettings
from finch.twitter.models import Tweet


def _tweet(
    id: str = "1",
    author: str = "alice",
    text: str = "hello",
    created_at: str = "Wed Sep 03 12:00:00 +0000 2025",
    likes: int = 10,
    views: int = 100,
    url: str = "https://x.com/alice/status/1",
) -> Tweet:
    return Tweet(
        id=id,
        author=author,
        text=text,
        created_at=created_at,
        likes=likes,
        views=views,
        url=url,
    )


def _post(
    id: str = "1",
    platform: str = "x",
    url: str = "https://x.com/alice/status/1",
    author_id: str = "alice",
    author_name: str = "alice",
    content: str = "hello",
    published_at: datetime | None = None,
    metrics: dict | None = None,
    matched_topics: list[str] | None = None,
) -> ExternalPost:
    return ExternalPost(
        id=id,
        platform=platform,
        url=url,
        author_id=author_id,
        author_name=author_name,
        content=content,
        published_at=published_at or datetime(2025, 9, 3, 12, 0, tzinfo=UTC),
        metrics=metrics or {},
        matched_topics=matched_topics or [],
    )


def test_x_provider_maps_tweet_to_external_post():
    class FakeClient:
        def search(self, query, *, product="top", limit=20):
            return [_tweet(id="123", author="alice", text="some text")]

    provider = XPostSearchProvider(client=FakeClient())
    posts = provider.search("agent reliability", limit=5)

    assert len(posts) == 1
    post = posts[0]
    assert post.platform == "x"
    assert post.id == "123"
    assert post.url == "https://x.com/alice/status/1"
    assert post.author_id == "alice"
    assert post.author_name == "alice"
    assert post.content == "some text"
    assert post.published_at == datetime(2025, 9, 3, 12, 0, tzinfo=UTC)
    assert post.metrics == {"likes": 10, "views": 100}
    assert post.matched_topics == ["agent reliability"]


def test_x_provider_skips_tweet_without_parseable_time():
    class FakeClient:
        def search(self, query, *, product="top", limit=20):
            return [_tweet(id="bad", created_at="not a date")]

    provider = XPostSearchProvider(client=FakeClient())
    assert provider.search("q", limit=5) == []


def test_reddit_provider_reports_unavailable():
    provider = RedditPostSearchProvider()
    assert provider.platform == "reddit"
    assert provider.available() is False
    with pytest.raises(ProviderUnavailableError):
        provider.search("query", limit=5)


def test_build_queries_from_stable_and_exploring():
    interests = InterestsSettings(stable=["a", "b"], exploring=["c", " a "])
    assert build_queries(interests) == ["a", "b", "c"]


def test_is_excluded_by_content_and_topic_case_insensitive():
    excluded = ["AI 新闻搬运", "融资"]
    assert is_excluded(_post(content="这是一篇 ai 新闻搬运 的帖子"), excluded) is True
    assert is_excluded(_post(content="fine", matched_topics=["融资与估值"]), excluded) is True
    assert is_excluded(_post(content="unrelated"), excluded) is False


def test_dedupe_by_platform_and_id():
    posts = [
        _post(id="1", platform="x"),
        _post(id="1", platform="x"),
        _post(id="1", platform="reddit"),
        _post(id="2", platform="x"),
    ]
    result = dedupe(posts)
    assert [(p.platform, p.id) for p in result] == [("x", "1"), ("reddit", "1"), ("x", "2")]


def test_search_respects_max_posts_scanned():
    class FakeClient:
        def search(self, query, *, product="top", limit=20):
            return [_tweet(id=str(i), text=f"post {i}") for i in range(10)]

    provider = XPostSearchProvider(client=FakeClient())
    interests = InterestsSettings(stable=["agent reliability"])
    engagement = EngagementSettings(max_posts_scanned=3)

    outcome = search_engagement_posts([provider], interests, engagement)

    assert len(outcome.posts) == 3
    assert outcome.failures == []


def test_one_provider_failure_does_not_drop_other_posts():
    class FailingProvider:
        platform = "reddit"

        def available(self):
            return True

        def search(self, query, *, limit):
            raise ProviderUnavailableError("boom")

    class OkClient:
        def search(self, query, *, product="top", limit=20):
            return [_tweet(id="42", text="good post")]

    ok = XPostSearchProvider(client=OkClient())
    interests = InterestsSettings(stable=["agent reliability"])
    engagement = EngagementSettings(max_posts_scanned=30)

    outcome = search_engagement_posts([ok, FailingProvider()], interests, engagement)

    assert [p.id for p in outcome.posts] == ["42"]
    assert len(outcome.failures) == 1
    failure = outcome.failures[0]
    assert failure.platform == "reddit"
    assert failure.query == "agent reliability"
    assert "boom" in failure.reason


def test_unavailable_provider_recorded_not_called():
    calls: list[str] = []

    class UnavailableProvider:
        platform = "reddit"

        def available(self):
            return False

        def search(self, query, *, limit):
            calls.append(query)
            return []

    outcome = search_engagement_posts(
        [UnavailableProvider()],
        InterestsSettings(stable=["q"]),
        EngagementSettings(),
    )

    assert outcome.posts == []
    assert calls == []
    assert [f.reason for f in outcome.failures] == ["provider not enabled"]


def test_skip_ids_excludes_known_posts():
    class FakeClient:
        def search(self, query, *, product="top", limit=20):
            return [_tweet(id="1"), _tweet(id="2")]

    provider = XPostSearchProvider(client=FakeClient())
    outcome = search_engagement_posts(
        [provider],
        InterestsSettings(stable=["q"]),
        EngagementSettings(max_posts_scanned=30),
        skip_ids={"1"},
    )

    assert [p.id for p in outcome.posts] == ["2"]


def test_search_runs_queries_in_parallel():
    import threading

    # 串行实现会在第一个 query 上阻塞至 barrier 超时（BrokenBarrierError）；并行后两个
    # query 同时到达 barrier。结果仍按 provider 主序 + query 次序回放。
    barrier = threading.Barrier(2, timeout=5)
    calls: list[str] = []

    class BarrierProvider:
        platform = "x"

        def available(self):
            return True

        def search(self, query, *, limit):
            barrier.wait()
            calls.append(query)
            return [_post(id=f"p_{query}", content=query, matched_topics=[query])]

    outcome = search_engagement_posts(
        [BarrierProvider()],
        InterestsSettings(stable=["alpha", "beta"]),
        EngagementSettings(max_posts_scanned=30),
    )

    assert sorted(calls) == ["alpha", "beta"]
    assert [p.id for p in outcome.posts] == ["p_alpha", "p_beta"]


def test_search_evaluates_available_once():
    calls = {"available": 0}

    class CountingProvider:
        platform = "x"

        def available(self):
            calls["available"] += 1
            return True

        def search(self, query, *, limit):
            return []

    outcome = search_engagement_posts(
        [CountingProvider()],
        InterestsSettings(stable=["a", "b"]),
        EngagementSettings(max_posts_scanned=10),
    )
    assert outcome.posts == []
    assert outcome.failures == []
    # available() 只应评估一次（两个 query 共享同一 provider 状态）
    assert calls == {"available": 1}
