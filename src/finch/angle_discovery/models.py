"""article-angle-discovery 数据模型。

``AngleBrief`` 的确定性字段（``id``/``source_type``/``source_ref``/``content_hash``/``coverage``）
由 service 覆盖；模型只产判断字段（原文摘要 + 选题卡 + 推荐方向）。禁止总分/数值评分字段。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IncrementBasis = Literal["source_claim", "verified_fact", "inference", "hypothetical_example"]


class SourceSummary(BaseModel):
    """原文摘要：只概括原文本身，为选题提供出发点，不点评写法。"""

    model_config = ConfigDict(extra="forbid")

    main_point: str = Field(min_length=1)  # 一句话主旨
    key_claims: list[str] = Field(default_factory=list)  # 关键主张
    author_advice: list[str] = Field(default_factory=list)  # 作者给出的建议/做法
    scope: list[str] = Field(default_factory=list)  # 作者明说的条件与限制
    gaps: list[str] = Field(default_factory=list)  # 原文未回答的问题/主要缺口（选题由缺口触发）


class AngleCard(BaseModel):
    """一张选题卡：相对原文有增量的独立成文方向。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)  # 选题（一句话问题或主张）
    main_angles: list[str] = Field(default_factory=list)  # 主要角度（引用角度库名称）
    target_reader: str = ""  # 目标读者
    thesis: str = Field(min_length=1)  # 中心主张
    incremental_value: str = Field(min_length=1)  # 相对原文的增量（补上什么）
    increment_basis: IncrementBasis = "inference"  # 增量主张的证据性质
    opening_scene: str = ""  # 开篇场景（具体、可代入）
    evidence_gaps: list[str] = Field(default_factory=list)  # 需要补充的证据
    reader_action: str = ""  # 读者能采取的行动
    writing_status: str = ""  # 写作状态（可先写机制 / 需先验证）


class AngleBrief(BaseModel):
    """一篇文章的选角报告（即算即打印，默认落库）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    source_type: Literal["text", "file", "url"] = "text"
    source_ref: str | None = None
    content_hash: str = ""
    coverage: list[str] = Field(default_factory=list)  # 确定性覆盖缺口（媒体未读等，代码填）

    source_summary: SourceSummary
    angles: list[AngleCard] = Field(min_length=1)  # shortlist（默认 3 个确实不同方向）
    recommended_index: int = 0  # 指向 angles（0-based）
    recommendation_reason: str = ""  # 为什么推荐这个方向
    outline: list[str] = Field(default_factory=list)  # 推荐方向的提纲
    evidence_gaps: list[str] = Field(default_factory=list)  # 推荐方向需补充的证据
    smallest_validation_action: str = ""  # 最小验证行动
