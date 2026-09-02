"""Evidence 数据模型（spec 7.1/7.2/7.4）。"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ClaimConfidence(str, Enum):
    VERIFIED = "VERIFIED"
    SUPPORTED = "SUPPORTED"
    INFERRED = "INFERRED"
    USER_CONFIRMED = "USER_CONFIRMED"
    UNKNOWN = "UNKNOWN"

    @property
    def assertable(self) -> bool:
        return self in {ClaimConfidence.VERIFIED, ClaimConfidence.SUPPORTED, ClaimConfidence.USER_CONFIRMED}


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
