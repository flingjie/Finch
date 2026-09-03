"""Review 数据模型（人工审核 CLI：approve/revise/skip + feedback）。"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ReviewAction(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"
    SKIP = "skip"
    CONFIRM_POSITION = "confirm_position"  # 独立于最终发布批准


class SkipReason(StrEnum):
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    NOT_RELEVANT = "not_relevant"
    LOW_QUALITY = "low_quality"
    NOT_NOW = "not_now"
    OTHER = "other"
    NO_CLEAR_POSITION = "no_clear_position"
    GENERIC_VOICE = "generic_voice"
    JOB_NOT_USEFUL = "job_not_useful"
    FACT_ERROR = "fact_error"


class ReviewDecision(BaseModel):
    id: str                                  # "rev_<draft_id>"（幂等键）
    draft_id: str
    action: ReviewAction
    reason: str | None = None                # skip 理由（SkipReason.value）
    revised_body: str | None = None          # revise 后的正文
    diff: str | None = None                  # 修改前后 unified diff
    position_correct: bool | None = None     # 立场是否正确（confirm_position）
    voice_match: int | None = Field(default=None, ge=0, le=5)  # 语气匹配度 0-5（confirm_position）
    job_clear: bool | None = None            # job 是否清晰（confirm_position）
    decided_at: datetime


class OutcomeAssessment(BaseModel):
    """发布后的结果评估（C5）：任务是否完成 + 可选的阅读/行动/回复/点击计数。

    各计数均为 None 表示「未记录」，而非 0；`job_completed` 为必填枚举。
    """

    job_completed: Literal["yes", "partly", "no", "unknown"]
    reader_understood: bool | None = None
    desired_action_count: int | None = None
    useful_reply_count: int | None = None
    github_clicks: int | None = None
    notes: str | None = None


class Feedback(BaseModel):
    draft_id: str
    published_url: str | None = None
    interaction_metrics: dict = Field(default_factory=dict)
    recorded_at: datetime
    outcome: OutcomeAssessment | None = None
