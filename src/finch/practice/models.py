"""expression-practice 领域模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

MethodVerdictLiteral = Literal["worth_reuse", "practice_again", "not_for_me"]

ActionLiteral = Literal["revise", "predict", "hint", "transfer", "finish"]
ResponseKindLiteral = Literal["revision", "prediction", "transfer", "skipped"]
ExerciseLiteral = Literal["auto", "revise", "predict", "hint", "transfer"]


class PracticeContext(BaseModel):
    """训练目标语境：目标读者与训练目的。"""

    audience: str = ""
    goal: str = ""


class PracticeFeedback(BaseModel):
    """LLM 诊断输出：一个最大障碍 + 一个下一步动作。"""

    action: ActionLiteral
    diagnosis: str
    evidence_quote: str = ""
    task: str | None = None  # revise/predict/hint/transfer 的动作内容；finish 为 None
    hint_level: Literal[0, 1, 2, 3] = 0

    @model_validator(mode="after")
    def _validate_task_and_hint(self) -> "PracticeFeedback":
        if self.action == "finish":
            if self.task:
                raise ValueError("finish action must not carry a task")
        elif not self.task:
            raise ValueError(f"{self.action} action requires a non-empty task")
        if self.action != "hint" and self.hint_level != 0:
            raise ValueError("hint_level is only allowed for hint action")
        return self


class PracticeTurn(BaseModel):
    """一轮训练：针对某份表达快照的反馈 + 用户响应。"""

    id: str
    expression_snapshot: str
    feedback: PracticeFeedback
    response: str | None = None
    response_kind: ResponseKindLiteral | None = None
    created_at: datetime
    responded_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_response_pairing(self) -> "PracticeTurn":
        if (self.response is None) != (self.response_kind is None):
            raise ValueError("response and response_kind must be set together or both unset")
        if self.response_kind == "skipped" and self.response:
            raise ValueError("skipped response must be empty")
        return self


class PracticeSession(BaseModel):
    """一次表达练习会话：首稿 + 逐轮反馈 + 修订 + 最终版 + 经验。"""

    id: str
    idea_id: str | None = None
    initial_attempt: str = ""
    diagnosis: str = ""
    questions_asked: list[str] = Field(default_factory=list)
    revisions: list[str] = Field(default_factory=list)
    final_expression: str = ""
    lesson: str = ""
    status: Literal["started", "finished"] = "started"
    method_id: str | None = None
    method_verdict: MethodVerdictLiteral | None = None
    method_verdict_note: str = ""
    context: PracticeContext = Field(default_factory=PracticeContext)
    turns: list[PracticeTurn] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class PracticeLesson(BaseModel):
    """LLM 经验总结输出。"""

    lesson: str
