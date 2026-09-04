"""Engagement 领域模型（执行计划 4 数据与类型设计）。"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

EvidenceOrigin = Literal["personal", "external", "conversation"]
Platform = Literal["x", "reddit"]


class ExternalPost(BaseModel):
    """搜索到的外部帖子（外部信号，未经验证不可直接升级为个人证据）。"""

    id: str
    platform: Platform
    url: str
    author_id: str
    author_name: str
    content: str
    published_at: datetime
    metrics: dict[str, int | float] = Field(default_factory=dict)
    matched_topics: list[str] = Field(default_factory=list)


class ConversationScore(BaseModel):
    """互动候选五维评分；total 由后续评分阶段确定性计算，模型不得直接决定。"""

    relevance: float = Field(ge=0, le=1)
    novelty: float = Field(ge=0, le=1)
    discussability: float = Field(ge=0, le=1)
    practical_evidence: float = Field(ge=0, le=1)
    relationship_value: float = Field(ge=0, le=1)
    total: float = Field(ge=0, le=1)
    reasons: list[str]


class InteractionAction(StrEnum):
    """互动建议动作（执行计划 4）。"""

    IGNORE = "ignore"
    BOOKMARK = "bookmark"
    OBSERVE_AUTHOR = "observe_author"
    DRAFT_REPLY = "draft_reply"
    DRAFT_QUOTE = "draft_quote"
    DRAFT_DM = "draft_dm"


class InteractionStatus(StrEnum):
    """互动候选审批/执行状态（执行计划 5 审批与执行保护）。"""

    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    EXPIRED = "expired"


class InteractionCandidate(BaseModel):
    """互动候选：帖子 + 评分 + 建议动作 + 草稿 + 审批状态。"""

    post: ExternalPost
    score: ConversationScore
    action: InteractionAction
    draft: str | None = None
    approval_required: bool
    status: InteractionStatus = InteractionStatus.PROPOSED
