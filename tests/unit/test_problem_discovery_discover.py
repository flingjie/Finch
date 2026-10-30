"""问题发现 discover 单测：build_problem 门禁 + discover 幂等 / 指纹 / 失败缓存。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from finch.problem_discovery.discover import (
    PROBLEM_VERSION,
    ProblemDraft,
    ProblemEvidenceDraft,
    ProblemsExtraction,
    build_problem,
    discover_problems,
)
from finch.problem_discovery.ids import problem_id_for
from finch.problem_discovery.models import EvidenceLevel, Problem, ProblemStatus
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


def _art(
    artifact_id: str,
    text: str,
    *,
    retrieved_at: datetime | None = None,
    url: str = "https://github.com/a/b/issues/1",
) -> RawArtifact:
    from finch.sources.fingerprint import content_fingerprint

    return RawArtifact(
        artifact_id=artifact_id,
        source=Source.GITHUB,
        source_type="issue",
        source_id="1",
        canonical_url=url,
        author_identity=AuthorIdentity(platform="github", handle="u"),
        text=text,
        retrieved_at=retrieved_at or datetime.now(UTC),
        capture_method="gh",
        content_fingerprint=content_fingerprint(text, url=url),
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
            ProblemEvidenceDraft(source_ref="s", quote="", claim="empty quote"),
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


def test_build_problem_ignores_llm_url_and_caps_level():
    draft = ProblemDraft(
        title="t",
        evidence=[
            ProblemEvidenceDraft(
                source_ref="s",
                quote="traceable",
                url="https://evil.example/fake",
                evidence_level="e4",
            )
        ],
    )
    p = build_problem(
        draft,
        source_texts={"s": "this text is traceable"},
        urls={"s": "https://x.com/1"},
        now=_now(),
    )
    assert p is not None
    assert p.problem_id == problem_id_for("t")
    assert p.source_refs == ["s"]
    assert p.evidence[0].url == "https://x.com/1"
    assert p.evidence[0].evidence_level == EvidenceLevel.E1  # public single-source cap
    assert p.status == ProblemStatus.NEW


def test_build_problem_marketing_caps_to_e0():
    draft = ProblemDraft(
        title="t",
        evidence=[
            ProblemEvidenceDraft(
                source_ref="s",
                quote="traceable",
                evidence_level="e2",
                is_marketing=True,
            )
        ],
    )
    p = build_problem(
        draft, source_texts={"s": "this text is traceable"}, urls={"s": "u"}, now=_now()
    )
    assert p.evidence[0].evidence_level == EvidenceLevel.E0


def test_build_problem_two_refs_allow_e2():
    draft = ProblemDraft(
        title="t",
        evidence=[
            ProblemEvidenceDraft(
                source_ref="s1", quote="alpha", evidence_level="e2", author="a"
            ),
            ProblemEvidenceDraft(
                source_ref="s2", quote="beta", evidence_level="e3", author="b"
            ),
        ],
    )
    p = build_problem(
        draft,
        source_texts={"s1": "alpha here", "s2": "beta there"},
        urls={"s1": "u1", "s2": "u2"},
        now=_now(),
    )
    assert {e.evidence_level for e in p.evidence} == {EvidenceLevel.E2}


class _BoomRunner:
    def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
        raise AssertionError("should not be called when idempotent")


def _discover_kwargs(ws: Workspace) -> dict:
    return dict(
        problem_repo=ProblemRepository(ws),
        artifact_repo=ArtifactRepository(ws),
        discussion_repo=DiscussionRepository(ws),
        run_repo=DiscoveryRunRepository(ws),
        version=PROBLEM_VERSION,
        lookback_hours=720,
        max_sources=40,
        max_chars_per_source=4000,
        batch_size=10,
    )


def test_discover_problems_reuses_cached(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    artifact_repo = ArtifactRepository(ws)
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

    kwargs = _discover_kwargs(ws)
    first = discover_problems(_Runner(), **kwargs)
    assert first is not None
    assert len(first) == 1
    second = discover_problems(_BoomRunner(), **kwargs)
    assert second is not None
    assert [p.problem_id for p in second] == [p.problem_id for p in first]


def test_discover_replay_returns_only_run_problem_ids(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    artifact_repo = ArtifactRepository(ws)
    problem_repo = ProblemRepository(ws)
    artifact_repo.upsert(_art("github:issue:1", "crashes on import"))

    class _Runner:
        def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
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

    kwargs = _discover_kwargs(ws)
    discover_problems(_Runner(), **kwargs)
    # Stale card not part of the run must not appear on replay.
    stale = Problem(problem_id="prob_stale", title="stale")
    problem_repo.upsert(stale)
    replayed = discover_problems(_BoomRunner(), **kwargs)
    assert replayed is not None
    assert all(p.problem_id != "prob_stale" for p in replayed)
    assert len(replayed) == 1


def test_discover_body_change_invalidates_fingerprint(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    artifact_repo = ArtifactRepository(ws)
    calls = {"n": 0}

    class _Runner:
        def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
            calls["n"] += 1
            return ProblemsExtraction(problems=[])

    art = _art("github:issue:1", "first body")
    artifact_repo.upsert(art)
    kwargs = _discover_kwargs(ws)
    discover_problems(_Runner(), **kwargs)
    assert calls["n"] == 1
    discover_problems(_BoomRunner(), **kwargs)  # cache hit
    # Same artifact_id, different body → new fingerprint → runner called again.
    artifact_repo.upsert(_art("github:issue:1", "second body changed"))
    discover_problems(_Runner(), **kwargs)
    assert calls["n"] == 2


def test_discover_eval_failed_does_not_cache(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    ArtifactRepository(ws).upsert(_art("github:issue:1", "crashes on import"))
    run_repo = DiscoveryRunRepository(ws)
    calls = {"n": 0}

    class _FailRunner:
        def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
            calls["n"] += 1
            raise RuntimeError("llm down")

    kwargs = _discover_kwargs(ws)
    assert discover_problems(_FailRunner(), **kwargs) is None
    assert run_repo.latest() is None
    assert discover_problems(_FailRunner(), **kwargs) is None
    assert calls["n"] == 2


def test_discover_respects_max_chars_budget(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    ArtifactRepository(ws).upsert(_art("github:issue:1", "ABCDEFGHIJ" * 10))
    seen: list[str] = []

    class _Runner:
        def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
            seen.append(prompt)
            return ProblemsExtraction(problems=[])

    kwargs = _discover_kwargs(ws)
    kwargs["max_chars_per_source"] = 20
    discover_problems(_Runner(), **kwargs)
    assert len(seen) == 1
    assert "ABCDEFGHIJABCDEFGHIJ" in seen[0]
    assert "ABCDEFGHIJ" * 10 not in seen[0]


def test_discover_lookback_excludes_old_sources(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    old = _now() - timedelta(days=40)
    ArtifactRepository(ws).upsert(
        _art("github:issue:old", "old crash text", retrieved_at=old)
    )
    out = discover_problems(
        _BoomRunner(),
        **{
            **_discover_kwargs(ws),
            "lookback_hours": 24,
            "now": _now(),
        },
    )
    assert out is None


def test_discover_problems_no_sources_returns_none(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    out = discover_problems(_BoomRunner(), **_discover_kwargs(ws))
    assert out is None
