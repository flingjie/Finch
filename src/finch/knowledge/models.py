"""KnowledgeCard 领域模型：可复用结论（实践结果的复用落点）。

本文件只放模型，不 import repository（避免与 ``storage/repositories.py`` 循环导入）。
"""

import hashlib
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

KnowledgeBasis = Literal["source_claim", "user_reported", "inferred"]


class ReuseEvent(BaseModel):
    """一次实际复用事件（用户明确在另一问题复用；不是搜索曝光）。"""

    problem_id: str
    note: str = ""
    created_at: datetime


class KnowledgeCard(BaseModel):
    """一条可复用结论 + 边界；复用事件显式记录。

    ``basis`` 三分：来源主张（source_claim）/ 用户报告（user_reported）/ 系统推断（inferred）。
    推断必须标 ``inferred``，不自动升格为事实。
    """

    id: str
    claim: str
    basis: KnowledgeBasis
    source_refs: list[str] = Field(default_factory=list)
    attempt_ids: list[str] = Field(default_factory=list)
    problem_ids: list[str] = Field(default_factory=list)
    applicability: str = ""  # 适用条件
    limitations: str = ""  # 边界/局限
    reuse_events: list[ReuseEvent] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


def knowledge_id_for(claim: str) -> str:
    """内容寻址 id：相同 claim → 相同 id（幂等）。"""
    return f"knowledge_{hashlib.sha256(claim.strip().encode('utf-8')).hexdigest()[:8]}"
