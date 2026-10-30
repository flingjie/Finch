"""问题发现 discover 单测：build_problem 门禁 + discover 幂等。"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from finch.problem_discovery.discover import (
    ProblemDraft,
    ProblemEvidenceDraft,
    ProblemsExtraction,
    build_problem,
    discover_problems,
)
from finch.problem_discovery.ids import problem_id_for
from finch.problem_discovery.models import ProblemStatus
from finch.problem_discovery.repository import (
    DiscoveryRunRepository,
    DiscussionRepository,
    ProblemRepository,
)
from finch.sources.models import AuthorIdentity, RawArtifact, Source
from finch.sources.store import ArtifactRepository
from finch.storage.workspace import Workspace


def _now() -> datetime:
    return datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def _art(artifact_id: str, text: str) -> RawArtifact:
    return RawArtifact(
        artifact_id=artifact_id,
        source=Source.GITHUB,
        source_type="issue",
        source_id="1",
        canonical_url="https://github.com/a/b/issues/1",
        author_identity=AuthorIdentity(platform="github", handle="u"),
        text=text,
        retrieved_at=datetime.now(UTC),
        capture_method="gh",
    )


def test_build_problem_requires_title():
    assert build_problem(ProblemDraft(), source_texts={"s": "text"}, urls={}, now=_now()) is None


def test_build_problem_drops_invalid_or_untraceable_evidence():
    draft = ProblemDraft(
        title="t",
        evidence=[
            ProblemEvidenceDraft(source_ref="s", quote="traceable", claim="c1"),
            ProblemEvidenceDraft(source_ref="missing", quote="x", claim="c2"),
            ProblemEvidenceDraft(source_ref="s", quote="not in text", claim="c3"),
        ],
    )
    p = build_problem(draft, source_texts={"s": "this text is traceable"}, urls={}, now=_now())
    assert p is not None
    assert [e.source_ref for e in p.evidence] == ["s"]


def test_build_problem_marks_unverified_when_no_evidence():
    draft = ProblemDraft(
        title="t", evidence=[ProblemEvidenceDraft(source_ref="missing", quote="x")]
    )
    p = build_problem(draft, source_texts={"s": "text"}, urls={}, now=_now())
    assert p.evidence == []
    assert any("未验证" in u for u in p.unknowns)


def test_build_problem_sets_ids_refs_url_and_status():
    draft = ProblemDraft(
        title="t", evidence=[ProblemEvidenceDraft(source_ref="s", quote="traceable")]
    )
    p = build_problem(
        draft,
        source_texts={"s": "this text is traceable"},
        urls={"s": "https://x.com/1"},
        now=_now(),
    )
    assert p.problem_id == problem_id_for("t")
    assert p.source_refs == ["s"]
    assert p.evidence[0].url == "https://x.com/1"
    assert p.status == ProblemStatus.NEW


class _BoomRunner:
    def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
        raise AssertionError("should not be called when idempotent")


def test_discover_problems_reuses_cached(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    problem_repo = ProblemRepository(ws)
    artifact_repo = ArtifactRepository(ws)
    discussion_repo = DiscussionRepository(ws)
    run_repo = DiscoveryRunRepository(ws)
    artifact_repo.upsert(_art("github:issue:1", "crashes on import"))

    class _Runner:
        def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
            assert output_model is ProblemsExtraction
            return ProblemsExtraction(
                problems=[
                    ProblemDraft(
                        title="crashes",
                        evidence=[
                            ProblemEvidenceDraft(
                                source_ref="github:issue:1", quote="crashes on import"
                            )
                        ],
                    )
                ]
            )

    kwargs = dict(
        problem_repo=problem_repo,
        artifact_repo=artifact_repo,
        discussion_repo=discussion_repo,
        run_repo=run_repo,
    )
    first = discover_problems(_Runner(), **kwargs)
    assert first is not None
    assert len(first) == 1
    second = discover_problems(_BoomRunner(), **kwargs)
    assert second is not None
    assert [p.problem_id for p in second] == [p.problem_id for p in first]


def test_discover_problems_no_sources_returns_none(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    out = discover_problems(
        _BoomRunner(),
        problem_repo=ProblemRepository(ws),
        artifact_repo=ArtifactRepository(ws),
        discussion_repo=DiscussionRepository(ws),
        run_repo=DiscoveryRunRepository(ws),
    )
    assert out is None
