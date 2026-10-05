"""机会聚合模型（规范 §10.1/§11.1）：带生命周期状态机的交流机会。

替换旧的 ``engagement.Opportunity``（无状态发现结果）与 ``InteractionProposal``
（审批状态机）：一个聚合承载 ``why_me`` / ``why_continue`` / ``entry_kind`` /
``proposal`` / ``status`` / ``decision``，快照 + append-only 事件日志。
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class EntryKind(StrEnum):
    """交流入口（规范 §3.2）：困难 / 成果 / 分歧 / 共同探索 / 跨领域。"""

    DIFFICULTY = "difficulty"
    RESULT = "result"
    DISAGREEMENT = "disagreement"
    CO_EXPLORATION = "co_exploration"
    CROSS_DOMAIN = "cross_domain"


class EvidenceTier(StrEnum):
    """证据分层（规范 §5.3）：原文明确说出 / 由材料推导 / 未知。"""

    EXPLICIT = "explicit"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class ContributionForm(StrEnum):
    """贡献形式（规范 §6.2）。"""

    REPLY_DRAFT = "reply_draft"
    METHOD_CARD = "method_card"
    DEMO = "demo"
    CASE = "case"
    CLARIFYING_QUESTION = "clarifying_question"


class OpportunityStatus(StrEnum):
    """机会生命周期（规范 §11.1 状态图）。"""

    PROPOSED = "proposed"
    SELECTED = "selected"
    READY = "ready"
    PARKED = "parked"
    CLOSED = "closed"


class EvidenceRef(BaseModel):
    """支持提案的证据引用（含分层标记）。"""

    source_ref: str
    quote: str = ""
    claim: str = ""
    tier: EvidenceTier = EvidenceTier.UNKNOWN


class Proposal(BaseModel):
    """最小贡献：明确产物与边界（规范 §6.1/§6.2）。"""

    contribution: str
    form: ContributionForm
    expected_output: str
    scope: str = ""
    cost_note: str = ""  # 粗略投入范围与未知（自然语言，不伪造精确耗时）


ProblemEvidenceStatus = Literal["author_stated", "inferred"]


class Problem(BaseModel):
    """推荐参与机会的具体问题：原文明确（author_stated）与模型推测（inferred）分开。"""

    statement: str
    source_refs: list[str] = Field(default_factory=list)
    evidence_status: ProblemEvidenceStatus = "inferred"


class Fit(BaseModel):
    """为什么与用户有关：reason + 已确认实践引用 + 活跃问题引用。"""

    reason: str
    practice_refs: list[str] = Field(default_factory=list)
    problem_refs: list[str] = Field(default_factory=list)


NextActionType = Literal["ask", "offer", "try", "observe"]


class NextAction(BaseModel):
    """推荐的下一步动作：类型 + 一句可执行建议。缺少依据时默认 ask（提问优先）。"""

    type: NextActionType = "ask"
    suggestion: str = ""


class Reaction(BaseModel):
    """用户对这条机会亲口说的话（原话，append-only）。

    不是 InteractionRecord（没有发生互动）、不是 ConversationThread 笔记（没有对方）、
    不是 PracticeItem（未经 confirm）；只在本聚合内有效。
    """

    seq: int  # 从 1 起
    text: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Opportunity(BaseModel):
    """交流机会聚合：可接续话题 + 双方参与理由 + 最小贡献 + 生命周期。"""

    id: str
    revision: int = 1
    person_ref: str | None = None
    thread_ref: str | None = None
    topic: str = ""
    entry_kind: EntryKind | None = None
    why_me: str = ""
    why_continue: str = ""
    proposal: Proposal | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    # 推荐参与机会的结构化三字段（面向推荐，与既有字段并存；None 表示未评估）。
    problem: Problem | None = None
    fit: Fit | None = None
    next_action: NextAction | None = None
    status: OpportunityStatus = OpportunityStatus.PROPOSED
    decision: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    # 用户对这条机会的反应（原话；append-only）。无反应 → prepare 只能出澄清问题。
    reactions: list[Reaction] = Field(default_factory=list)
    # 接续提案引用前次机会（规范 §10.4 / §11.1：不复活原任务）。
    previous_opportunity_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OpportunityEvent(BaseModel):
    """追加到 events.jsonl 的不可变事件（含乐观并发锚点 ``expected_revision``）。"""

    event_id: str
    opportunity_id: str
    event_type: str
    expected_revision: int
    decision: str | None = None
    request_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ArtifactKind(StrEnum):
    """成果种类（规范 §10.3）：方法卡 / 演示 / 回复草稿 / 草稿 / 案例。"""

    METHOD_CARD = "method_card"
    DEMO = "demo"
    REPLY_DRAFT = "reply_draft"
    DRAFT = "draft"
    CASE = "case"


class MaterialOrigin(StrEnum):
    """材料来源（规范 §10.3）：真实材料 / 合成材料。"""

    REAL = "real"
    SYNTHETIC = "synthetic"


class ExecutionStatus(StrEnum):
    """执行状态（规范 §6.4/§10.3）：区分未运行与已运行，不得把未运行标为已运行。"""

    N_A = "n/a"
    NOT_RUN = "not_run"
    RAN_OK = "ran_ok"
    RAN_FAILED = "ran_failed"
    UNCLEAR = "unclear"


class Artifact(BaseModel):
    """成果对象（规范 §10.3）：方法卡 / 演示 / 草稿是同一任务的不同成果引用。

    ``material_origin``（真实/合成）与 ``execution_status``（未运行/已运行…）分开描述，
    展示结果不等于证明普遍有效。``opportunity_id`` 可空（独立写作任务无机会）。
    """

    id: str
    opportunity_id: str | None = None
    kind: ArtifactKind
    revision: int = 1
    path: str = ""
    source_refs: list[str] = Field(default_factory=list)
    author_note: str = ""
    material_origin: MaterialOrigin = MaterialOrigin.SYNTHETIC
    execution_status: ExecutionStatus = ExecutionStatus.N_A
    # 方法复用元数据（可选，默认安全）：方法出处与 contribution_refs 分开，永不进 source_refs。
    method_ref: str | None = None
    method_version_hash: str = ""
    response_focus: str = ""
    contribution_refs: list[str] = Field(default_factory=list)
    fit_reason: str = ""
    style_policy_version: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SkipAssessment(BaseModel):
    """否定评估缓存（规范 §11.3）：同指纹已跳过则不重复调 LLM。

    只缓存 ``skipped``；``eval_failed`` 为瞬时失败，不得缓存以免永久抑制重试。
    """

    opportunity_id: str
    person_ref: str
    fingerprint: str
    reason: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
