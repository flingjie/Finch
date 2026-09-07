"""Gate 交互层数据模型：结构化人工输入请求。"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


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
