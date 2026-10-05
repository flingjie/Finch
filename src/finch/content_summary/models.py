"""content-summary 数据模型。

``ContentSummary`` 的确定性字段（``id``/``source_type``/``source_ref``/``content_hash``）
由 service 覆盖；模型只产判断字段。禁止总分/数值评分字段。
"""

from typing import Literal

from pydantic import BaseModel, Field


class EvidencePoint(BaseModel):
    """支撑观点的关键依据或例子，标注来源以区分作者观点 / 引用 / 回复。"""

    source: str  # 作者 / 引用 / 回复 / 未标明
    content: str


class ContentSummary(BaseModel):
    """一篇帖子的内容摘要（即算即打印，默认落库）。"""

    id: str = ""
    source_type: Literal["text", "file", "url"] = "text"
    source_ref: str | None = None
    content_hash: str = ""

    main_point: str  # 一句话主旨：作者最想传达什么
    key_points: list[str] = Field(default_factory=list)  # 核心要点（短帖不硬凑）
    evidence: list[EvidencePoint] = Field(default_factory=list)  # 关键依据或例子
    conditions: list[str] = Field(default_factory=list)  # 条件与限制（含缺上下文说明）
