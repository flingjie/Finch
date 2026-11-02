"""问题发现领域仓储单测：读写 roundtrip。"""

from __future__ import annotations

from finch.problem_discovery.models import (
    Admission,
    ImportedDiscussion,
    Problem,
    ProblemEvidence,
)
from finch.problem_discovery.repository import (
    DiscussionRepository,
    ProblemRepository,
    filter_by_admission,
    filter_default_focus,
)
from finch.storage.workspace import Workspace


def test_problem_repository_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = ProblemRepository(ws)
    repo.upsert(Problem(problem_id="prob_x", title="Agent 失败后难以复现"))
    assert repo.get("prob_x").title == "Agent 失败后难以复现"
    assert [p.problem_id for p in repo.list_all()] == ["prob_x"]


def test_discussion_repository_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = DiscussionRepository(ws)
    repo.upsert(ImportedDiscussion(discussion_id="disc_x", kind="url", url="https://x.com/a/1"))
    assert repo.get("disc_x").url == "https://x.com/a/1"
    assert [d.discussion_id for d in repo.list_all()] == ["disc_x"]


def _p(pid: str, admission: Admission, conflicting: bool = False) -> Problem:
    ev = [ProblemEvidence(source_ref="s", quote="q")]
    if conflicting:
        ev.append(
            ProblemEvidence(
                source_ref="s2",
                quote="q2",
                claim_support="conflicting",
                claim_support_reason="r",
            )
        )
    return Problem(problem_id=pid, title=pid, admission=admission, evidence=ev)


def test_filter_default_uses_source_checked():
    items = [
        _p("a", Admission.SOURCE_CHECKED),
        _p("b", Admission.QUARANTINED),
        _p("c", Admission.ACCEPTED),
    ]
    assert [p.problem_id for p in filter_by_admission(items)] == ["a", "c"]


def test_filter_legacy_verified_token_still_works():
    items = [_p("a", Admission.SOURCE_CHECKED)]
    assert len(filter_by_admission(items, "verified,accepted")) == 1


def test_default_focus_excludes_conflicting():
    items = [
        _p("clean", Admission.SOURCE_CHECKED),
        _p("conflicted", Admission.SOURCE_CHECKED, conflicting=True),
        _p("accepted_conflicted", Admission.ACCEPTED, conflicting=True),
    ]
    assert [p.problem_id for p in filter_default_focus(items)] == ["clean"]
