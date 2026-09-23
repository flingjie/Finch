"""讨论摘要记忆领域模型（薄持久化，区分用户立场 / AI 推测 / 未解决问题）。

一个 ``DialogueNote`` 对应一个讨论主题；``checkpoints`` 追加不覆盖，当前立场由最后一条
checkpoints 表达。``position_status=user_confirmed`` 只描述「讨论中的认同程度」，不等于
``ContentJobStatus.CONFIRMED``，也不等于观点被事实证实；写草稿继续走 idea 链路门禁。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class PositionStatus(StrEnum):
    """讨论中的立场认同程度（非内容发布语义）。"""

    TENTATIVE = "tentative"
    USER_CONFIRMED = "user_confirmed"
    UNRESOLVED = "unresolved"


class OriginRef(BaseModel):
    """讨论主题的来源引用（任务 / 社区卡 / Opportunity / 已有 idea）。"""

    type: str
    ref: str


class SuggestedValidation(BaseModel):
    """建议的最小验证行动（仅建议，不自动执行）。"""

    action: str
    signal: str
    falsifies: str


class DialogueCheckpoint(BaseModel):
    """一次有意义的收束摘要。``checkpoint_id`` 是调用方重试的幂等键。"""

    checkpoint_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_position: str = ""
    position_status: PositionStatus = PositionStatus.UNRESOLVED
    confirmation_quote: str = ""
    conditions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    assistant_hypotheses: list[str] = Field(default_factory=list)
    suggested_validation: SuggestedValidation | None = None
    change_reason: str = ""
    supersedes_checkpoint_id: str | None = None


class DialogueNote(BaseModel):
    """一个讨论主题的记录。``id`` 由宿主首次保存时给出（opaque），``topic_key`` 是
    稳定的检索键（只召回，不自动合并相似观点）。"""

    id: str
    schema_version: int = 1
    revision: int = 1
    topic: str
    topic_key: str
    origin_refs: list[OriginRef] = Field(default_factory=list)
    checkpoints: list[DialogueCheckpoint] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
