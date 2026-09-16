"""同行关系领域模型：PeerProfile 记录「这个人是谁、为什么值得继续交流」，而非销售线索。"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Platform = Literal["x", "reddit", "github", "v2ex", "weixin", "xiaohongshu"]


class EvidenceStatus(StrEnum):
    """实践证据状态：有来源 / 作者自述 / 待了解（仅 bio/转发）。"""

    SOURCED = "sourced"
    AUTHOR_STATED = "author_stated"
    PENDING_REVIEW = "pending_review"


class RelationshipStage(StrEnum):
    """关系阶段：仅用于恢复上下文，不作为强制推进漏斗。

    跨平台计划阶段：discovered → engaged → recurring → practicing → collaborating
    （+ dormant）。保留 ``relevant`` / ``conversing`` 以便旧 YAML 可读；
    ``conversing`` 加载时归一为 ``recurring``。
    """

    DISCOVERED = "discovered"
    RELEVANT = "relevant"  # legacy; treat as score signal, not a funnel stage
    ENGAGED = "engaged"
    CONVERSING = "conversing"  # legacy alias for recurring
    RECURRING = "recurring"
    PRACTICING = "practicing"
    COLLABORATING = "collaborating"
    DORMANT = "dormant"

    @classmethod
    def normalize(cls, value: "RelationshipStage | str") -> "RelationshipStage":
        """将 legacy 值映射到当前阶段词汇。"""
        stage = cls(value) if not isinstance(value, cls) else value
        if stage == cls.CONVERSING:
            return cls.RECURRING
        return stage


class PlatformIdentity(BaseModel):
    """同行在某个平台上的身份（外部作者按 ``platform + author_id`` 幂等归一化）。"""

    platform: Platform
    author_id: str
    username: str = ""
    url: str | None = None


class PeerProfile(BaseModel):
    """同行档案：记录这个人是谁、共同主题、为什么值得继续交流，以及下一步上下文。"""

    id: str
    platform_identities: list[PlatformIdentity]
    display_name: str = ""
    expertise_topics: list[str] = Field(default_factory=list)
    current_interests: list[str] = Field(default_factory=list)
    shared_topics: list[str] = Field(default_factory=list)
    why_relevant: str = ""
    relationship_stage: RelationshipStage = RelationshipStage.DISCOVERED
    last_meaningful_interaction_at: datetime | None = None
    next_context: str = ""
    possible_next_actions: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    current_work: str = ""
    practice_evidence_refs: list[str] = Field(default_factory=list)
    evidence_status: EvidenceStatus | None = None
    person_id: str | None = None

    @field_validator("relationship_stage", mode="before")
    @classmethod
    def _normalize_stage(cls, v: object) -> object:
        if v == "conversing" or v == RelationshipStage.CONVERSING:
            return RelationshipStage.RECURRING
        return v
