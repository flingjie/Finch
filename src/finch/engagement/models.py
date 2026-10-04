"""Engagement 领域模型（关系与表达交互事实的持久化模型）。

旧的互动轨道（ExternalPost / ConversationScore / InteractionProposal / Opportunity）
已由 ``finch.opportunities`` 聚合取代；本模块只保留关系与表达链路仍用的快照、反馈、
互动事实与证据模型。
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class RecommendationEntry(BaseModel):
    """快照持久化的一条 50 人分层推荐（F1：非刷新读取可重放）。

    字段与 ``cli._recommendations_payload`` 的展示行一致；顺序由列表与 ``rank`` 保留。
    """

    person_id: str
    peer_id: str
    display_name: str = ""
    tier: str = ""  # priority | summary | browse
    rank: int = 0
    direction: str = ""
    platform: str = ""
    score: float = 0.0
    artifact_ids: list[str] = Field(default_factory=list)
    hit_labels: list[str] = Field(default_factory=list)


class OpportunityAssessmentEntry(BaseModel):
    """快照持久化的一条首选机会评估结果（非刷新可读；含 skip 原因）。"""

    person_id: str
    outcome: Literal["recommended", "skipped", "eval_failed"]
    reason: str = ""
    opportunity_id: str | None = None
    fingerprint: str = ""


class DiscoverySnapshot(BaseModel):
    """一次有界发现刷新的缓存快照（B）。"""

    id: str
    created_at: datetime
    context_fingerprint: str
    source_coverage: dict[str, object] = Field(default_factory=dict)
    failures: list[dict[str, str]] = Field(default_factory=list)
    ranked_opportunity_ids: list[str] = Field(default_factory=list)
    ranking_version: str = "1"
    selected_opportunity_ids: list[str] = Field(default_factory=list)
    # 本轮查询计划（P1）：plan_id 稳定、plan_summary 记录窗口/来源/排除项摘要。
    plan_id: str = ""
    plan_summary: dict[str, object] = Field(default_factory=dict)
    # 完整 50 人分层推荐 + 数量缺口（F1：刷新后持久化，非刷新读取可重放）
    recommendations: list[RecommendationEntry] = Field(default_factory=list)
    recommendation_shortfall: dict[str, int] = Field(default_factory=dict)
    # 首页 3 个重点发现（D7：与 50 人浏览来自同一快照，按序持久化）
    home_person_ids: list[str] = Field(default_factory=list)
    # 首选机会（新聚合）id：首页 0-1 条首选；空表示本轮无值得优先投入的机会。
    preferred_opportunity_id: str = ""
    # 本轮评估覆盖（含跳过原因）：非刷新读取可重放「为何无首选 / 为何优先」。
    opportunity_assessments: list[OpportunityAssessmentEntry] = Field(
        default_factory=list
    )


class PresentationRecord(BaseModel):
    """实际呈现给用户的机会（仅展示时写入，后台生成不算看过）。"""

    id: str
    snapshot_id: str
    opportunity_id: str
    presented_at: datetime
    # D10：展示语义版本；历史"生成即曝光"数据默认 "1"，新写入 "2"。
    presentation_semantics_version: str = "1"


class InterestFeedbackValue(StrEnum):
    WORTH_FOLLOWING = "worth_following"
    NEUTRAL = "neutral"
    UNSUITABLE = "unsuitable"


class ActionFeedbackValue(StrEnum):
    PREPARE = "prepare"
    SAVE_FOR_LATER = "save_for_later"
    NO_OPENING = "no_opening"
    # 轻量反馈：今天没时间/暂时跳过 —— 瞬态信号，不得映射为长期排斥。
    NO_TIME_TODAY = "no_time_today"


class OutcomeFeedbackValue(StrEnum):
    """推荐后的轻量结果：是否采用并回复 / 得到补充信息 / 再次交流或共同实践。"""

    ADOPTED_REPLIED = "adopted_replied"
    GOT_MORE_INFO = "got_more_info"
    REENGAGED = "reengaged"


class RecommendationFeedback(BaseModel):
    """推荐反馈（兴趣 / 行动 / 结果维度分离；非 DecisionRecord）。"""

    id: str
    opportunity_id: str
    snapshot_id: str
    dimension: Literal["interest", "action", "outcome"]
    value: str
    reason: str = ""
    created_at: datetime


class VerificationStatus(StrEnum):
    SOURCE_VERIFIED = "source_verified"
    USER_ATTESTED = "user_attested"
    UNVERIFIED = "unverified"


class InteractionRecord(BaseModel):
    """已发生的互动事实：单独记录，不得用提案状态替代。

    ``proposal_id`` 可空（系统外发生的交流）；``published_body`` / ``body`` 是真正发出
    的正文；``occurred_at`` 是互动发生时间（重跑不得刷新）；``verification_status``
    区分平台验证 / 用户声明 / 未验证。
    """

    id: str
    proposal_id: str | None = None
    opportunity_id: str | None = None
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
    讨论信号。``origin`` 恒为 ``"conversation"``：外部帖子绝不直接成为 personal 证据。
    """

    id: str
    interaction_id: str
    post_id: str
    origin: Literal["conversation"] = "conversation"
    kind: Literal["question", "disagreement", "hypothesis", "experiment"]
    statement: str
    verified: bool = False


class EngagementRunStats(BaseModel):
    """互动轨道单轮运行级计数（执行计划 Phase 7 可观测性）。"""

    run_id: str
    posts_scanned: int = 0
    candidates: int = 0
    drafts: int = 0
    latency_ms: int = 0
