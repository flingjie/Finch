"""问题发现领域仓储单测：读写 roundtrip。"""

from __future__ import annotations

from finch.problem_discovery.models import ImportedDiscussion, Problem
from finch.problem_discovery.repository import DiscussionRepository, ProblemRepository
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
