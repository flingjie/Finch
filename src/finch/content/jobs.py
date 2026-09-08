"""Content Job 模型：idea 候选流的持久化实体（Skill 架构 Step 1）。

``ContentJob`` 是 ``IdeaCandidate`` 落库后的唯一权威表示，承载状态机
``PROPOSED → CONFIRMED → DRAFTED``（或 ``→ SKIPPED``）与作者立场。
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel

from finch.content.models import DraftKind


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
    recommended_format: DraftKind
    status: ContentJobStatus
    reject_reason: str | None = None
    core_message: str = ""
    why_now: str = ""
    # ---- idea 候选流字段（Skill 架构 Step 1）----
    origin: Literal["commit", "search", "user", "conversation"] | None = None
    observation: str = ""
    intent: Literal["stance", "exploration"] = "stance"
    open_question: str = ""
    generation_key: str | None = None
    generator_name: str | None = None
    generator_version: str | None = None
    content_fingerprint: str | None = None
