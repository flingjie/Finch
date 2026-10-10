"""expression-practice 领域模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

MethodVerdictLiteral = Literal["worth_reuse", "practice_again", "not_for_me"]

ActionLiteral = Literal["revise", "predict", "hint", "transfer", "finish"]
ResponseKindLiteral = Literal["revision", "prediction", "transfer", "skipped"]
ExerciseLiteral = Literal["auto", "revise", "predict", "hint", "transfer"]

# 写作伙伴流程的会话阶段与最终版来源。
PhaseLiteral = Literal["explore", "drafting", "feedback", "revising", "done"]
FinalSourceLiteral = Literal["user_authored", "ai_example", "mixed"]

# 草稿优先（draft-first）会话模式与用户动作。
ModeLiteral = Literal["example", "guided", "independent"]
UserActionLiteral = Literal["adopt", "comment", "edit", "skip"]

# 风格观察确认状态。
ObservationStatus = Literal["pending", "accepted", "corrected", "rejected"]


class PracticeContext(BaseModel):
    """训练目标语境：目标读者与训练目的。"""

    audience: str = ""
    goal: str = ""


class PracticeOption(BaseModel):
    """一种展开写法：名称、熟悉程度、切入点、推进路径、效果、代价、所需事实、练习维度。"""

    name: str
    familiarity: str  # 熟悉 / 相邻 / 陌生 / 暂定
    entry_point: str
    progression: list[str] = Field(default_factory=list)  # 2-3 步推进路径
    effect: str = ""  # 希望带来的阅读效果
    cost: str = ""  # 代价
    facts_needed: str = ""  # 需要用户提供的事实或细节
    dimension: str = ""  # 本次主要练习维度（结构/节奏/手法/语气）
    method_id: str | None = None  # 该方案由哪个表达方法启发（可空）


class PracticeOptionsOutput(BaseModel):
    """explore 的 LLM 输出：三种展开方案（必须恰好三种且实质不同）。"""

    options: list[PracticeOption] = Field(min_length=3, max_length=3)


class LocalFeedback(BaseModel):
    """局部对比反馈：保留、关键位置、两种写法、差异、重写任务、用户重写。"""

    keep: str = ""  # 值得保留：引用一句用户原话 + 有效之处
    key_location: str = ""  # 关键位置：一句/短片段 + 当前效果
    alternative_a: str = ""  # 写法 A 短示范（不扩写整段）
    alternative_b: str = ""  # 写法 B 短示范
    difference: str = ""  # A/B 差异 + 各自代价
    rewrite_task: str = ""  # 重写任务
    user_rewrite: str = ""  # 用户重写结果（保存最终版用）
    method_id: str | None = None  # 该反馈由哪个表达方法启发（可空）


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


class AiDraft(BaseModel):
    """AI 草稿版本：example=全文，guided=开头/框架。"""

    id: str
    text: str
    parent_version_id: str | None = None  # 派生自哪个版本；首个为 None
    method_ids: list[str] = Field(default_factory=list)
    explanation: str = ""  # 一条写法说明
    task: str = ""  # 可选微动作（example）或续写任务（guided）
    created_at: datetime


class UserAction(BaseModel):
    """用户对某个 AI 草稿版本的动作。"""

    action: UserActionLiteral  # adopt / comment / edit / skip
    target_version_id: str
    text: str = ""  # comment 正文 / edit 文本 / adopt 采纳正文
    edit_scope: str = ""  # 编辑范围说明（仅 edit）
    created_at: datetime


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
    # 写作伙伴流程增量字段（旧会话缺省读入，向后兼容）。
    source_material: str = ""  # 原始素材（想法/事件）；已有段落时可为空
    phase: PhaseLiteral | None = None  # explore / drafting / feedback / revising / done
    options: list[PracticeOption] = Field(default_factory=list)  # 三种写法方案
    selected_option: int | None = None  # 下标（0/1/2）
    selection_reason: str = ""
    practice_dimension: str = ""  # 本次主要练习维度
    feedback_rounds: list[LocalFeedback] = Field(default_factory=list)  # 局部对比反馈
    final_source: FinalSourceLiteral = "user_authored"  # 最终版来源
    source_note: str = ""  # 混合文本的来源片段说明
    # 草稿优先（draft-first）增量字段（旧会话缺省读入，向后兼容）。
    mode: ModeLiteral = "independent"  # 模型默认 independent；start 默认 example
    ai_drafts: list[AiDraft] = Field(default_factory=list)  # AI 生成版本
    user_actions: list[UserAction] = Field(default_factory=list)  # adopt/comment/edit/skip
    final_version_id: str | None = None  # 最终版对应 AI 版本 id；None=用户独立创作
    learning_observation: str = ""  # 本次轻量学习观察（偏好，非创作证据）
    created_at: datetime
    updated_at: datetime


class PracticeLesson(BaseModel):
    """LLM 经验总结输出。"""

    lesson: str


class PracticeDraftMeta(BaseModel):
    """draft 的 LLM 元信息输出：一条写法说明 + 一个可选小动作/续写任务。"""

    explanation: str
    task: str = ""


class StyleObservationEvidence(BaseModel):
    """风格观察证据：一次会话 + 用户原句。"""

    session_id: str
    quote: str  # 用户创作片段（可追溯）


class StyleObservation(BaseModel):
    """候选风格观察：多次练习出现相似选择后提出，用户确认后才进入 voice profile。"""

    id: str
    characteristic: str  # 特点
    evidence: list[StyleObservationEvidence] = Field(default_factory=list)
    applicable_context: str = ""  # 适用场景
    counterexamples: str = ""  # 反例 / 不确定性
    possible_from_prompt: str = ""  # 是否可能来自当次练习要求
    status: ObservationStatus = "pending"
    created_at: datetime
    updated_at: datetime
