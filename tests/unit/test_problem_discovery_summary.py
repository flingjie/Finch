"""问题发现 summary 单测：确定性概览计数与来源 scope。"""

from __future__ import annotations

from datetime import UTC, datetime

from finch.problem_discovery.models import Problem, ProblemEvidence
from finch.problem_discovery.repository import (
    DiscoveryRunRepository,
    DiscussionRepository,
    ProblemRepository,
)
from finch.problem_discovery.summary import build_summary, summarize_source_scope
from finch.sources.models import AuthorIdentity, RawArtifact, Source
from finch.sources.store import ArtifactRepository
from finch.storage.workspace import Workspace


def _art(artifact_id: str, text: str, url: str) -> RawArtifact:
    return RawArtifact(
        artifact_id=artifact_id,
        source=Source.GITHUB,
        source_type="issue",
        source_id=artifact_id,
        canonical_url=url,
        author_identity=AuthorIdentity(platform="github", handle="u"),
        text=text,
        retrieved_at=datetime.now(UTC),
        capture_method="gh",
    )


def test_summarize_source_scope_single_repo():
    urls = ["https://github.com/a/b/issues/1", "https://github.com/a/b/issues/2"]
    assert summarize_source_scope(urls) == "single_repo"


def test_summarize_source_scope_mixed():
    urls = ["https://github.com/a/b/issues/1", "https://x.com/a/1"]
    assert summarize_source_scope(urls) == "mixed"


def test_summarize_source_scope_none():
    assert summarize_source_scope([]) == "none"
    assert summarize_source_scope([""]) == "none"


def test_build_summary_counts_and_scope(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    problem_repo = ProblemRepository(ws)
    artifact_repo = ArtifactRepository(ws)
    discussion_repo = DiscussionRepository(ws)
    run_repo = DiscoveryRunRepository(ws)

    artifact_repo.upsert(_art("github:issue:1", "crashes", "https://github.com/a/b/issues/1"))
    artifact_repo.upsert(_art("github:issue:2", "hangs", "https://github.com/a/b/issues/2"))

    problem_repo.upsert(
        Problem(
            problem_id="prob_1",
            title="crashes",
            user_type="dev",
            evidence=[
                ProblemEvidence(source_ref="github:issue:1", quote="crashes"),
                ProblemEvidence(source_ref="github:issue:2", quote="hangs"),
            ],
        )
    )
    problem_repo.upsert(Problem(problem_id="prob_2", title="no evidence"))  # 缺证据 + 缺角色

    summary = build_summary(
        problem_repo=problem_repo,
        artifact_repo=artifact_repo,
        discussion_repo=discussion_repo,
        run_repo=run_repo,
    )
    assert summary.total_cards == 2
    assert summary.total_sources == 2
    assert summary.source_scope == "single_repo"
    assert summary.source_urls == [
        "https://github.com/a/b/issues/1",
        "https://github.com/a/b/issues/2",
    ]
    assert summary.cards_without_evidence == ["prob_2"]
    assert summary.cards_without_role == ["prob_2"]
    assert summary.groups_available is False
    assert summary.latest_run_created_at == ""
    assert summary.latest_run_card_count == 0
