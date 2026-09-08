"""对话线索领域模型：ConversationThread 串联同一同行、同一主题的多次互动。"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ThreadStatus(StrEnum):
    """对话线索状态。"""

    ACTIVE = "active"
    DORMANT = "dormant"
    CLOSED = "closed"


class ConversationThread(BaseModel):
    """同一同行、同一主题的多次互动串联，积累关系上下文。"""

    id: str
    peer_id: str
    topic: str
    interaction_ids: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    agreements: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    possible_experiments: list[str] = Field(default_factory=list)
    last_activity_at: datetime | None = None
    status: ThreadStatus = ThreadStatus.ACTIVE
