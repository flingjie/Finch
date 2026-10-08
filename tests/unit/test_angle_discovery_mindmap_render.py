"""MindMap → Mermaid 渲染。"""

from finch.angle_discovery.mindmap_models import (
    MindMap,
    MindMapCombination,
    MindMapNode,
)
from finch.angle_discovery.mindmap_render import render_combination, render_mindmap


def _map() -> MindMap:
    return MindMap(
        id="map_x",
        root_label="AI 让学习更容易？",
        nodes=[
            MindMapNode(
                id="n0",
                label="AI 让学习更容易？",
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
                label="它减少了哪种学习成本？",
                source="原文观点",
                parent_id="n1",
            ),
            MindMapNode(
                id="n3",
                label="哪些困难值得保留？",
                source="AI 假设",
                parent_id="n1",
            ),
        ],
    )


def test_render_mindmap_basic():
    out = render_mindmap(_map())
    assert out.startswith("```mermaid")
    assert "mindmap" in out
    assert "root((AI 让学习更容易？))" in out
    assert "机制" in out
    assert "它减少了哪种学习成本？〔原文观点〕" in out
    assert "哪些困难值得保留？〔AI 假设〕" in out


def test_render_mindmap_depth_collapses():
    out = render_mindmap(_map(), max_depth=1)
    assert "机制" in out
    assert "它减少了哪种学习成本？" not in out
    assert "2 个待展开" in out


def test_render_mindmap_full_shows_leaves():
    out = render_mindmap(_map())
    assert "它减少了哪种学习成本？" in out
    assert "待展开" not in out


def test_render_combination():
    combo = MindMapCombination(
        node_a="n2",
        node_b="n3",
        angle_title="让学习工具增加适度挑战",
        thesis="挑战暴露缺口。",
        connection_rationale="两者都暴露理解缺口。",
        incremental_value="补上工具设计。",
        applicable_boundary="纯记忆任务不适用。",
        validation_gap="还需对照实验。",
    )
    out = render_combination(combo, {"n2": "看懂却做不出", "n3": "游戏关卡"})
    assert "flowchart TD" in out
    assert "看懂却做不出" in out
    assert "游戏关卡" in out
    assert "让学习工具增加适度挑战" in out
    assert "连接理由：两者都暴露理解缺口。" in out
    assert "验证缺口：还需对照实验。" in out
