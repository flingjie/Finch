"""article_analysis 数据模型。

``ArticleReport`` 的确定性字段（``id``/``source_type``/``source_ref``/``content_hash``）
由 service 覆盖；模型只产判断字段（含嵌套 ``style``）。禁止总分字段。
"""

from typing import Literal

from pydantic import BaseModel, Field


class ExpressionTask(BaseModel):
    """文章要完成的表达任务（主题 vs 目的）。"""

    topic: str
    primary_task: str
    secondary_tasks: list[str] = Field(default_factory=list)
    inferred: bool = False  # True → 呈现「根据文章推断」


class AudienceChange(BaseModel):
    """写给谁，以及阅读前后应发生的变化。"""

    who: str
    before: str
    after: str
    fit_check: str


class TechniqueBreakdown(BaseModel):
    """原文片段 → 方法 → 对读者的作用 → 代价。"""

    excerpt: str
    method: str
    reader_effect: str
    caveat: str = ""


class Effectiveness(BaseModel):
    """按表达任务的定性成功标准（无分数）。"""

    clarity: str
    concreteness: str
    credibility: str
    actionability: str  # 可写「不适用：…」


class TransferableMethod(BaseModel):
    """可迁移方法 + 小练习。"""

    method: str
    why_effective_here: str
    when_to_use: str
    mini_exercise: str


class ClarityCostReduction(BaseModel):
    """写法如何降低理解成本（ASD-STE100-inspired lens）。"""

    excerpt: str
    method: str
    reader_effect: str
    mini_exercise: str
    rule_id: str | None = None  # optional CL*


class StyleEvidence(BaseModel):
    """可观察风格证据：维度 + 观察 + 短摘录 + 置信度。"""

    dimension: str
    observation: str
    excerpts: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"


class StyleBlock(BaseModel):
    """写作风格七维（原 StyleReport 判断字段，无 rhetorical_patterns）。"""

    scope: Literal["single_text", "multi_sample_author"] = "single_text"
    overall_confidence: Literal["low", "medium", "high"] = "medium"
    opening: list[StyleEvidence] = Field(default_factory=list)
    structure: list[StyleEvidence] = Field(default_factory=list)
    rhythm: list[StyleEvidence] = Field(default_factory=list)
    word_choice: list[StyleEvidence] = Field(default_factory=list)
    stance: list[StyleEvidence] = Field(default_factory=list)
    concreteness: list[StyleEvidence] = Field(default_factory=list)
    reader_relationship: list[StyleEvidence] = Field(default_factory=list)
    signature_patterns: list[str] = Field(default_factory=list)
    transferable_techniques: list[str] = Field(default_factory=list)
    potential_weaknesses: list[str] = Field(default_factory=list)
    experiments_for_me: list[str] = Field(default_factory=list)


class ArticleReport(BaseModel):
    """文章表达分析报告（即算即打印，不落库）。"""

    id: str = ""
    source_type: Literal["text", "file", "url"] = "text"
    source_ref: str | None = None
    content_hash: str = ""

    expression_task: ExpressionTask
    audience_change: AudienceChange
    techniques: list[TechniqueBreakdown] = Field(default_factory=list)
    effectiveness: Effectiveness
    transferable_methods: list[TransferableMethod] = Field(min_length=2, max_length=3)
    clarity_cost_reductions: list[ClarityCostReduction] = Field(
        default_factory=list, max_length=3
    )
    limitations: list[str] = Field(default_factory=list)
    style: StyleBlock = Field(default_factory=StyleBlock)
