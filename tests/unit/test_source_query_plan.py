"""Unit tests for per-source query plan builder."""

from __future__ import annotations

from finch.settings import (
    Settings,
    SourceGithubPlan,
    SourcesSettings,
    SourceTwitterPlan,
    SourceV2exPlan,
    SourceWeixinPlan,
)
from finch.sources.models import Source
from finch.sources.query_plan import build_context_by_source


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
