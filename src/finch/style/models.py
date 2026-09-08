"""writing-style-analysis 数据模型。

``StyleReport`` 的确定性字段（``id``/``source_type``/``source_ref``/``content_hash``/
``sample_size``）由代码算，service 覆盖模型输出；模型只产判断字段（维度 / 技巧 / 局限 /
``scope`` / ``overall_confidence``）。
"""

from typing import Literal

from pydantic import BaseModel, Field


class StyleEvidence(BaseModel):
    """单条风格证据：维度 + 观察 + 原文出处 + 置信度。"""

    dimension: str
    observation: str
    excerpts: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"


class StyleReport(BaseModel):
    """写作风格分析报告（即算即打印，不落库）。"""

    # ---- 确定性字段（service 覆盖，LLM 无需产出）----
    id: str = ""
    source_type: Literal["text", "file", "url"] = "text"
    source_ref: str | None = None
    content_hash: str = ""
    sample_size: int = 1

    # ---- 判断字段（LLM 产出）----
    scope: Literal["single_text", "multi_sample_author"] = "single_text"
    overall_confidence: Literal["low", "medium", "high"] = "medium"

    opening: list[StyleEvidence] = Field(default_factory=list)
    structure: list[StyleEvidence] = Field(default_factory=list)
    rhythm: list[StyleEvidence] = Field(default_factory=list)
    word_choice: list[StyleEvidence] = Field(default_factory=list)
    stance: list[StyleEvidence] = Field(default_factory=list)
    concreteness: list[StyleEvidence] = Field(default_factory=list)
    reader_relationship: list[StyleEvidence] = Field(default_factory=list)
    rhetorical_patterns: list[StyleEvidence] = Field(default_factory=list)

    signature_patterns: list[str] = Field(default_factory=list)
    transferable_techniques: list[str] = Field(default_factory=list)
    potential_weaknesses: list[str] = Field(default_factory=list)
    experiments_for_me: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class StyleComparison(BaseModel):
    """--compare-voice 产物：与作者画像的三桶对比。"""

    already_shared: list[str] = Field(default_factory=list)
    worth_experimenting: list[str] = Field(default_factory=list)
    not_a_fit: list[str] = Field(default_factory=list)
