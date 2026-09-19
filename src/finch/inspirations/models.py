"""Inspiration（轻量灵感笔记）领域模型。

独立于 ConversationThread 与 CollisionCard；只保存用户明确「记下来」的启发。
``origin`` 描述事实来源，不描述证据强弱；``simulation`` 永不升级为真实关系。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class InspirationOrigin(StrEnum):
    OBSERVATION = "observation"
    CONVERSATION = "conversation"
    PRACTICE = "practice"
    SIMULATION = "simulation"
    USER_INPUT = "user_input"


class InspirationNote(BaseModel):
    """追加式笔记（append-only，不用机器重新生成覆盖原话）。"""

    text: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Inspiration(BaseModel):
    """用户明确保存的一条启发。"""

    id: str
    text: str
    origin: InspirationOrigin = InspirationOrigin.USER_INPUT
    source_refs: list[str] = Field(default_factory=list)
    person_ids: list[str] = Field(default_factory=list)
    conversation_id: str | None = None
    collision_id: str | None = None
    notes: list[InspirationNote] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    archived_at: datetime | None = None


def inspiration_id_for(text: str) -> str:
    """内容寻址 id：相同 text → 相同 id（幂等）；相似但不同 text 不合并。"""
    digest = hashlib.sha256(text.strip().encode("utf-8")).hexdigest()
    return f"insp_{digest[:12]}"
