"""CLI tests for finch angles map。"""

import hashlib

from typer.testing import CliRunner

from finch import cli
from finch.angle_discovery.mindmap_service import map_id
from finch.cli import app
from finch.settings import Paths, Settings


def _settings(tmp_path):
    return Settings(paths=Paths(var_dir=tmp_path))


def _patch(monkeypatch, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)


def _text_id(text="hello") -> str:
    return map_id(hashlib.sha256(text.encode("utf-8")).hexdigest())


def _map():
    from finch.angle_discovery.mindmap_models import MindMap, MindMapNode

    return MindMap(
        id="map_x",
        source_type="text",
        content_hash="hash1",
        root_label="AI 让学习更容易？",
        nodes=[
            MindMapNode(
                id="n0",
                label="学习更容易？",
                source="AI 假设",
                parent_id=None,
                expanded=True,
            ),
            MindMapNode(id="n1", label="机制", source="AI 假设", parent_id="n0", expanded=True),
            MindMapNode(id="n2", label="成本降低？", source="原文观点", parent_id="n1"),
        ],
    )


class _FakeMapService:
    def __init__(self):
        self.expanded = None
        self.connected = None

    def seed(self, source, context=None):
        return _map().model_copy(
            update={
                "id": map_id(source.content_hash),
                "content_hash": source.content_hash,
                "source_type": source.source_type,
            }
        )

    def expand(self, m, node_id, move="追问", predict=""):
        self.expanded = node_id
        from finch.angle_discovery.mindmap_models import MindMapNode

        n = list(m.nodes) + [
            MindMapNode(
                id=f"n{len(m.nodes)}",
                label="数据 or 场景？",
                source="AI 假设",
                parent_id=node_id,
            )
        ]
        return m.model_copy(update={"nodes": n})

    def connect(self, m, node_a, node_b):
        self.connected = (node_a, node_b)
        from finch.angle_discovery.mindmap_models import MindMapCombination

        c = MindMapCombination(node_a=node_a, node_b=node_b, angle_title="组合", thesis="主张")
        return m.model_copy(update={"combinations": m.combinations + [c]})


def test_map_new_requires_exactly_one_source(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    r = CliRunner().invoke(app, ["angles", "map", "new"])
    assert r.exit_code == 1
    assert "exactly one of" in r.output


def test_map_new_text(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "MindMapService", lambda runner: _FakeMapService())
    r = CliRunner().invoke(app, ["angles", "map", "new", "--text", "hello"])
    assert r.exit_code == 0, r.output
    assert "# 思维导图" in r.output
    assert f"id: {_text_id()}" in r.output
    assert "mindmap" in r.output
    assert "机制" in r.output
    assert "节点：" in r.output


def test_map_new_rejects_duplicate(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "MindMapService", lambda runner: _FakeMapService())
    r1 = CliRunner().invoke(app, ["angles", "map", "new", "--text", "hello"])
    assert r1.exit_code == 0
    r2 = CliRunner().invoke(app, ["angles", "map", "new", "--text", "hello"])
    assert r2.exit_code == 1
    assert "already exists" in r2.output


def test_map_show_and_list(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "MindMapService", lambda runner: _FakeMapService())
    CliRunner().invoke(app, ["angles", "map", "new", "--text", "hello"])
    shown = CliRunner().invoke(app, ["angles", "map", "show", _text_id()])
    assert shown.exit_code == 0, shown.output
    assert "mindmap" in shown.output
    listed = CliRunner().invoke(app, ["angles", "map", "list"])
    assert listed.exit_code == 0
    assert _text_id() in listed.output


def test_map_expand(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    fake = _FakeMapService()
    monkeypatch.setattr(cli, "MindMapService", lambda runner: fake)
    CliRunner().invoke(app, ["angles", "map", "new", "--text", "hello"])
    r = CliRunner().invoke(app, ["angles", "map", "expand", _text_id(), "n2"])
    assert r.exit_code == 0, r.output
    assert fake.expanded == "n2"
    assert "数据 or 场景？" in r.output


def test_map_connect(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    fake = _FakeMapService()
    monkeypatch.setattr(cli, "MindMapService", lambda runner: fake)
    CliRunner().invoke(app, ["angles", "map", "new", "--text", "hello"])
    r = CliRunner().invoke(app, ["angles", "map", "connect", _text_id(), "n1", "n2"])
    assert r.exit_code == 0, r.output
    assert fake.connected == ("n1", "n2")
    assert "组合角度" in r.output
