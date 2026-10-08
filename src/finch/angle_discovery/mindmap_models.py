"""article-angle-discovery · 发散思维导图数据模型。

``MindMap`` 的确定性字段（``id``/``source_type``/``source_ref``/``content_hash``、节点
``id``/``parent_id``/``expanded``、组合 ``node_a``/``node_b``）由 service 覆盖；模型只产判断字段。
禁止总分/数值评分字段。节点是「问题」，不是分类目录；跨分支连接走 Edge，组合走 Combination。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NodeSource = Literal["原文观点", "我的补充", "AI 假设", "待验证"]
EdgeRelation = Literal["支持", "冲突", "类比", "推测"]
MoveKind = Literal["追问", "改条件", "反例"]


class MindMapNode(BaseModel):
    """导图节点：一条问题（或维度名），带来源标注。``id``/``parent_id``/``expanded`` 由代码填。"""

    model_config = ConfigDict(extra="forbid")

    id: str = ""  # n0/n1/...，图内稳定，创建顺序分配
    label: str = Field(min_length=1)
    source: NodeSource = "AI 假设"
    parent_id: str | None = None
    expanded: bool = False


class MindMapEdge(BaseModel):
    """跨分支连线，标明连线性质。"""

    model_config = ConfigDict(extra="forbid")

    from_id: str = Field(min_length=1)
    to_id: str = Field(min_length=1)
    relation: EdgeRelation = "推测"


class MindMapCombination(BaseModel):
    """「连两个节点」的产物：四个简短说明 + 候选角度。``node_a``/``node_b`` 由代码填。"""

    model_config = ConfigDict(extra="forbid")

    node_a: str = ""
    node_b: str = ""
    connection_rationale: str = ""  # 连接理由
    incremental_value: str = ""  # 新增价值
    applicable_boundary: str = ""  # 适用边界
    validation_gap: str = ""  # 验证缺口
    angle_title: str = ""  # 候选角度标题
    thesis: str = Field(min_length=1)  # 中心主张


class MindMapQuestion(BaseModel):
    """发散/展开产出的一条问题节点（label + 来源），id 由 service 分配。"""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    source: NodeSource = "AI 假设"


class MindMapBranch(BaseModel):
    """``new`` 的一个思考维度分支：维度名 + 它下面的问题。"""

    model_config = ConfigDict(extra="forbid")

    dimension: str = Field(min_length=1)
    dimension_source: NodeSource = "AI 假设"
    questions: list[MindMapQuestion] = Field(default_factory=list)


class MindMapSeed(BaseModel):
    """``new`` 的发散结果（判断字段）：根 + 4–6 个维度分支。"""

    model_config = ConfigDict(extra="forbid")

    root_label: str = Field(min_length=1)
    branches: list[MindMapBranch] = Field(default_factory=list)


class MindMapExpansion(BaseModel):
    """``expand`` 的结果（判断字段）：沿一个节点展开的下一层问题。"""

    model_config = ConfigDict(extra="forbid")

    nodes: list[MindMapQuestion] = Field(default_factory=list)


class MindMap(BaseModel):
    """一张可继续探索的思维导图（持久化对象）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = ""  # map_{content_hash 派生}
    source_type: Literal["text", "file", "url"] = "text"
    source_ref: str | None = None
    content_hash: str = ""
    root_label: str = ""  # 与根节点 n0 的 label 一致（渲染/标题便捷字段）
    nodes: list[MindMapNode] = Field(default_factory=list)
    edges: list[MindMapEdge] = Field(default_factory=list)
    combinations: list[MindMapCombination] = Field(default_factory=list)
