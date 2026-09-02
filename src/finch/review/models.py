"""Review 数据模型（人工审核 CLI：approve/revise/skip + feedback）。"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ReviewAction(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"
    SKIP = "skip"


class SkipReason(StrEnum):
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    NOT_RELEVANT = "not_relevant"
    LOW_QUALITY = "low_quality"
    NOT_NOW = "not_now"
    OTHER = "other"


class ReviewDecision(BaseModel):
    id: str                                  # "rev_<draft_id>"（幂等键）
    draft_id: str
    action: ReviewAction
    reason: str | None = None                # skip 理由（SkipReason.value）
    revised_body: str | None = None          # revise 后的正文
    diff: str | None = None                  # 修改前后 unified diff
    decided_at: datetime


class Feedback(BaseModel):
    draft_id: str
    published_url: str | None = None
    interaction_metrics: dict = Field(default_factory=dict)
    recorded_at: datetime
