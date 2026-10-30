"""Skill-first shadow thin tools: candidates / validate-upsert / journal / compare."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from finch.problem_discovery.discover import (
    PROBLEM_VERSION,
    ProblemDraft,
    ProblemEvidenceDraft,
    ProblemsExtraction,
    discover_problems,
)
from finch.problem_discovery.ids import discussion_id_for
from finch.problem_discovery.importing import import_discussion
from finch.problem_discovery.repository import (
    DiscoveryRunRepository,
    DiscussionRepository,
    ProblemRepository,
)
from finch.problem_discovery.shadow import (
    JournalEntry,
    compare_runs,
    export_candidates,
    journal_append,
    journal_show,
    validate_upsert,
)
from finch.sources.models import AuthorIdentity, RawArtifact, Source
from finch.sources.store import ArtifactRepository
from finch.storage.workspace import Workspace


def _now() -> datetime:
    return datetime(2026, 10, 30, 18, 0, tzinfo=UTC)


def _art(aid: str, text: str) -> RawArtifact:
    from finch.sources.fingerprint import content_fingerprint

    return RawArtifact(
        artifact_id=aid,
        source=Source.GITHUB,
        source_type="issue",
        source_id=aid.split(":")[-1],
        canonical_url=f"https://github.com/a/b/issues/{aid.split(':')[-1]}",
        author_identity=AuthorIdentity(platform="github", handle="u"),
        text=text,
        retrieved_at=_now(),
        capture_method="gh",
        content_fingerprint=content_fingerprint(text, url=""),
    )


def test_export_candidates_fingerprint_matches_discover_pool(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    arts = ArtifactRepository(ws)
    arts.upsert(_art("github:issue:1", "session restore fails after crash"))
    export = export_candidates(
        artifact_repo=arts,
        discussion_repo=DiscussionRepository(ws),
        version=PROBLEM_VERSION,
        now=_now(),
    )
    assert export.budget_stats["source_count"] == 1
    assert export.fingerprint
    assert export.sources[0]["id"] == "github:issue:1"


def test_validate_upsert_shadow_keeps_good_drops_bad_quote(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    arts = ArtifactRepository(ws)
    arts.upsert(_art("github:issue:1", "session restore fails after crash"))
    drafts = ProblemsExtraction(
        problems=[
            ProblemDraft(
                title="session restore fails",
                evidence=[
                    ProblemEvidenceDraft(
                        source_ref="github:issue:1",
                        quote="session restore fails",
                        url="https://evil.example/fake",
                    )
                ],
            ),
            ProblemDraft(
                title="invented cite",
                evidence=[
                    ProblemEvidenceDraft(
                        source_ref="github:issue:1",
                        quote="this quote is not in the source text at all",
                    )
                ],
            ),
        ]
    )
    result = validate_upsert(
        drafts,
        artifact_repo=arts,
        discussion_repo=DiscussionRepository(ws),
        problem_repo=ProblemRepository(ws, shadow=True),
        run_repo=DiscoveryRunRepository(ws, shadow=True),
        now=_now(),
    )
    assert result.kept_count == 2  # both titles kept; second has empty evidence
    good = next(p for p in result.problems if p.title == "session restore fails")
    assert good.evidence[0].url.startswith("https://github.com/")  # LLM url ignored
    invented = next(p for p in result.problems if p.title == "invented cite")
    assert invented.evidence == []
    assert any("未验证" in u for u in invented.unknowns)
    assert ProblemRepository(ws, shadow=False).list_all() == []  # control clean
    assert len(ProblemRepository(ws, shadow=True).list_all()) == 2


def test_validate_upsert_rejects_unknown_source_ref(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    arts = ArtifactRepository(ws)
    arts.upsert(_art("github:issue:1", "real text here"))
    drafts = {
        "problems": [
            {
                "title": "fake ref",
                "evidence": [
                    {"source_ref": "missing:ref", "quote": "real text here", "claim": "c"}
                ],
            }
        ]
    }
    result = validate_upsert(
        drafts,
        artifact_repo=arts,
        discussion_repo=DiscussionRepository(ws),
        problem_repo=ProblemRepository(ws, shadow=True),
        run_repo=DiscoveryRunRepository(ws, shadow=True),
        now=_now(),
    )
    assert result.kept_count == 1
    assert result.problems[0].evidence == []


def test_compare_control_vs_shadow(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    arts = ArtifactRepository(ws)
    text = "agent stuck after tool calling failed"
    arts.upsert(_art("github:issue:9", text))

    class _Runner:
        def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
            return ProblemsExtraction(
                problems=[
                    ProblemDraft(
                        title="agent stuck",
                        evidence=[
                            ProblemEvidenceDraft(
                                source_ref="github:issue:9", quote="agent stuck"
                            )
                        ],
                    )
                ]
            )

    discover_problems(
        _Runner(),
        problem_repo=ProblemRepository(ws, shadow=False),
        artifact_repo=arts,
        discussion_repo=DiscussionRepository(ws),
        run_repo=DiscoveryRunRepository(ws, shadow=False),
        version=PROBLEM_VERSION,
        force=True,
        now=_now(),
    )
    validate_upsert(
        ProblemsExtraction(
            problems=[
                ProblemDraft(
                    title="agent stuck",
                    evidence=[
                        ProblemEvidenceDraft(
                            source_ref="github:issue:9", quote="tool calling failed"
                        )
                    ],
                ),
                ProblemDraft(
                    title="extra shadow only",
                    evidence=[
                        ProblemEvidenceDraft(
                            source_ref="github:issue:9", quote="agent stuck"
                        )
                    ],
                ),
            ]
        ),
        artifact_repo=arts,
        discussion_repo=DiscussionRepository(ws),
        problem_repo=ProblemRepository(ws, shadow=True),
        run_repo=DiscoveryRunRepository(ws, shadow=True),
        now=_now(),
    )
    cmp = compare_runs(
        control_run_repo=DiscoveryRunRepository(ws, shadow=False),
        shadow_run_repo=DiscoveryRunRepository(ws, shadow=True),
        control_problem_repo=ProblemRepository(ws, shadow=False),
        shadow_problem_repo=ProblemRepository(ws, shadow=True),
    )
    assert cmp.fingerprint_match is True
    assert len(cmp.overlap_ids) == 1
    assert len(cmp.only_shadow) == 1


def test_journal_append_and_show(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    journal_append(
        ws, JournalEntry(kind="note", detail={"msg": "hello"}), shadow=True
    )
    entries = journal_show(ws, shadow=True)
    assert len(entries) == 1
    assert entries[0].detail["msg"] == "hello"


def test_import_fixture_style_discussions(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    refs = import_discussion(
        ws,
        text="Browser-use session restore fails when the tab crashes mid-task.",
        title="session restore",
        url="https://example.com/discuss/1",
    )
    assert refs
    assert DiscussionRepository(ws).get(refs[0]) is not None
    assert refs[0] == discussion_id_for(
        "https://example.com/discuss/1",
        "Browser-use session restore fails when the tab crashes mid-task.",
    )
