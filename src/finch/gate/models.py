"""Gate 交互层数据模型：结构化人工输入请求与立场批准记录。"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class InputAction(StrEnum):
    CONFIRM = "confirm"
    EDIT = "edit"
    SKIP = "skip"
    STOP = "stop"
    SHOW_EVIDENCE = "show_evidence"


class ProposedPosition(BaseModel):
    """作者立场提案。立场缺失/不完整时对应字段为空串；只有三者非空才允许 confirm。"""

    claim: str = ""
    decision: str = ""
    tradeoff: str = ""
    change_mind_if: str | None = None

    def complete(self) -> bool:
        return bool(self.claim and self.decision and self.tradeoff)


class InputRequest(BaseModel):
    """position_gate 停在 needs_input 时产出的结构化请求，供 CLI/Skill/未来 Web UI 消费。"""

    type: Literal["author_position_confirmation"] = "author_position_confirmation"
    run_id: str
    job_id: str
    topic: str
    why_now: str = ""
    proposed_position: ProposedPosition
    evidence_card_ids: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    actions: list[InputAction] = Field(default_factory=lambda: list(InputAction))


class PositionApproval(BaseModel):
    """一次作者立场确认记录。按 fingerprint 复用（而非 job id），撤销后禁止复用。"""

    position_fingerprint: str
    source_job_id: str
    approved_at: datetime = Field(default_factory=_utcnow)
    revoked_at: datetime | None = None
