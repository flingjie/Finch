"""Content 数据模型（Writer/Critic/Daily Brief）。"""

from enum import StrEnum

from pydantic import BaseModel, Field

from finch.evidence.models import ClaimConfidence


class DraftKind(StrEnum):
    REPLY = "reply"
    ORIGINAL = "original"


class ClaimRef(BaseModel):
    statement: str
    evidence_card_id: str
    confidence: ClaimConfidence


class Draft(BaseModel):
    id: str
    kind: DraftKind
    candidate_id: str | None = None   # reply 有；original 为 None
    language: str = "en"              # reply="en"；original="zh"
    body: str
    claims: list[ClaimRef] = Field(default_factory=list)
    content_job_id: str | None = None
    position_statement: str = ""
    critic_report_id: str | None = None


class DraftWarning(BaseModel):
    """绑定到某条草稿的未解决 Critic 警告（计划 Task 3.4）。

    ``draft_id`` 把警告归属到具体草稿：Daily Brief 只展示当前草稿的未解决警告，
    绝不把全局警告列表重复打印在每个候选上。``checker`` 为产生警告的检查器名
    （如 evidence/decision/safety），无特定检查器时可为空字符串。
    """

    draft_id: str
    checker: str
    message: str


class DailyBrief(BaseModel):
    run_id: str
    has_drafts: bool
    reply_count: int = 0
    original_count: int = 0
    body: str
