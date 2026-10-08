"""MindMap Workspace 持久化。"""

from finch.angle_discovery.mindmap_models import MindMap, MindMapNode
from finch.angle_discovery.mindmap_repository import MindMapRepository
from finch.storage.workspace import Workspace


def _map(**kw) -> MindMap:
    base = dict(
        id="map_abc",
        source_type="text",
        content_hash="hash1",
        root_label="AI 让学习更容易？",
        nodes=[MindMapNode(id="n0", label="AI 让学习更容易？", parent_id=None, expanded=True)],
    )
    base.update(kw)
    return MindMap(**base)


def test_upsert_and_get(tmp_path):
    repo = MindMapRepository(Workspace(tmp_path))
    repo.upsert(_map())
    got = repo.get("map_abc")
    assert got is not None
    assert got.content_hash == "hash1"
    assert got.root_label == "AI 让学习更容易？"


def test_upsert_overwrites_same_id(tmp_path):
    repo = MindMapRepository(Workspace(tmp_path))
    repo.upsert(_map(content_hash="h1"))
    repo.upsert(_map(content_hash="h2"))
    assert repo.get("map_abc").content_hash == "h2"


def test_list_ids(tmp_path):
    repo = MindMapRepository(Workspace(tmp_path))
    repo.upsert(_map())
    repo.upsert(_map(id="map_def"))
    assert set(repo.list()) == {"map_abc", "map_def"}


def test_get_missing_returns_none(tmp_path):
    repo = MindMapRepository(Workspace(tmp_path))
    assert repo.get("missing") is None
