"""expression-practice 领域模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PracticeSession(BaseModel):
    """一次表达练习会话：首稿 + 诊断 + 追问 + 修订 + 最终版 + 经验。"""

    id: str
    idea_id: str | None = None
    opportunity_id: str | None = None
    initial_attempt: str = ""
    diagnosis: str = ""
    questions_asked: list[str] = Field(default_factory=list)
    revisions: list[str] = Field(default_factory=list)
    final_expression: str = ""
    lesson: str = ""
    status: Literal["started", "finished"] = "started"
    created_at: datetime
    updated_at: datetime


class PracticeDiagnosis(BaseModel):
    """LLM 诊断输出：最大问题 + 一个追问。"""

    diagnosis: str
    question: str


class PracticeLesson(BaseModel):
    """LLM 经验总结输出。"""

    lesson: str
