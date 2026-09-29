"""Tests for IdeaExplorationRepository（文件工作区持久化）。"""

from finch.ideas.models import IdeaExploration, IdeaGenerator
from finch.storage.repositories import IdeaExplorationRepository
from finch.storage.workspace import Workspace


def _exploration(id: str = "expl_abc12345") -> IdeaExploration:
    return IdeaExploration(
        id=id,
        origin="practice",
        source_kind="commit",
        facts=["f"],
        source_refs=[],
        generator=IdeaGenerator(skill="idea-discovery", version="2.0.0"),
    )


def test_upsert_and_get_round_trip(tmp_path):
    ws = Workspace(tmp_path)
    repo = IdeaExplorationRepository(ws)
    repo.upsert(_exploration())
    got = repo.get("expl_abc12345")
    assert got is not None
    assert got.facts == ["f"]


def test_get_missing_returns_none(tmp_path):
    repo = IdeaExplorationRepository(Workspace(tmp_path))
    assert repo.get("expl_nope") is None


def test_list_all(tmp_path):
    ws = Workspace(tmp_path)
    repo = IdeaExplorationRepository(ws)
    repo.upsert(_exploration("expl_a"))
    repo.upsert(_exploration("expl_b"))
    ids = sorted(e.id for e in repo.list_all())
    assert ids == ["expl_a", "expl_b"]
