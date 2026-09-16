"""Unit tests for per-source query plan builder."""

from __future__ import annotations

from finch.settings import Settings, SourceGithubPlan, SourcesSettings, SourceTwitterPlan
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


def test_twitter_falls_back_to_legacy_queries():
    settings = Settings(
        twitter={"queries": [{"id": "q1", "text": '"agent harness"'}]},  # type: ignore[arg-type]
    )
    ctx = build_context_by_source(settings, all_sources=True)
    assert '"agent harness"' in ctx[Source.TWITTER].queries
