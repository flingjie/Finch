"""Content Job 模型：idea 候选流的持久化实体（Skill 架构 Step 1）。

``ContentJob`` 是 ``IdeaCandidate`` 落库后的唯一权威表示，承载状态机
``PROPOSED → CONFIRMED → DRAFTED``（或 ``→ SKIPPED``）与作者立场。
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, field_validator

from finch.content.models import RecommendedFormat

# 交流目标：这次内容打算把对话推进到哪里（连接优先改造 Phase 4）。
CommunicationGoal = Literal[
    "continue_discussion", "invite_counterexample", "summarize_practice", "find_collaborators"
]

# Idea origin: traceability label for where the idea came from.
# Normalized to three values with legacy read-compat.
IdeaOrigin = Literal["practice", "conversation", "synthesis"]

# 旧工作区中已存的 legacy origin 值 → 新枚举（读取时归一化，避免校验崩溃）。
# ``search`` 从无生产路径，但历史上若曾落库，其语义最接近社区信号综合 → 归入 synthesis。
_LEGACY_ORIGIN = {"commit": "practice", "user": "practice", "search": "synthesis"}


class ContentJobStatus(StrEnum):
    """Content Job 状态枚举（idea 候选流状态机）。

    ``PROPOSED → CONFIRMED → DRAFTED``，或 ``PROPOSED → SKIPPED``。
    """

    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    DRAFTED = "drafted"
    SKIPPED = "skipped"


class AuthorPosition(BaseModel):
    """作者立场：判断是否值得写。"""

    claim: str
    decision: str
    tradeoff: str
    change_mind_if: str | None = None


class ContentJob(BaseModel):
    """idea 候选的持久化实体：定义内容目标、作者立场与状态。

    - reader_problem / core_message / why_now / author_position 是 Writer 与 Critic
      检查器的约束对象；
    - source_card_ids / candidate_id 供收件箱「个人证据」轨道投影（idea 流恒为空）；
    - origin / generation_key / generator_* / content_fingerprint 是幂等键与溯源元数据。
    """

    id: str
    source_card_ids: list[str]
    candidate_id: str | None = None
    reader_problem: str
    author_position: AuthorPosition | None = None
    recommended_format: RecommendedFormat
    status: ContentJobStatus
    reject_reason: str | None = None
    core_message: str = ""
    why_now: str = ""
    # ---- idea 候选流字段（Skill 架构 Step 1）----
    origin: IdeaOrigin | None = None
    observation: str = ""
    intent: Literal["stance", "exploration"] = "stance"
    open_question: str = ""
    communication_goal: CommunicationGoal | None = None
    generation_key: str | None = None
    generator_name: str | None = None
    generator_version: str | None = None
    content_fingerprint: str | None = None

    @field_validator("origin", mode="before")
    @classmethod
    def _normalize_legacy_origin(cls, v):
        if isinstance(v, str) and v in _LEGACY_ORIGIN:
            return _LEGACY_ORIGIN[v]
        return v
