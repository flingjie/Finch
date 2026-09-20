"""Unit tests for per-source query plan builder."""

from __future__ import annotations

from finch.settings import (
    ExplorationTopic,
    Settings,
    SourceGithubPlan,
    SourcesSettings,
    SourceTwitterPlan,
    SourceV2exPlan,
    SourceWeixinPlan,
)
from finch.sources.models import Source
from finch.sources.query_plan import build_context_by_source, select_exploration_topic


def test_cli_override_single_source():
    settings = Settings()
    ctx = build_context_by_source(
        settings,
        cli_queries=["AI Agent"],
        source=Source.TWITTER,
        limit=10,
    )
    assert list(ctx.keys()) == [Source.TWITTER]
    assert ctx[Source.TWITTER].queries == ["AI Agent"]
    assert ctx[Source.TWITTER].limit == 10


def test_all_sources_uses_yaml_not_shared_query():
    settings = Settings(
        sources=SourcesSettings(
            twitter=SourceTwitterPlan(queries=["agent reliability"]),
            github=SourceGithubPlan(users=["jackwener"]),
        )
    )
    ctx = build_context_by_source(
        settings,
        cli_queries=["SHOULD_NOT_APPLY"],
        all_sources=True,
        limit=20,
    )
    assert ctx[Source.TWITTER].queries == ["agent reliability"]
    assert ctx[Source.GITHUB].queries == ["jackwener"]
    # Topic query must never land on GitHub
    assert "SHOULD_NOT_APPLY" not in ctx[Source.GITHUB].queries
    assert "SHOULD_NOT_APPLY" not in ctx[Source.TWITTER].queries


def test_github_cli_queries_are_logins():
    settings = Settings()
    ctx = build_context_by_source(
        settings,
        cli_queries=["octocat", "torvalds"],
        source=Source.GITHUB,
    )
    assert ctx[Source.GITHUB].queries == ["octocat", "torvalds"]


def test_legacy_queries_infer_enabled_and_query_mode():
    # 旧 YAML：只有 queries 而无 enabled/mode → 推断为启用 + query 模式。
    plan = SourceTwitterPlan(queries=["q"])
    assert plan.enabled is True
    assert plan.mode == "query"


def test_sources_twitter_is_sole_source_not_legacy():
    # 顶层 legacy `twitter:` 不再回退进 sources 查询计划。
    settings = Settings(
        twitter={"queries": [{"id": "q1", "text": '"agent harness"'}]},  # type: ignore[arg-type]
    )
    ctx = build_context_by_source(settings, all_sources=True)
    assert Source.TWITTER not in ctx


def test_disabled_source_skipped():
    settings = Settings(
        sources=SourcesSettings(
            twitter=SourceTwitterPlan(queries=["q"], enabled=False)
        )
    )
    ctx = build_context_by_source(settings, all_sources=True)
    assert Source.TWITTER not in ctx


def test_enabled_query_without_queries_is_config_error():
    settings = Settings(
        sources=SourcesSettings(
            twitter=SourceTwitterPlan(queries=[], enabled=True, mode="query")
        )
    )
    ctx = build_context_by_source(settings, all_sources=True)
    assert Source.TWITTER in ctx
    assert ctx[Source.TWITTER].config_error


def test_v2ex_hot_mode_preserved():
    settings = Settings(
        sources=SourcesSettings(v2ex=SourceV2exPlan(enabled=True, mode="hot"))
    )
    ctx = build_context_by_source(settings, all_sources=True)
    assert ctx[Source.V2EX].mode == "hot"
    assert ctx[Source.V2EX].queries == []


def test_weixin_queries_map_to_search():
    settings = Settings(
        sources=SourcesSettings(
            weixin=SourceWeixinPlan(queries=["AI Agent 实践"], enabled=True)
        )
    )
    ctx = build_context_by_source(settings, all_sources=True)
    assert ctx[Source.WEIXIN].queries == ["AI Agent 实践"]
    assert ctx[Source.WEIXIN].mode == "query"


def test_select_exploration_topic_rotates_deterministically():
    from datetime import UTC, datetime

    topics = [
        ExplorationTopic(id="a", queries_by_source={"twitter": ["q1"]}),
        ExplorationTopic(id="b", queries_by_source={"twitter": ["q2"]}),
    ]
    settings = Settings(sources=SourcesSettings(exploration_topics=topics))

    assert select_exploration_topic(Settings()) is None  # 无主题 → None

    day0 = datetime(2026, 1, 1, tzinfo=UTC)
    day1 = datetime(2026, 1, 2, tzinfo=UTC)
    t0 = select_exploration_topic(settings, now=day0)
    t1 = select_exploration_topic(settings, now=day1)
    assert t0 is not None and t1 is not None
    assert t0.id != t1.id  # 跨天轮换
    assert select_exploration_topic(settings, now=day0).id == t0.id  # 同一天同一组


def test_topic_queries_merge_into_sources():
    topic = ExplorationTopic(
        id="failure_learning",
        queries_by_source={"twitter": ["复盘"], "v2ex": ["失败复盘"]},
        github_users=["octocat"],
    )
    settings = Settings(
        sources=SourcesSettings(
            twitter=SourceTwitterPlan(queries=["agent"], enabled=True),
            github=SourceGithubPlan(users=["jackwener"], enabled=True),
            v2ex=SourceV2exPlan(queries=["工具"], enabled=True),
        )
    )
    ctx = build_context_by_source(settings, all_sources=True, topic=topic)
    # 用户固定查询优先，主题查询追加去重。
    assert ctx[Source.TWITTER].queries == ["agent", "复盘"]
    assert ctx[Source.V2EX].queries == ["工具", "失败复盘"]
    # GitHub 用 github_users（登录名），不用主题词。
    assert ctx[Source.GITHUB].queries == ["jackwener", "octocat"]
