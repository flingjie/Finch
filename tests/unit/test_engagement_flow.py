"""互动轨道流程单元测试（无网络、无 LLM；发现路径产出 Opportunity，无草稿）。"""

import pytest

from finch.codex.runner import CodexRunner
from finch.engagement.flow import run_discovery_engagement_flow
from finch.engagement.models import Opportunity
from finch.engagement.scoring import ConversationScoreInput, ScoreBatchOutput, ScoreItem
from finch.reddit.models import RedditPost
from finch.settings import EngagementSettings, InterestsSettings, Settings
from finch.twitter.models import Tweet


class FakeOpenCli:
    def __init__(self, tweets: list[Tweet] | None = None):
        self._tweets = tweets or []
        self.calls = 0

    def search(self, query, *, product="top", limit=20):
        self.calls += 1
        return list(self._tweets)


class FakeRedditOpenCli:
    def __init__(self, posts: list[RedditPost] | None = None):
        self._posts = posts or []
        self.calls = 0

    def search(self, query, *, sort="relevance", limit=20):
        self.calls += 1
        return list(self._posts)


class FakeRunner(CodexRunner):
    def __init__(self, items: list[ScoreItem] | None = None):
        self.calls = 0
        self.items = items or []
        self.proposal_calls = 0

    def run(self, prompt, output_model, **kwargs):
        self.calls += 1
        name = getattr(output_model, "__name__", "")
        if output_model is ScoreBatchOutput or name == "ScoreBatchOutput":
            return ScoreBatchOutput(items=self.items)
        if name == "ProposalBatchOutput":
            self.proposal_calls += 1
            raise AssertionError("discovery must not call generate_proposals")
        raise AssertionError(f"unexpected output_model: {output_model}")


def _tweet(
    id: str = "1",
    author: str = "alice",
    text: str = "How do you test agent reliability in production systems?",
) -> Tweet:
    return Tweet(
        id=id,
        author=author,
        text=text,
        created_at="Wed Sep 03 12:00:00 +0000 2025",
        likes=10,
        views=100,
        url=f"https://x.com/{author}/status/{id}",
    )


def _reddit_post(id: str = "r1") -> RedditPost:
    return RedditPost(
        id=id,
        title="How do you test agent reliability?",
        subreddit="LocalLLaMA",
        author="bob",
        score=10,
        comments=3,
        url=f"https://www.reddit.com/r/LocalLLaMA/comments/{id}/t/",
        created_utc=1756627200,
        selftext="A concrete failure replay.",
    )


def _settings(*, platforms: list[str]) -> Settings:
    return Settings(
        engagement=EngagementSettings(platforms=platforms, max_posts_scanned=30),
        interests=InterestsSettings(long_term_interests=["agent reliability"]),
    )


def _dims(**overrides) -> ConversationScoreInput:
    data = dict(
        relevance=0.9,
        novelty=0.9,
        discussability=0.9,
        practical_evidence=0.9,
        reasons=["on topic", "debatable", "has code"],
        why_relevant="Concrete overlap with failure-replay practice",
        opening="Ask how they diff tool traces after a partial failure",
        suggested_mode="discuss",
        complementarity=0.7,
        topic_tags=["agent reliability"],
        novelty_reason="new framing",
    )
    data.update(overrides)
    return ConversationScoreInput(**data)


def test_empty_search_returns_empty_and_skips_llm():
    opencli = FakeOpenCli(tweets=[])
    runner = FakeRunner()
    result = run_discovery_engagement_flow(
        _settings(platforms=["x"]), opencli, runner, run_id="run-1"
    )

    assert result.status == "empty"
    assert result.posts_found == 0
    assert result.candidates == []
    assert result.opportunities == []
    assert result.failures == []
    assert runner.calls == 0
    assert "no posts found" in result.summary


def test_found_posts_return_opportunities_without_drafts():
    opencli = FakeOpenCli(tweets=[_tweet(id="p1")])
    runner = FakeRunner(items=[ScoreItem(post_id="p1", scores=_dims())])
    result = run_discovery_engagement_flow(
        _settings(platforms=["x"]), opencli, runner, run_id="run-1"
    )

    assert result.status == "succeeded"
    assert result.posts_found == 1
    assert result.candidates == []
    assert len(result.opportunities) == 1
    opp = result.opportunities[0]
    assert isinstance(opp, Opportunity)
    assert opp.post is not None and opp.post.id == "p1"
    assert opp.why_relevant
    assert opp.opening
    assert opp.score_total == pytest.approx(0.9 * (0.25 + 0.25 + 0.20 + 0.20) + 0.10 * 0.5)
    assert runner.proposal_calls == 0
    assert "p1" in result.summary or opp.source_refs


def test_reddit_posts_flow_through_with_x():
    opencli = FakeOpenCli(tweets=[_tweet(id="p1")])
    reddit = FakeRedditOpenCli(posts=[_reddit_post(id="r1")])
    runner = FakeRunner(
        items=[
            ScoreItem(post_id="p1", scores=_dims()),
            ScoreItem(post_id="r1", scores=_dims(why_relevant="Reddit failure replay detail")),
        ],
    )
    result = run_discovery_engagement_flow(
        _settings(platforms=["x", "reddit"]), opencli, runner,
        reddit_opencli=reddit, run_id="run-1",
    )

    assert result.status == "succeeded"
    assert sorted(o.post.id for o in result.opportunities if o.post) == ["p1", "r1"]
    assert result.candidates == []
    assert result.failures == []


def test_reddit_failure_keeps_x_posts_and_records_failure():
    class FailingReddit:
        def search(self, query, *, sort="relevance", limit=20):
            raise RuntimeError("boom")

    opencli = FakeOpenCli(tweets=[_tweet(id="p1")])
    runner = FakeRunner(items=[ScoreItem(post_id="p1", scores=_dims())])
    result = run_discovery_engagement_flow(
        _settings(platforms=["x", "reddit"]), opencli, runner,
        reddit_opencli=FailingReddit(), run_id="run-1",
    )

    assert result.status == "succeeded"
    assert [o.post.id for o in result.opportunities if o.post] == ["p1"]
    assert len(result.failures) == 1
    assert result.failures[0].platform == "reddit"
    assert "boom" in result.failures[0].reason


def test_top_level_error_returns_failed_not_raise():
    class ExplodingRunner(CodexRunner):
        def run(self, prompt, output_model, **kwargs):
            raise RuntimeError("scoring exploded")

    opencli = FakeOpenCli(tweets=[_tweet(id="p1")])
    result = run_discovery_engagement_flow(
        _settings(platforms=["x"]), opencli, ExplodingRunner(), run_id="run-1"
    )

    assert result.status == "failed"
    assert result.candidates == []
    assert result.opportunities == []
    assert "scoring exploded" in result.summary


def test_empty_current_questions_still_runs():
    settings = Settings(
        engagement=EngagementSettings(platforms=["x"]),
        interests=InterestsSettings(
            long_term_interests=["agent reliability"],
            current_questions=[],
        ),
    )
    opencli = FakeOpenCli(tweets=[_tweet(id="p1")])
    runner = FakeRunner(items=[ScoreItem(post_id="p1", scores=_dims())])
    result = run_discovery_engagement_flow(settings, opencli, runner, run_id="run-1")
    assert result.status == "succeeded"
    assert len(result.opportunities) == 1
