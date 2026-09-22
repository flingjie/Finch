"""Community（社区匹配与进入）领域模型。

社区是"关系发生的场"，不是人也不是问题：核心 Builder 进 People/Connection 流程，
社区里的问题进 Idea Discovery。MVP 只处理公开数据，公开证据引用是硬门槛；
不接私有 Discord/Slack 消息，不自动加入/发言。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class CommunityResult(StrEnum):
    """一次推荐之后的跟进状态（简单、可累积，用于度量"第二次交流"）。"""

    IGNORED = "ignored"
    SAVED = "saved"
    JOINED = "joined"
    INTERACTED = "interacted"
    REPEATED = "repeated"
    CONTRIBUTED = "contributed"


class CommunityEvidence(BaseModel):
    """一条近期公开活动证据（硬门槛 3：推荐结论必须能引用公开证据）。"""

    topic: str
    relevance: str
    url: str = ""


class CommunityPerson(BaseModel):
    """社区里值得关注的核心 Builder。"""

    name: str
    reason: str


class EntryPoint(BaseModel):
    """一个可切入的具体讨论 + 建议角度。"""

    discussion: str
    suggested_angle: str


class FirstContribution(BaseModel):
    """用户能贡献的代码 / 案例 / 工具。"""

    type: str
    proposal: str


class CommunityProfile(BaseModel):
    """一张社区行动卡（每周 3 张）。

    ``id`` 由 ``name`` 内容寻址；为空时由 ``CommunityService.save`` 补全。
    """

    id: str = ""
    name: str
    platforms: list[str] = Field(default_factory=list)
    fit_score: int = Field(default=0, ge=0, le=100)
    why_fit: list[str] = Field(default_factory=list)
    recent_evidence: list[CommunityEvidence] = Field(default_factory=list)
    people: list[CommunityPerson] = Field(default_factory=list)
    entry_point: EntryPoint | None = None
    first_contribution: FirstContribution | None = None
    risks: list[str] = Field(default_factory=list)
    evidence_urls: list[str] = Field(default_factory=list)
    week: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CommunityFeedback(BaseModel):
    """一次跟进反馈（append-only，永不覆盖）。"""

    community_id: str
    result: CommunityResult
    note: str = ""
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CommunityContext(BaseModel):
    """一次 discover 的输入上下文快照（可审计：本轮推荐基于什么）。"""

    week: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    interests: list[str] = Field(default_factory=list)
    current_questions: list[str] = Field(default_factory=list)
    practice_refs: list[str] = Field(default_factory=list)
    active_peers: list[str] = Field(default_factory=list)
    recent_ideas: list[str] = Field(default_factory=list)


def community_id_for(name: str) -> str:
    """内容寻址 id：相同 name → 相同 id（幂等）；相似但不同 name 不合并。"""
    digest = hashlib.sha256(name.strip().encode("utf-8")).hexdigest()
    return f"comm_{digest[:12]}"
