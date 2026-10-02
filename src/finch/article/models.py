"""article_analysis 数据模型。

``ArticleReport`` 的确定性字段（``id``/``source_type``/``source_ref``/``content_hash``）
由 service 覆盖；模型只产判断字段。禁止总分字段。
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
    limitations: list[str] = Field(default_factory=list)
