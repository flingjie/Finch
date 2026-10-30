"""problem_discovery.sources + research_topics merge / sync wiring."""

from __future__ import annotations

from finch.problem_discovery.sync import merge_problem_sources
from finch.settings import (
    ProblemDiscoverySettings,
    ResearchTopic,
    Settings,
    SourceGithubPlan,
    SourceHackerNewsPlan,
    SourceRedditPlan,
    SourcesSettings,
)
from finch.sources.models import Source
from finch.sources.query_plan import build_context_by_source


def test_merge_research_topics_enables_sources():
    pd = ProblemDiscoverySettings(
        sources=SourcesSettings(
            github=SourceGithubPlan(enabled=False, mode="disabled"),
            reddit=SourceRedditPlan(enabled=False, mode="disabled"),
            hackernews=SourceHackerNewsPlan(enabled=False, mode="disabled"),
        ),
        research_topics=[
            ResearchTopic(
                id="t1",
                signals=["agent stuck"],
                queries_by_source={
                    "github_issue_queries": ["agent stuck type:issue"],
                    "github_watchlist": ["openai/codex"],
                    "reddit": ["tool calling failed"],
                },
            )
        ],
    )
    merged = merge_problem_sources(pd)
    assert merged.github.enabled is True
    assert "agent stuck type:issue" in merged.github.issue_queries
    assert "openai/codex" in merged.github.watchlist
    assert merged.reddit.enabled is True
    assert "agent stuck" in merged.reddit.queries
    assert "tool calling failed" in merged.reddit.queries
    assert merged.hackernews.enabled is True
    assert "agent stuck" in merged.hackernews.queries


def test_build_context_includes_watchlist_extra():
    settings = Settings()
    pd = ProblemDiscoverySettings(
        sources=SourcesSettings(
            github=SourceGithubPlan(
                enabled=True,
                mode="query",
                watchlist=["a/b"],
                issue_queries=["crash type:issue"],
            )
        )
    )
    merged = merge_problem_sources(pd)
    contexts = build_context_by_source(settings, all_sources=True, sources=merged)
    assert Source.GITHUB in contexts
    assert contexts[Source.GITHUB].extra["watchlist"] == ["a/b"]
    assert contexts[Source.GITHUB].extra["issue_queries"] == ["crash type:issue"]
