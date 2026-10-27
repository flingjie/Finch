"""article-angle-discovery 数据模型。

``AngleBrief`` 的确定性字段（``id``/``source_type``/``source_ref``/``content_hash``/``coverage``）
由 service 覆盖；模型只产判断字段（原文摘要 + 选题卡 + 推荐方向）。禁止总分/数值评分字段。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IncrementBasis = Literal["source_claim", "verified_fact", "inference", "hypothetical_example"]

CombinationRole = Literal["原文观点", "第二份材料"]


class CombinationMaterial(BaseModel):
    """一份组合材料：原文观点 或 第二份材料，各自带来源。

    第二份材料可来自任何有出处的地方（实践/失败记录、其它文章、已有方法、领域通识、
    读者问题），不限于实践记录；来源用于诚实标注，不冒充亲历。
    """

    model_config = ConfigDict(extra="forbid")

    role: CombinationRole  # 原文观点 / 第二份材料
    content: str = Field(min_length=1)  # 材料内容（观点本身）
    source: str = Field(min_length=1)  # 来源（见 docstring）


class SourceSummary(BaseModel):
    """原文摘要：只概括原文本身，为选题提供出发点，不点评写法。"""

    model_config = ConfigDict(extra="forbid")

    main_point: str = Field(min_length=1)  # 一句话主旨
    key_claims: list[str] = Field(default_factory=list)  # 关键主张
    author_advice: list[str] = Field(default_factory=list)  # 作者给出的建议/做法
    scope: list[str] = Field(default_factory=list)  # 作者明说的条件与限制
    gaps: list[str] = Field(default_factory=list)  # 原文未回答的问题/主要缺口（选题由缺口触发）


class ReaderJourney(BaseModel):
    """一个候选角度期望引起的读者认知变化。

    读者心理是假设，须标注，不得冒充调查事实。
    """

    model_config = ConfigDict(extra="forbid")

    initial_belief: str = ""  # 读者原有认知（假设，标注为推断）
    reader_problem: str = ""  # 读者实际关心的问题
    cognitive_shift: str = ""  # 希望读者重新理解什么
    emotional_path: list[str] = Field(default_factory=list)  # 如 ["好奇", "怀疑", "理解"]
    turning_point: str = ""  # 触发变化的事实或经历
    evidence_refs: list[str] = Field(default_factory=list)  # 转折依据（真实来源，字符串）
    evidence_gaps: list[str] = Field(default_factory=list)  # 转折依据仍缺的证据
    desired_afterthought: str = ""  # 读后留下的问题或行动


class ComparisonDimension(BaseModel):
    """一个比较维度的定性说明（跨角度，非数值、非总分）。"""

    model_config = ConfigDict(extra="forbid")

    dimension: str  # 读者相关性 / 认知新意 / 证据充分度 / 可实践性 / 与作者风格契合度
    comparison: str = ""  # 定性比较文字


class AngleCard(BaseModel):
    """一张选题卡：相对原文有增量的独立成文方向。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)  # 选题（一句话问题或主张）
    main_angles: list[str] = Field(default_factory=list)  # 主要角度（引用角度库名称）
    target_reader: str = ""  # 目标读者
    thesis: str = Field(min_length=1)  # 中心主张
    combination_materials: list[CombinationMaterial] = Field(default_factory=list)  # 组合材料
    connection_rationale: str = ""  # 连接理由：为什么两份材料能共同解释这个问题
    incremental_value: str = Field(min_length=1)  # 相对原文的增量（补上什么）
    increment_basis: IncrementBasis = "inference"  # 增量主张的证据性质
    opening_scene: str = ""  # 开篇场景（具体、可代入）
    evidence_gaps: list[str] = Field(default_factory=list)  # 需要补充的证据
    reader_action: str = ""  # 读者能采取的行动
    writing_status: str = ""  # 写作状态（可先写机制 / 需先验证）
    reader_journey: ReaderJourney | None = None  # 可选；本角度的读者认知变化
    applicable_form: str = ""  # 适用形式（推文 / 长文 / 视频…）
    main_risk: str = ""  # 主要风险


class AngleBrief(BaseModel):
    """一篇文章的选角报告（即算即打印，默认落库）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    source_type: Literal["text", "file", "url"] = "text"
    source_ref: str | None = None
    content_hash: str = ""
    coverage: list[str] = Field(default_factory=list)  # 确定性覆盖缺口（媒体未读等，代码填）

    source_summary: SourceSummary
    angles: list[AngleCard] = Field(default_factory=list)  # shortlist（1–3 个确实不同方向，可为空）
    recommended_index: int | None = None  # 指向 angles（0-based）；空 angles 时为 None
    recommendation_reason: str = ""  # 为什么推荐这个方向
    outline: list[str] = Field(default_factory=list)  # 推荐方向的提纲
    evidence_gaps: list[str] = Field(default_factory=list)  # 推荐方向需补充的证据
    smallest_validation_action: str = ""  # 最小验证行动
    comparison: list[ComparisonDimension] = Field(default_factory=list)  # 5 个维度定性比较
