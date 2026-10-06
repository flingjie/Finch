"""表达方法库模型。"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MethodVerdict = Literal["worth_reuse", "practice_again", "not_for_me"]
ReplyMethodVerdict = Literal["useful", "mixed", "not_fit"]


class MethodSource(BaseModel):
    report_id: str
    method_index: int  # 1-based into ArticleReport.transferable_methods
    excerpt: str = ""
    source_ref: str | None = None
    why_effective_here: str = ""
    content_hash: str = ""


class MethodPracticeLog(BaseModel):
    session_id: str
    verdict: MethodVerdict | ReplyMethodVerdict
    note: str = ""
    at: datetime
    # form 区分练习（practice）与回复（reply）两种复用场景；draft_ref 为回复草稿引用。
    form: Literal["practice", "reply"] = "practice"
    draft_ref: str | None = None
    # 连接结果记录（增量）：方法在什么条件下起作用，及一次回复引发的具体交流。
    conditions: str = ""  # 这次在什么条件下起作用/没起作用
    question_asked: str = ""  # 具体问了什么
    response: str = ""  # 对方回应
    follow_up_action: str = ""  # 后续行动


class ExpressionMethod(BaseModel):
    id: str
    title: str
    why_effective: str
    when_to_use: str
    boundaries: str = ""
    mini_exercise: str = ""
    purpose_tags: list[str] = Field(default_factory=list)
    required_material: str = ""
    # 回复复用字段：applicable_forms 空 = 仅 article（旧卡向后兼容，不自动视为适合回复）。
    applicable_forms: list[str] = Field(default_factory=list)
    reply_usage: str = ""
    reply_boundaries: str = ""
    sources: list[MethodSource] = Field(default_factory=list)
    practice_logs: list[MethodPracticeLog] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    def content_fingerprint(self) -> str:
        """Deterministic hash of method body fields (for exploration cache)."""
        tags = ",".join(sorted(self.purpose_tags))
        forms = ",".join(sorted(self.applicable_forms))
        payload = "\n".join(
            [
                self.title,
                self.when_to_use,
                self.boundaries,
                self.mini_exercise,
                self.required_material,
                self.reply_usage,
                self.reply_boundaries,
                forms,
                tags,
            ]
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class MergeCandidate(BaseModel):
    method_id: str
    reason: str


class MergeSuggestion(BaseModel):
    candidates: list[MergeCandidate] = Field(default_factory=list, max_length=3)
