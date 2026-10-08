"""MindMapService：seed/expand/connect 覆盖确定性字段 + prompt 注入。"""

import pytest

from finch.angle_discovery.mindmap_models import (
    MindMap,
    MindMapBranch,
    MindMapCombination,
    MindMapExpansion,
    MindMapNode,
    MindMapQuestion,
    MindMapSeed,
)
from finch.angle_discovery.mindmap_service import MindMapService, map_id
from finch.angle_discovery.service import AngleContext
from finch.ingest.resolver import ResolvedSource


class _Runner:
    def __init__(self, ret):
        self.ret = ret
        self.last_prompt = None
        self.calls = 0

    def run(self, prompt, output_model, **kw):
        self.last_prompt = prompt
        self.calls += 1
        return self.ret


def _source() -> ResolvedSource:
    return ResolvedSource(
        body="AI 让写代码更快，但交付没有同步变快。",
        content_hash="abc123",
        sample_size=1,
        source_type="file",
        source_ref="a.md",
    )


def _seed() -> MindMapSeed:
    return MindMapSeed(
        root_label="AI 让写代码更快？",
        branches=[
            MindMapBranch(
                dimension="机制",
                dimension_source="AI 假设",
                questions=[
                    MindMapQuestion(label="它减少了哪种成本？", source="原文观点"),
                    MindMapQuestion(label="哪些成本值得保留？", source="AI 假设"),
                ],
            ),
            MindMapBranch(
                dimension="边界",
                questions=[MindMapQuestion(label="换一个条件还能做对吗？", source="待验证")],
            ),
        ],
    )


def _map() -> MindMap:
    return MindMap(
        id="map_x",
        source_type="file",
        source_ref="a.md",
        content_hash="abc123",
        root_label="AI 让写代码更快？",
        nodes=[
            MindMapNode(
                id="n0",
                label="AI 让写代码更快？",
                source="AI 假设",
                parent_id=None,
                expanded=True,
            ),
            MindMapNode(
                id="n1",
                label="机制",
                source="AI 假设",
                parent_id="n0",
                expanded=True,
            ),
            MindMapNode(
                id="n2",
                label="它减少了哪种成本？",
                source="原文观点",
                parent_id="n1",
            ),
        ],
    )


def test_map_id_shape_and_stability():
    assert map_id("abc123") == map_id("abc123")
    assert map_id("abc123") != map_id("other")
    assert map_id("abc123").startswith("map_")


def test_seed_assigns_ids_and_root():
    svc = MindMapService(_Runner(_seed()))
    m = svc.seed(_source())
    assert m.id == map_id("abc123")
    assert m.source_type == "file"
    assert m.source_ref == "a.md"
    assert m.content_hash == "abc123"
    assert m.root_label == "AI 让写代码更快？"
    root = m.nodes[0]
    assert root.id == "n0" and root.parent_id is None and root.expanded is True
    dims = [n for n in m.nodes if n.parent_id == "n0"]
    assert len(dims) == 2
    q = [n for n in m.nodes if n.parent_id == dims[0].id]
    assert [n.label for n in q] == ["它减少了哪种成本？", "哪些成本值得保留？"]
    ids = [n.id for n in m.nodes]
    assert ids == sorted(ids, key=lambda x: int(x[1:]))


def test_seed_prompt_includes_article_and_angle_library():
    runner = _Runner(_seed())
    MindMapService(runner).seed(_source())
    p = runner.last_prompt or ""
    assert "AI 让写代码更快" in p
    assert "第一性原理" in p
    assert "原文观点" in p


def test_seed_prompt_includes_context():
    runner = _Runner(_seed())
    ctx = AngleContext(reader="小团队", reader_problem="交付慢", goal="找到自己的判断")
    MindMapService(runner).seed(_source(), ctx)
    p = runner.last_prompt or ""
    assert "小团队" in p
    assert "交付慢" in p
    assert "找到自己的判断" in p


def _expansion() -> MindMapExpansion:
    return MindMapExpansion(
        nodes=[
            MindMapQuestion(label="换数据，还是换场景？", source="AI 假设"),
            MindMapQuestion(label="原来的解释在哪个条件失效？", source="待验证"),
        ]
    )


def test_expand_appends_children_and_marks_expanded():
    svc = MindMapService(_Runner(_expansion()))
    m = svc.expand(_map(), "n2")
    n2 = next(n for n in m.nodes if n.id == "n2")
    assert n2.expanded is True
    kids = [n for n in m.nodes if n.parent_id == "n2"]
    assert [n.label for n in kids] == ["换数据，还是换场景？", "原来的解释在哪个条件失效？"]
    assert len(m.nodes) == 5


def test_expand_with_predict_inserts_my_supplement():
    svc = MindMapService(_Runner(_expansion()))
    m = svc.expand(_map(), "n2", move="改条件", predict="我觉得会改变审查这一步")
    p = next(n for n in m.nodes if n.source == "我的补充")
    assert p.label == "我觉得会改变审查这一步"
    assert p.parent_id == "n2"
    kids = [n for n in m.nodes if n.parent_id == p.id]
    assert len(kids) == 2


def test_expand_unknown_node_raises():
    svc = MindMapService(_Runner(_expansion()))
    with pytest.raises(RuntimeError):
        svc.expand(_map(), "n99")


def test_expand_prompt_includes_path_and_move():
    runner = _Runner(_expansion())
    MindMapService(runner).expand(_map(), "n2", move="反例", predict="先预测")
    p = runner.last_prompt or ""
    assert "反例" in p
    assert "先预测" in p
    assert "它减少了哪种成本？" in p


def _combination() -> MindMapCombination:
    return MindMapCombination(
        connection_rationale="两者都暴露理解缺口。",
        incremental_value="比原文多了解释学习工具设计。",
        applicable_boundary="纯记忆任务不适用。",
        validation_gap="还需一个对照实验。",
        angle_title="让学习工具增加适度挑战",
        thesis="挑战暴露缺口，缺口驱动独立完成。",
    )


def test_connect_fills_node_ids_and_appends():
    svc = MindMapService(_Runner(_combination()))
    m = svc.connect(_map(), "n1", "n2")
    assert len(m.combinations) == 1
    c = m.combinations[0]
    assert c.node_a == "n1" and c.node_b == "n2"
    assert c.thesis == "挑战暴露缺口，缺口驱动独立完成。"


def test_connect_prompt_includes_both_labels():
    runner = _Runner(_combination())
    MindMapService(runner).connect(_map(), "n1", "n2")
    p = runner.last_prompt or ""
    assert "机制" in p
    assert "它减少了哪种成本？" in p


def test_connect_unknown_node_raises():
    svc = MindMapService(_Runner(_combination()))
    with pytest.raises(RuntimeError):
        svc.connect(_map(), "n1", "n99")
