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
    """互动建议审批/执行状态。"""

    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    EXPIRED = "expired"


class ContributionType(StrEnum):
    """互动建议的贡献类型：这一次互动打算给对方带去什么。"""

    EXPERIENCE = "experience"
    QUESTION = "question"
    ADDITION = "addition"
    COUNTEREXAMPLE = "counterexample"
    RESOURCE = "resource"


class InteractionProposal(BaseModel):
    """互动建议：待批准的候选，不是已发生的互动。

    ``id`` 是稳定幂等键（``<platform>:<post_id>:<action>``，同一帖子同一动作恒定），
    用于审批队列的去重与执行防重复发送。``draft`` 是纯正文字符串；``intent``/
    ``source_summary``/``factual_risks`` 记录草稿意图、所回应的帖子片段摘要与事实风险标记。
    ``revised_draft`` 保存人工修订版本（不改变发布权限）；``reject_reason`` 记录拒绝理由。

    ``peer_id`` / ``contribution_type`` / ``relationship_context`` / ``why_this_person`` /
    ``why_now`` / ``expected_conversation_opening`` 是连接优先新增的关系字段（Phase 2 的
    同行发现填充）。``generation_key`` 编码 ``peer + source + action + prompt_version``，
    用于幂等：相同 key 不重复调用 LLM 或创建 Proposal。真正发生的互动记录在
    :class:`InteractionRecord`，不得用本建议的状态替代事实。
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
    peer_id: str | None = None
    contribution_type: ContributionType | None = None
    relationship_context: str | None = None
    why_this_person: str | None = None
    why_now: str | None = None
    expected_conversation_opening: str | None = None
    generation_key: str | None = None


class SuggestedMode(StrEnum):
    """交流机会建议模式：先了解 / 讨论 / 调查。"""

    LEARN = "learn"
    DISCUSS = "discuss"
    INVESTIGATE = "investigate"


class Opportunity(BaseModel):
    """轻量交流机会（发现结果，无审批状态机）。

    ``InteractionProposal`` 是用户选中后深度准备的提案；本模型只承载浏览列表。
    ``id`` 由 peer_id + 规范来源 + 内容版本派生，稳定可重放。
    """

    id: str
    peer_id: str
    source_refs: list[str] = Field(default_factory=list)
    source_excerpt: str = ""
    content_fingerprint: str = ""
    discovered_via: str = ""
    topic_tags: list[str] = Field(default_factory=list)
    role_tags: list[str] = Field(default_factory=list)
    tags_inferred: bool = True
    why_relevant: str = ""
    opening: str = ""
    suggested_mode: SuggestedMode = SuggestedMode.DISCUSS
    novelty_reason: str = ""
    uncertainty: str = ""
    assessed_at: datetime | None = None
    assessment_version: str = "1"
    # Optional score snapshot for ranking (deterministic total from Python).
    score_total: float = 0.0
    complementarity: float = 0.0
    post: ExternalPost | None = None
    related_source_refs: list[str] = Field(default_factory=list)


class DiscoverySnapshot(BaseModel):
    """一次有界发现刷新的缓存快照（B）。"""

    id: str
    created_at: datetime
    context_fingerprint: str
    source_coverage: dict[str, object] = Field(default_factory=dict)
    failures: list[dict[str, str]] = Field(default_factory=list)
    ranked_opportunity_ids: list[str] = Field(default_factory=list)
    ranking_version: str = "1"


class PresentationRecord(BaseModel):
    """实际呈现给用户的机会（仅展示时写入，后台生成不算看过）。"""

    id: str
    snapshot_id: str
    opportunity_id: str
    presented_at: datetime


class InterestFeedbackValue(StrEnum):
    WORTH_FOLLOWING = "worth_following"
    NEUTRAL = "neutral"
    UNSUITABLE = "unsuitable"


class ActionFeedbackValue(StrEnum):
    PREPARE = "prepare"
    SAVE_FOR_LATER = "save_for_later"
    NO_OPENING = "no_opening"


class RecommendationFeedback(BaseModel):
    """推荐反馈（兴趣 / 行动维度分离；非 DecisionRecord）。"""

    id: str
    opportunity_id: str
    snapshot_id: str
    dimension: Literal["interest", "action"]
    value: str
    reason: str = ""
    created_at: datetime


class VerificationStatus(StrEnum):
    SOURCE_VERIFIED = "source_verified"
    USER_ATTESTED = "user_attested"
    UNVERIFIED = "unverified"


class InteractionRecord(BaseModel):
    """已发生的互动事实：单独记录，不得用 Proposal 状态替代。

    ``proposal_id`` 可空（系统外发生的交流）；``published_body`` / ``body`` 是真正发出
    的正文；``occurred_at`` 是互动发生时间（重跑不得刷新）；``verification_status``
    区分平台验证 / 用户声明 / 未验证。
    """

    id: str
    proposal_id: str | None = None
    peer_id: str
    platform: str
    source_url: str
    published_body: str = ""
    body: str = ""
    occurred_at: datetime
    observed_at: datetime | None = None
    outcome: str = ""
    reply_refs: list[str] = Field(default_factory=list)
    follow_up_status: str = "none"
    follow_up_at: datetime | None = None
    platform_message_id: str | None = None
    author_identity: str | None = None
    direction: Literal["outbound", "inbound", "unknown"] = "unknown"
    reply_to_id: str | None = None
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    provenance: str = ""


class FeedbackSnapshot(BaseModel):
    """互动结果反馈快照（执行计划 Phase 6 反馈回流）。

    记录一次互动执行后的回复/点赞数量，以及是否获得实质回复（``meaningful``）。
    ``interaction_id`` 回溯到 ``InteractionProposal.id``，与 ``ConversationEvidence``
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
