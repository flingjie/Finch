"""Content 数据模型（Writer/Critic/Daily Brief）。"""

from enum import StrEnum

from pydantic import BaseModel, Field

from finch.evidence.models import ClaimConfidence


class DraftKind(StrEnum):
    REPLY = "reply"
    ORIGINAL = "original"


class ClaimRef(BaseModel):
    statement: str
    evidence_card_id: str
    confidence: ClaimConfidence


class Draft(BaseModel):
    id: str
    kind: DraftKind
    candidate_id: str | None = None   # reply 有；original 为 None
    language: str = "en"              # reply="en"；original="zh"
    body: str
    claims: list[ClaimRef] = Field(default_factory=list)
    content_job_id: str | None = None
    position_statement: str = ""
    critic_report_id: str | None = None


class DailyBrief(BaseModel):
    run_id: str
    has_drafts: bool
    reply_count: int = 0
    original_count: int = 0
    body: str
