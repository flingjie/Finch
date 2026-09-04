"""Engagement 领域模型（执行计划 4 数据与类型设计）。"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

EvidenceOrigin = Literal["personal", "external", "conversation"]
Platform = Literal["x", "reddit"]


class ExternalPost(BaseModel):
    """搜索到的外部帖子（外部信号，未经验证不可直接升级为个人证据）。"""

    id: str
    platform: Platform
    url: str
    author_id: str
    author_name: str
    content: str
    published_at: datetime
    metrics: dict[str, int | float] = Field(default_factory=dict)
    matched_topics: list[str] = Field(default_factory=list)


class ConversationScore(BaseModel):
    """互动候选五维评分；total 由后续评分阶段确定性计算，模型不得直接决定。"""

    relevance: float = Field(ge=0, le=1)
    novelty: float = Field(ge=0, le=1)
    discussability: float = Field(ge=0, le=1)
    practical_evidence: float = Field(ge=0, le=1)
    relationship_value: float = Field(ge=0, le=1)
    total: float = Field(ge=0, le=1)
    reasons: list[str]


class InteractionAction(StrEnum):
    """互动建议动作（执行计划 4）。"""

    IGNORE = "ignore"
    BOOKMARK = "bookmark"
    OBSERVE_AUTHOR = "observe_author"
    DRAFT_REPLY = "draft_reply"
    DRAFT_QUOTE = "draft_quote"
    DRAFT_DM = "draft_dm"


class InteractionStatus(StrEnum):
    """互动候选审批/执行状态（执行计划 5 审批与执行保护）。"""

    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    EXPIRED = "expired"


class InteractionCandidate(BaseModel):
    """互动候选：帖子 + 评分 + 建议动作 + 草稿 + 审批状态。

    ``id`` 是稳定幂等键（``<platform>:<post_id>:<action>``，同一帖子同一动作恒定），
    用于审批队列的去重与执行防重复发送。``draft`` 是纯正文字符串；``intent``/
    ``source_summary``/``factual_risks`` 记录草稿意图、所回应的帖子片段摘要与事实风险标记
    （执行计划 4 Phase 4）。``revised_draft`` 保存人工修订版本（不改变发布权限）；
    ``reject_reason`` 记录拒绝理由（镜像 ``ContentJob.reject_reason``）。
    """

    id: str
    post: ExternalPost
    score: ConversationScore
    action: InteractionAction
    draft: str | None = None
    intent: str | None = None
    source_summary: str | None = None
    factual_risks: list[str] = Field(default_factory=list)
    revised_draft: str | None = None
    reject_reason: str | None = None
    approval_required: bool
    status: InteractionStatus = InteractionStatus.PROPOSED


class FeedbackSnapshot(BaseModel):
    """互动结果反馈快照（执行计划 Phase 6 反馈回流）。

    记录一次互动执行后的回复/点赞数量，以及是否获得实质回复（``meaningful``）。
    ``interaction_id`` 回溯到 ``InteractionCandidate.id``，与 ``ConversationEvidence``
    共享同一 id 链路，构成 candidate → snapshot → conversation evidence → personal
    evidence 的可追溯闭环。
    """

    id: str
    interaction_id: str
    replies: int = 0
    likes: int = 0
    meaningful: bool = False
    captured_at: datetime


class ConversationEvidence(BaseModel):
    """互动讨论中提取的候选证据（``conversation`` 类型，执行计划 Phase 6）。

    ``kind`` 区分问题 / 分歧 / 可验证假设 / 可能的实验；``verified`` 为 False 时仅作为
    讨论信号，只有经个人实践或外部验证并提升（见 ``evidence_upgrade.promote_to_personal``）
    后才成为 personal 证据。``origin`` 恒为 ``"conversation"``：外部帖子（``external``）
    是独立的 ``ExternalPost`` 模型，绝不直接成为 personal 证据。
    """

    id: str
    interaction_id: str
    post_id: str
    origin: Literal["conversation"] = "conversation"
    kind: Literal["question", "disagreement", "hypothesis", "experiment"]
    statement: str
    verified: bool = False


class Verification(BaseModel):
    """conversation → personal 升级所需的验证证据（执行计划 4 证据升级规则）。

    ``kind`` 必须是 case / code / experiment / multi_source 之一；``detail`` 描述验证来源
    （真实案例、代码、实验或多来源交叉验证），供后续原创内容引用个人新增判断时审计。
    """

    kind: Literal["case", "code", "experiment", "multi_source"]
    detail: str


class EngagementRunStats(BaseModel):
    """互动轨道单轮运行级计数（执行计划 Phase 7 可观测性）。

    与质量指标（``engagement.metrics.compute_metrics``）互补：这些是运行级计数，回答
    「扫了多少帖子、产出多少候选与草稿、单轮耗时」，供 ``no_evidence_runs`` /
    ``posts_scanned`` / 单轮延迟等运行级信号聚合。模型成本需要 CodexRunner 做 per-run
    token/cost 埋点，本阶段不在本模型记录（明确 OUT of scope）。
    """

    run_id: str
    posts_scanned: int = 0
    candidates: int = 0
    drafts: int = 0
    latency_ms: int = 0
