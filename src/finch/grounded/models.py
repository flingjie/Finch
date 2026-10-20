"""Grounded 领域模型：接地气练习会话（观察他人处境 → 理解 → 单次追问 → 动作 → 反馈 → 修正）。

独立于表达训练 ``PracticeSession`` 与学习闭环脊柱 ``PracticeAttempt``；CLI 用 ``finch
grounded``。本文件只放模型，不 import repository（避免与 ``storage/repositories.py``
循环导入）。
"""

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ActionType = Literal["observe", "ask", "help", "pause"]
EvidenceType = Literal["source_quote", "user_reported", "ai_inferred"]
ConstraintEvidence = Literal["known", "inferred", "unknown"]
StatusLiteral = Literal["draft", "ready", "waiting_feedback", "reviewed", "paused"]


class Constraint(BaseModel):
    """一条现实约束：描述、依据、证据状态。无证据时 evidence=unknown、basis 写「尚不清楚」。"""

    description: str
    basis: str = ""
    evidence: ConstraintEvidence = "unknown"


class Feedback(BaseModel):
    """一条真实反馈：正文、来源、证据类型、记录时间。证据类型决定可信度边界。"""

    id: str
    text: str
    source_refs: list[str] = Field(default_factory=list)
    evidence_type: EvidenceType = "user_reported"
    recorded_at: datetime


class Revision(BaseModel):
    """前后判断对比：原判断保留（不覆盖），新判断/暂不改变 + 修正依据。"""

    original_understanding: str
    revised_understanding: str
    basis: str = ""
    kept: bool = False  # True = 暂不改变（保留原理解）
    recorded_at: datetime


class GroundedSession(BaseModel):
    """一次接地气练习会话。

    ``initial_understanding`` / ``revised_understanding`` 只能是用户原话或显式「暂不改变」；
    ``hypothesis`` / ``question`` / ``action_draft`` 由 grounded-practice 技能（LLM）提出、
    经 CLI 参数传入，领域服务原样落库、不调 LLM。``action_draft`` 是待审方案，不等于已执行。
    """

    id: str
    schema_version: int = 1
    source_refs: list[str] = Field(default_factory=list)
    person_ref: str | None = None
    situation: str = ""
    constraints: list[Constraint] = Field(default_factory=list)
    initial_understanding: str = ""
    understanding_recorded_at: datetime | None = None
    hypothesis: str = ""
    question: str = ""
    action_type: ActionType | None = None
    action_draft: str = ""
    action_recorded: bool = False
    action_recorded_at: datetime | None = None
    feedback: list[Feedback] = Field(default_factory=list)
    revision: Revision | None = None
    status: StatusLiteral = "draft"
    paused_from: StatusLiteral | None = None
    coverage: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


def grounded_session_id_for(*, content_hash: str, request_id: str | None = None) -> str:
    """会话 id：request_id 优先，否则 content_hash（内容寻址，重提交同素材复用会话）。"""
    key = f"req:grounded:{request_id}" if request_id else f"src:{content_hash}"
    return f"gs_{hashlib.sha256(key.encode('utf-8')).hexdigest()[:12]}"


def feedback_id_for(*, text: str, evidence_type: EvidenceType) -> str:
    """反馈 id：内容寻址，含证据类型（同一句话作为原文 vs AI 推测是不同事实，不合并）。"""
    raw = "\n".join([text.strip(), evidence_type])
    return f"fb_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:8]}"
