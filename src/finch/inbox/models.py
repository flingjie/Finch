"""收件箱投影模型（产品层只读投影）。"""

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
