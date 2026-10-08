"""MindMap 系列模型契约校验。"""

import pytest
from pydantic import ValidationError

from finch.angle_discovery.mindmap_models import (
    MindMap,
    MindMapBranch,
    MindMapCombination,
    MindMapEdge,
    MindMapExpansion,
    MindMapNode,
    MindMapQuestion,
    MindMapSeed,
)


def _node(**o):
    data = dict(id="n1", label="它减少了哪种学习成本？", source="原文观点", parent_id="n0")
    data.update(o)
    return MindMapNode(**data)


def test_node_rejects_empty_label():
    with pytest.raises(ValidationError):
        _node(label="")


def test_node_rejects_unknown_source():
    with pytest.raises(ValidationError):
        _node(source="随便")


def test_node_rejects_extra_field():
    with pytest.raises(ValidationError):
        _node(score=9)


def test_edge_relation_literal():
    e = MindMapEdge(from_id="n1", to_id="n2", relation="类比")
    assert e.relation == "类比"
    with pytest.raises(ValidationError):
        MindMapEdge(from_id="n1", to_id="n2", relation="随意")


def test_combination_requires_thesis():
    with pytest.raises(ValidationError):
        MindMapCombination(thesis="")


def test_combination_allows_empty_explanation():
    c = MindMapCombination(thesis="中心主张")
    assert c.node_a == ""
    assert c.connection_rationale == ""


def test_combination_rejects_extra_field():
    with pytest.raises(ValidationError):
        MindMapCombination(thesis="t", page=3)


def test_seed_requires_root_label():
    with pytest.raises(ValidationError):
        MindMapSeed(root_label="")


def test_branch_requires_dimension():
    with pytest.raises(ValidationError):
        MindMapBranch(dimension="")


def test_question_requires_label():
    with pytest.raises(ValidationError):
        MindMapQuestion(label="")


def test_expansion_defaults_empty():
    assert MindMapExpansion().nodes == []


def test_mindmap_defaults():
    m = MindMap()
    assert m.id == ""
    assert m.nodes == []
    assert m.combinations == []


def test_mindmap_rejects_extra_field():
    with pytest.raises(ValidationError):
        MindMap(score=5)
