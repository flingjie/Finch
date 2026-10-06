"""content-summary 数据模型。

``ContentSummary`` 的确定性字段（``id``/``source_type``/``source_refs``/``content_hash``）
由 service 覆盖；模型只产判断字段。禁止总分/数值评分字段。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EvidencePoint(BaseModel):
    """支撑观点的关键依据或例子，标注来源以区分作者观点 / 引用 / 回复。"""

    model_config = ConfigDict(extra="forbid")

    source: Literal["作者", "引用", "回复", "未标明"]
    content: str = Field(min_length=1)


class ContentSummary(BaseModel):
    """一篇帖子的内容摘要（即算即打印，默认落库）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    source_type: Literal["text", "file", "url"] = "text"
    source_refs: list[str] = Field(default_factory=list)
    content_hash: str = ""

    main_point: str = Field(min_length=1)  # 一句话主旨：作者最想传达什么
    key_points: list[str] = Field(default_factory=list)  # 核心要点（短帖不硬凑）
    evidence: list[EvidencePoint] = Field(default_factory=list)  # 关键依据或例子（可为空）
    conditions: list[str] = Field(default_factory=list)  # 作者明说的条件与限制
    coverage_gaps: list[str] = Field(default_factory=list)  # 材料缺失/覆盖缺口（媒体未读等）
