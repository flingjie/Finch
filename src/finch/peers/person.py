"""跨平台 Person / Identity 关联（PeerProfile 仍是平台身份原子）。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from finch.peers.models import Platform


class IdentityConfidence(StrEnum):
    CONFIRMED = "confirmed"
    PROBABLE = "probable"
    PENDING = "pending"


class LinkedIdentity(BaseModel):
    """Person 上的一条平台身份引用。"""

    platform: Platform
    external_id: str
    handle: str = ""
    peer_id: str = ""
    confidence: IdentityConfidence = IdentityConfidence.PENDING
    url: str | None = None


class IdentityLinkAudit(BaseModel):
    """可逆合并/拆分审计。"""

    event_id: str
    action: Literal["link", "unlink", "confirm", "reject"]
    person_id: str
    peer_id: str
    platform: str
    external_id: str
    confidence: IdentityConfidence
    reason: str = ""
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Person(BaseModel):
    """跨平台自然人：链接多个 PeerProfile / 平台身份。"""

    person_id: str
    display_name: str = ""
    bio: str = ""
    identities: list[LinkedIdentity] = Field(default_factory=list)
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_evidence_at: datetime | None = None


def person_id_for(*keys: str) -> str:
    """由稳定键派生 person_id。"""
    payload = "|".join(k.strip().lower() for k in keys if k.strip())
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return f"person_{digest[:12]}"


class CreatorEvidenceKind(StrEnum):
    CREATION = "creation"
    FIRST_HAND_EXPERIENCE = "first_hand_experience"
    KNOWLEDGE_SHARING = "knowledge_sharing"
    CROSS_DOMAIN_BRIDGE = "cross_domain_bridge"
    CONVERSATION_BEHAVIOR = "conversation_behavior"
    MARKETING_OR_REPOST = "marketing_or_repost"


class CreatorEvidence(BaseModel):
    """创作者证据卡（≠ 用户 GitHub EvidenceCard 表达管线）。"""

    evidence_id: str
    person_id: str
    peer_id: str = ""
    artifact_id: str
    kind: CreatorEvidenceKind
    claim: str
    support: list[str] = Field(default_factory=list)
    first_hand: bool = False
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    counter_evidence: list[str] = Field(default_factory=list)
