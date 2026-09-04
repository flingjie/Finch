"""互动轨道流程单元测试（无网络、无 LLM，fakes 复用 search/scoring 测试风格）。"""

import pytest

from finch.codex.runner import CodexRunner
from finch.engagement.flow import run_discovery_engagement_flow
from finch.engagement.scoring import ConversationScoreInput, ScoreBatchOutput, ScoreItem
from finch.settings import EngagementSettings, InterestsSettings, Settings
from finch.twitter.models import Tweet


class FakeOpenCli:
    def __init__(self, tweets: list[Tweet] | None = None):
        self._tweets = tweets or []
        self.calls = 0

    def search(self, query, *, product="top", limit=20):
        self.calls += 1
        return list(self._tweets)


class FakeRunner(CodexRunner):
    def __init__(self, items: list[ScoreItem] | None = None):
        self.calls = 0
        self.items = items or []

    def run(self, prompt, output_model, **kwargs):
        self.calls += 1
        return ScoreBatchOutput(items=self.items)


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


def _settings(*, platforms: list[str]) -> Settings:
    return Settings(
        engagement=EngagementSettings(platforms=platforms, max_posts_scanned=30),
        interests=InterestsSettings(stable=["agent reliability"]),
    )


def _dims(**overrides) -> ConversationScoreInput:
    data = dict(
        relevance=0.9,
        novelty=0.9,
        discussability=0.9,
        practical_evidence=0.9,
        relationship_value=0.9,
        reasons=["on topic", "debatable", "has code"],
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
    assert result.failures == []
    assert runner.calls == 0
    assert "no posts found" in result.summary


def test_found_posts_return_ranked_candidates_with_total_and_reasons():
    opencli = FakeOpenCli(tweets=[_tweet(id="p1")])
    runner = FakeRunner(items=[ScoreItem(post_id="p1", scores=_dims())])
    result = run_discovery_engagement_flow(
        _settings(platforms=["x"]), opencli, runner, run_id="run-1"
    )

    assert result.status == "succeeded"
    assert result.posts_found == 1
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.post.id == "p1"
    assert candidate.score.total == pytest.approx(0.9)
    assert candidate.score.reasons == ["on topic", "debatable", "has code"]
    assert "p1" in result.summary


def test_failing_provider_still_yields_other_provider_posts_and_records_failures():
    # reddit 适配器未启用（available()=False），x 正常返回；flow 应保留 x 的候选并记录 reddit 失败。
    opencli = FakeOpenCli(tweets=[_tweet(id="p1")])
    runner = FakeRunner(items=[ScoreItem(post_id="p1", scores=_dims())])
    result = run_discovery_engagement_flow(
        _settings(platforms=["x", "reddit"]), opencli, runner, run_id="run-1"
    )

    assert result.status == "succeeded"
    assert [c.post.id for c in result.candidates] == ["p1"]
    assert len(result.failures) == 1
    assert result.failures[0].platform == "reddit"
    assert result.failures[0].reason == "provider not enabled"


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
    assert "scoring exploded" in result.summary
