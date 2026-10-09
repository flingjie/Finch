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
    # 种子/参考方法字段：分类、维度、章节、证据状态、可执行性、非文章来源。
    # method_type 决定在 expression-practice 里如何用：technique（三方案/局部反馈）、
    # training（卡点训练任务）、scenario（仅在相应场景激活）。
    method_type: Literal["technique", "training", "scenario"] = "technique"
    dimension: str = ""
    chapter: str = ""
    evidence_status: str = ""  # 知识依据状态（如 user_supplied_toc_summary），与可执行性分开
    executable: bool = True  # 是否可作为练习任务推荐（「待补充」条目 = False）
    source_note: str = ""  # 非文章来源说明 + 链接；书籍来源不用 MethodSource
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
                self.method_type,
                self.dimension,
                "1" if self.executable else "0",
            ]
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class MergeCandidate(BaseModel):
    method_id: str
    reason: str


class MergeSuggestion(BaseModel):
    candidates: list[MergeCandidate] = Field(default_factory=list, max_length=3)
