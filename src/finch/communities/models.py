"""Community（社区匹配与进入）领域模型。

社区是"关系发生的场"，不是人也不是问题：核心 Builder 进 People/Connection 流程，
社区里的问题进 Idea Discovery。MVP 只处理公开数据，公开证据引用是硬门槛；
不接私有 Discord/Slack 消息，不自动加入/发言。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class CommunityResult(StrEnum):
    """一次推荐之后的跟进状态（简单、可累积，用于度量"第二次交流"）。"""

    IGNORED = "ignored"
    SAVED = "saved"
    JOINED = "joined"
    INTERACTED = "interacted"
    REPEATED = "repeated"
    CONTRIBUTED = "contributed"


class RecommendationState(StrEnum):
    """Finch 建议的社区状态（与用户实际动作 CommunityResult 正交）。"""

    OBSERVE = "observe"
    ACTIONABLE = "actionable"


class CommunityEvidence(BaseModel):
    """一条近期公开活动证据（硬门槛 3：推荐结论必须能引用公开证据）。"""

    topic: str
    relevance: str
    url: str = ""


class CommunityPerson(BaseModel):
    """社区里值得关注的核心 Builder。"""

    name: str
    reason: str


class EntryPoint(BaseModel):
    """一个可切入的具体讨论 + 建议角度。"""

    discussion: str
    suggested_angle: str
    url: str = ""
    status: str = ""  # open / closed / unknown / 空


class FirstContribution(BaseModel):
    """用户能贡献的代码 / 案例 / 工具。"""

    type: str
    proposal: str


class CommunityProfile(BaseModel):
    """一张社区行动卡（每周 3 张）。

    ``id`` 由 ``name`` 内容寻址；为空时由 ``CommunityService.save`` 补全。
    """

    id: str = ""
    name: str
    platforms: list[str] = Field(default_factory=list)
    fit_score: int = Field(default=0, ge=0, le=100)
    why_fit: list[str] = Field(default_factory=list)
    recent_evidence: list[CommunityEvidence] = Field(default_factory=list)
    people: list[CommunityPerson] = Field(default_factory=list)
    entry_point: EntryPoint | None = None
    first_contribution: FirstContribution | None = None
    canonical_url: str = ""
    recommendation_state: RecommendationState | None = None
    intent: str = ""
    question: str = ""
    practice_refs: list[str] = Field(default_factory=list)
    source_checked_at: datetime | None = None
    risks: list[str] = Field(default_factory=list)
    evidence_urls: list[str] = Field(default_factory=list)
    week: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CommunityFeedback(BaseModel):
    """一次跟进反馈（append-only，永不覆盖）。"""

    community_id: str
    result: CommunityResult
    note: str = ""
    reason_kind: str = ""  # no_time / too_general / language_barrier / deep_but_later …
    interaction_ref: str = ""  # 真实互动链接；空=未提供
    ref_kind: str = ""  # public_url / user_stated
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CommunityContext(BaseModel):
    """一次 discover 的输入上下文快照（可审计：本轮推荐基于什么）。"""

    week: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    interests: list[str] = Field(default_factory=list)
    current_questions: list[str] = Field(default_factory=list)
    practice_refs: list[str] = Field(default_factory=list)
    active_peers: list[str] = Field(default_factory=list)
    recent_ideas: list[str] = Field(default_factory=list)


def community_id_for(name: str) -> str:
    """内容寻址 id：相同 name → 相同 id（幂等）；相似但不同 name 不合并。"""
    digest = hashlib.sha256(name.strip().encode("utf-8")).hexdigest()
    return f"comm_{digest[:12]}"


def identity_key(profile: CommunityProfile) -> str:
    """跨周去重键：有规范 URL 用 URL，否则回退 name-hash id（不凭名称合并不同社区）。"""
    return profile.canonical_url or profile.id or community_id_for(profile.name)


class RunIntent(StrEnum):
    """community-scout 三种入口（对齐 SKILL）。"""

    WEEKLY = "weekly"
    QUESTION = "question"
    REVISIT = "revisit"


class ScoutAction(StrEnum):
    """薄 loop 的动作集（固定序；Python 决定下一步，LLM 不选动作）。"""

    SEARCH = "search"
    INSPECT = "inspect"
    PROPOSE = "propose"
    FINISH = "finish"


class CommunityCandidate(BaseModel):
    """search 动作产出的一个候选社区（尚未经 inspect 核验）。"""

    name: str
    canonical_url: str = ""
    source_note: str = ""
    evidence_text: str = ""


class ScoutObservation(BaseModel):
    """一个动作的结构化观察（按 action 不同只填相关字段）。"""

    candidates: list[CommunityCandidate] = Field(default_factory=list)
    verified: list[CommunityProfile] = Field(default_factory=list)
    rejected: list[dict] = Field(default_factory=list)  # {name, reason}
    cards: list[CommunityProfile] = Field(default_factory=list)
    gap_note: str = ""


class RunStep(BaseModel):
    """决策记录一行：goal/action/observation/decision/outcome + 耗时/调用次数。"""

    run_id: str
    action: ScoutAction
    observation: ScoutObservation = Field(default_factory=ScoutObservation)
    decision: str = ""
    outcome: str = ""
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    elapsed_ms: int = 0
    llm_calls: int = 0


class CommunityRun(BaseModel):
    """一次 run 头：intent/goal/状态/预算消耗/卡数。"""

    run_id: str
    intent: RunIntent
    goal: str
    week: str
    status: str  # running | done | failed | stopped
    candidates_found: int = 0
    cards_proposed: int = 0
    budget_used: int = 0
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None


class FeedbackFacts(BaseModel):
    """从 feedback 派生的确定性事实（硬门禁 + 软排序摘要）。"""

    excluded: dict[str, str] = Field(default_factory=dict)  # identity_key -> 原因
    continue_framing: list[str] = Field(default_factory=list)  # 回访须「继续」框架的 identity
    summaries: dict[str, str] = Field(default_factory=dict)  # identity_key -> 摘要
