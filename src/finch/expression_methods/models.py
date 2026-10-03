"""表达方法库模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MethodVerdict = Literal["worth_reuse", "practice_again", "not_for_me"]


class MethodSource(BaseModel):
    report_id: str
    method_index: int  # 1-based into ArticleReport.transferable_methods
    excerpt: str = ""
    source_ref: str | None = None


class MethodPracticeLog(BaseModel):
    session_id: str
    verdict: MethodVerdict
    note: str = ""
    at: datetime


class ExpressionMethod(BaseModel):
    id: str
    title: str
    why_effective: str
    when_to_use: str
    boundaries: str = ""
    mini_exercise: str = ""
    sources: list[MethodSource] = Field(default_factory=list)
    practice_logs: list[MethodPracticeLog] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class MergeCandidate(BaseModel):
    method_id: str
    reason: str


class MergeSuggestion(BaseModel):
    candidates: list[MergeCandidate] = Field(default_factory=list, max_length=3)
