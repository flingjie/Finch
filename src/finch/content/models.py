"""Content 数据模型（Writer/Critic）。"""

from enum import StrEnum

from pydantic import BaseModel, Field

from finch.evidence.models import ClaimConfidence


class DraftKind(StrEnum):
    REPLY = "reply"
    ORIGINAL = "original"


class RecommendedFormat(StrEnum):
    """观点候选的推荐表达形式（连接优先改造 Phase 4）。"""

    REPLY = "reply"
    QUOTE = "quote"
    SHORT_POST = "short_post"
    THREAD = "thread"
    DM = "dm"
    DO_NOT_PUBLISH = "do_not_publish"


def draft_kind_for(fmt: RecommendedFormat) -> DraftKind:
    """把推荐格式映射为实际草稿种类（``Draft.kind`` 只区分 reply / original）。

    reply / quote / dm 是「回应型」内容 → REPLY；short_post / thread / do_not_publish →
    ORIGINAL。``do_not_publish`` 与 ``dm`` 的「不公开」由发布门禁与 Critic 场景规则把关，
    本映射只决定草稿种类，不决定是否发布。
    """
    if fmt in (RecommendedFormat.REPLY, RecommendedFormat.QUOTE, RecommendedFormat.DM):
        return DraftKind.REPLY
    return DraftKind.ORIGINAL


class ClaimRef(BaseModel):
    statement: str
    evidence_card_id: str
    confidence: ClaimConfidence


class Draft(BaseModel):
    id: str
    kind: DraftKind
    candidate_id: str | None = None   # reply 有；original 为 None
    language: str = "en"              # reply="en"；original="zh"
    body: str
    claims: list[ClaimRef] = Field(default_factory=list)
    content_job_id: str | None = None
    position_statement: str = ""
    critic_report_id: str | None = None
    run_id: str = ""                  # 当次 run（回溯到来源）
