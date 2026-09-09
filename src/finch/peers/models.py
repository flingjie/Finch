"""同行关系领域模型：PeerProfile 记录「这个人是谁、为什么值得继续交流」，而非销售线索。"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

Platform = Literal["x", "reddit"]


class RelationshipStage(StrEnum):
    """关系阶段：仅用于恢复上下文，不作为强制推进漏斗。"""

    DISCOVERED = "discovered"
    RELEVANT = "relevant"
    ENGAGED = "engaged"
    CONVERSING = "conversing"
    COLLABORATING = "collaborating"
    DORMANT = "dormant"


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
