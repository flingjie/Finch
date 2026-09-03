"""Critic Suite 检查器协议（Task 4）：可解释的逐项检查。

每个检查器对一份 Draft 做单一维度的判断，输出结构化的 ``CheckResult``：
``locations``（具体句子/claim 位置）、``issues``（失败原因）、
``rewrite_instructions``（修改指令）、``severity``（严重级别）。
其中 ``hard_fail`` 是独立于 ``high`` 的级别：hard fail 不可被平均分掩盖。
"""

import re
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from finch.content.jobs import ContentJob
from finch.content.models import Draft
from finch.evidence.models import EvidenceCard

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|(?<=\n)")


def split_sentences(body: str) -> list[str]:
    """把草稿正文切成句子（去掉空串与首尾空白）。Specificity/Portability 共用。"""
    return [s.strip() for s in _SENTENCE_SPLIT.split(body) if s.strip()]


class CheckResult(BaseModel):
    """单个检查器的结果。通过时 passed=True（severity 惯例为 "low"）。"""

    checker: str
    passed: bool
    severity: Literal["low", "medium", "high", "hard_fail"]
    locations: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    rewrite_instructions: list[str] = Field(default_factory=list)
    requires_human_input: bool = False


class CheckContext(BaseModel):
    """检查器输入：草稿 + 可用证据卡 +（可选的）绑定的 Content Job。"""

    draft: Draft
    cards: list[EvidenceCard]
    job: ContentJob | None = None


class Checker(Protocol):
    """检查器协议：实现 ``name`` 与 ``check(ctx)`` 即可。"""

    name: str

    def check(self, ctx: CheckContext) -> CheckResult: ...
