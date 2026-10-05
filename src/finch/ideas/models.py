"""Idea 候选流领域模型（Skill 架构领域核心）。

统一 `IdeaCandidate` 契约：Skill 层（idea-discovery / conversation-scout）产出同一个
`IdeaCandidate`，领域服务再把它持久化到 ``ContentJob``（不新增 Idea 表）。

契约不变量（plan §3）：
- ``core_point`` 是单一中心主张；
- ``source_refs`` 可追溯来源；
- 自动生成立场一律 ``proposed``，只有用户输入或命中已批准 fingerprint 才是 ``confirmed``；
- Search 来源不得写成亲历；
- ``boundaries.known/inferred/unknown`` 传递到 Draft 校验。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from finch.content.jobs import (
    AuthorPosition,
    CommunicationGoal,
    EvidenceStatus,
    IdeaOrigin,
    SourceKind,
)
from finch.content.models import ContentType, RecommendedFormat


class SourceRef(BaseModel):
    """来源引用：类型 + 引用标识 + 一句话摘要（保证可追溯）。"""

    type: Literal["commit", "pr", "issue", "test", "post", "paper", "conversation", "attempt"]
    ref: str
    summary: str


class IdeaBoundaries(BaseModel):
    """证据边界：已知 / 推断 / 未知，传递到 Draft 校验（不得越界陈述）。"""

    known: list[str] = Field(default_factory=list)
    inferred: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)


TakeawayKind = Literal["diagnosis", "decision_criteria", "method", "pitfall"]
EvidenceSupport = Literal["observed_this_run", "inferred_cause", "unverified_general"]
MethodUseAs = Literal["idea_angle", "draft_technique"]


class IdeaGenerator(BaseModel):
    """生成器元数据：哪个 Skill 与版本产出了该候选。"""

    skill: str
    version: str


class MethodSelection(BaseModel):
    """方法匹配结果：适配原因与素材缺口（不进入事实 source_refs）。"""

    method_id: str
    fit_reason: str
    material_refs: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    use_as: MethodUseAs


class DraftTechniqueNote(BaseModel):
    """仅影响写法的技巧建议，不单独成为 idea。"""

    method_id: str
    note: str


class IdeaAngle(BaseModel):
    """发散后通过核验的一个候选角度（选中后映射为 IdeaCandidate）。"""

    index: int
    core_point: str
    reader_situation: str
    reader_takeaway: str
    takeaway_kind: TakeawayKind
    evidence_support: EvidenceSupport
    counterexample_or_limit: str
    source_refs: list[SourceRef]
    method_id: str | None = None
    use_as: MethodUseAs | None = None
    fit_reason: str = ""
    missing_requirements: list[str] = Field(default_factory=list)


class RejectedAngle(BaseModel):
    """被淘汰的角度及其理由（核验或收敛阶段）。"""

    index: int
    core_point: str
    reason: str


class Selection(BaseModel):
    """一次选择（自动推荐或改选），可追溯。"""

    index: int
    job_id: str
    at: datetime


class IdeaExploration(BaseModel):
    """一次发散的中间结果：多角度 + 淘汰理由 + 选择历史（持久化，便于改选）。"""

    id: str
    origin: IdeaOrigin
    source_kind: SourceKind | None = None
    evidence_status: EvidenceStatus | None = None
    facts: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    boundaries: IdeaBoundaries = Field(default_factory=IdeaBoundaries)
    angles: list[IdeaAngle] = Field(default_factory=list)
    rejected_angles: list[RejectedAngle] = Field(default_factory=list)
    recommended_index: int | None = None
    recommendation_reason: str = ""
    selections: list[Selection] = Field(default_factory=list)
    method_selections: list[MethodSelection] = Field(default_factory=list)
    draft_techniques: list[DraftTechniqueNote] = Field(default_factory=list)
    generator: IdeaGenerator


class FactBundle(BaseModel):
    """进入发散前的一束可追溯事实（commit 路径产出；后续 fragment 来源复用）。"""

    facts: list[str]
    source_refs: list[SourceRef]
    boundaries: IdeaBoundaries
    evidence_status: EvidenceStatus
    origin: IdeaOrigin
    source_kind: SourceKind


class IdeaCandidate(BaseModel):
    """Idea 候选：Skill 层产出的统一输入契约。"""

    id: str
    origin: IdeaOrigin
    core_point: str
    observation: str = ""
    reader_problem: str
    why_worth_saying: str
    intent: Literal["stance", "exploration"] = "stance"
    open_question: str = ""
    author_position: AuthorPosition
    source_refs: list[SourceRef]
    boundaries: IdeaBoundaries
    recommended_format: RecommendedFormat
    communication_goal: CommunicationGoal | None = None
    generator: IdeaGenerator
    source_kind: SourceKind | None = None
    facts: list[str] = Field(default_factory=list)
    interpretation: str = ""
    evidence_status: EvidenceStatus | None = None
    limitations: str = ""
    reader_situation: str = ""
    reader_takeaway: str = ""
    takeaway_kind: TakeawayKind | None = None
    evidence_support: EvidenceSupport | None = None
    counterexample_or_limit: str = ""
    method_id: str | None = None
    method_use_as: MethodUseAs | None = None
    method_fit_reason: str = ""
    method_version_hash: str = ""

    # ---- Learning loop 回链 ----
    content_type: ContentType | None = None
    attempt_id: str | None = None
    problem_id: str | None = None
