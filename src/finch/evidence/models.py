"""Evidence 数据模型（spec 7.1/7.2/7.4）。"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ClaimConfidence(StrEnum):
    """证据置信度与对外表达语义（spec 7.4 / 计划 Task 1.2）。

    对外表达规则：
    - VERIFIED       可作为事实断言，必须有直接来源
    - SUPPORTED      可作为有范围的事实陈述
    - USER_CONFIRMED 仅人工确认后产生（模型不得自行产出）
    - INFERRED       必须使用"这表明/在这次实现中/可能"等边界语言
    - UNKNOWN        不得作为可发布主张
    """

    VERIFIED = "VERIFIED"
    SUPPORTED = "SUPPORTED"
    INFERRED = "INFERRED"
    USER_CONFIRMED = "USER_CONFIRMED"
    UNKNOWN = "UNKNOWN"

    @property
    def assertable(self) -> bool:
        return self in {
            ClaimConfidence.VERIFIED,
            ClaimConfidence.SUPPORTED,
            ClaimConfidence.USER_CONFIRMED
        }


def sanitize_model_confidence(confidence: ClaimConfidence) -> ClaimConfidence:
    """强制模型输出不得自行产出 USER_CONFIRMED（计划 Task 1.2 硬性要求）。

    USER_CONFIRMED 仅能由人工确认产生；模型输出在反序列化后经此函数降级为 SUPPORTED
    （仍可发布，但不再声明人工确认）。其余置信度原样返回（INFERRED/UNKNOWN 由下游
    ``assertable`` 门禁拦截）。
    """
    if confidence is ClaimConfidence.USER_CONFIRMED:
        return ClaimConfidence.SUPPORTED
    return confidence


class Claim(BaseModel):
    statement: str
    confidence: ClaimConfidence


class Source(BaseModel):
    type: Literal["commit", "test", "pr", "issue"]
    url: str
    path: str | None = None


class EngineeringEvent(BaseModel):
    id: str
    repository: str
    commits: list[str] = Field(default_factory=list)
    problem: Claim
    decision: Claim
    result: Claim
    missing_context: list[str] = Field(default_factory=list)


class EvidenceCard(BaseModel):
    id: str
    event_id: str
    claim: str
    sources: list[Source] = Field(default_factory=list)
    confidence: ClaimConfidence
    publishable: bool = False
    topics: list[str] = Field(default_factory=list)


class JudgeScores(BaseModel):
    relevance: float = Field(ge=0.0, le=1.0)
    evidence_strength: float = Field(ge=0.0, le=1.0)
    incremental_value: float = Field(ge=0.0, le=1.0)
    discussability: float = Field(ge=0.0, le=1.0)


class RankedCandidate(BaseModel):
    candidate_id: str
    card_ids: list[str]
    recall_score: float = Field(ge=0.0, le=1.0)


class MatchResult(BaseModel):
    candidate_id: str
    card_ids: list[str]
    scores: JudgeScores
    timing: float = Field(ge=0.0, le=1.0)
    relationship_value: float = Field(ge=0.0, le=1.0)
    score: float = Field(ge=0.0, le=1.0)
