"""Evidence 数据模型（spec 7.1/7.2/7.4）。"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ClaimConfidence(StrEnum):
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
