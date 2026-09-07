"""收件箱投影模型（产品层只读投影 + 唯一权威决策记录）。"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class InboxTrack(StrEnum):
    """收件箱轨道：原创（ContentJob+Draft）或互动（InteractionCandidate）。"""

    ORIGINAL = "original"
    ENGAGEMENT = "engagement"


class InboxItem(BaseModel):
    """一条待决策项的统一投影。"""

    id: str                    # original=job_id；engagement=candidate.id
    track: InboxTrack
    content_type: Literal["original", "reply", "quote"]
    provenance: Literal["personal", "external", "idea"]
    source_refs: list[str]     # commit URL / 帖子 URL / idea 文本摘要
    why_now: str
    score: float               # 原创用 original_score；互动用 ConversationScore.total
    draft_id: str | None = None
    draft: str = ""
    position: dict | None = None  # claim/decision/tradeoff；互动可空
    must_ask: bool = False
    ask_reasons: list[str] = []
    risks: list[str] = []


class DecisionAction(StrEnum):
    ACCEPT = "accept"
    REVISE = "revise"
    SKIP = "skip"


class DecisionRecord(BaseModel):
    """一次原子决策的唯一权威记录。"""

    id: str                                   # "dec_<job_id>"（幂等键）
    job_id: str
    draft_id: str
    action: DecisionAction
    approved_content_hash: str                # 采用时绑定最终正文；revise 改变 hash → 旧批准失效
    revised_body: str | None = None
    diff: str | None = None
    decided_at: datetime


class SkipReason(StrEnum):
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    NOT_RELEVANT = "not_relevant"
    LOW_QUALITY = "low_quality"
    NOT_NOW = "not_now"
    OTHER = "other"
    NO_CLEAR_POSITION = "no_clear_position"
    GENERIC_VOICE = "generic_voice"
    JOB_NOT_USEFUL = "job_not_useful"
    FACT_ERROR = "fact_error"
