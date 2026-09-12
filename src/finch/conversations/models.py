"""对话线索领域模型：ConversationThread 串联同一同行、同一主题的多次互动。"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ThreadStatus(StrEnum):
    """对话线索状态。"""

    ACTIVE = "active"
    DORMANT = "dormant"
    CLOSED = "closed"
    DEFERRED = "deferred"


class ThreadNote(BaseModel):
    """带来源的线程笔记（共识 / 分歧 / 未解问题 / 实验）。"""

    text: str
    source_ref: str | None = None


class CommitmentStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    DROPPED = "dropped"


class Commitment(BaseModel):
    """用户明确表达或认可的承诺（非自动推断）。"""

    id: str
    owner: Literal["self", "peer"] = "self"
    source_ref: str
    status: CommitmentStatus = CommitmentStatus.OPEN
    due_at: datetime | None = None
    result_ref: str | None = None
    text: str = ""


class FollowUpTrigger(StrEnum):
    NEW_REPLY = "new_reply"
    OWN_COMMITMENT = "own_commitment"
    NEW_EVIDENCE = "new_evidence"
    RELATED_UPDATE = "related_update"


class MiniExperiment(BaseModel):
    """嵌入线程/观点的小实验（按需建议，不自动执行）。"""

    hypothesis: str
    method: str = ""
    expected_observation: str = ""
    actual_result: str = ""
    artifact_ref: str | None = None


class ConversationThread(BaseModel):
    """同一同行、同一主题的多次互动串联，积累关系上下文。"""

    id: str
    peer_id: str
    topic: str
    interaction_ids: list[str] = Field(default_factory=list)
    # Legacy free-text lists kept for read-compat; prefer *_notes when present.
    open_questions: list[str] = Field(default_factory=list)
    agreements: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    possible_experiments: list[str] = Field(default_factory=list)
    open_question_notes: list[ThreadNote] = Field(default_factory=list)
    agreement_notes: list[ThreadNote] = Field(default_factory=list)
    disagreement_notes: list[ThreadNote] = Field(default_factory=list)
    experiment_notes: list[ThreadNote] = Field(default_factory=list)
    commitments: list[Commitment] = Field(default_factory=list)
    experiments: list[MiniExperiment] = Field(default_factory=list)
    root_message_id: str | None = None
    pending_triggers: list[FollowUpTrigger] = Field(default_factory=list)
    defer_until: datetime | None = None
    last_activity_at: datetime | None = None
    status: ThreadStatus = ThreadStatus.ACTIVE
