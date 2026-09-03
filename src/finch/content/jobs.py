"""Content Job 模型（Spec §8）：定义内容目标与作者立场。"""

from enum import StrEnum
from json import dumps
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field

from finch.codex.runner import CodexRunner
from finch.content.models import DraftKind
from finch.evidence.models import EvidenceCard


class ContentJobsOutput(BaseModel):
    """define-content-jobs prompt 的输出：ContentJob 列表。"""

    items: list["ContentJob"]


class ContentJobStatus(StrEnum):
    """Content Job 状态枚举。"""

    PROPOSED = "proposed"
    NEEDS_INPUT = "needs_input"
    READY = "ready"
    DO_NOT_WRITE = "do_not_write"


class IntendedEffect(BaseModel):
    """内容预期产生的效果。"""

    understand: str
    believe: str | None = None
    action: str | None = None


class AuthorPosition(BaseModel):
    """作者立场：判断是否值得写。"""

    claim: str
    decision: str
    tradeoff: str
    change_mind_if: str | None = None
    confirmed: bool = False


class SuccessCriterion(BaseModel):
    """成功标准：如何衡量内容是否达成目标。"""

    id: str
    description: str
    measurement: Literal["critic", "human", "outcome"]


class ContentJob(BaseModel):
    """
    Content Job: define what the content must accomplish.

    - source_card_ids: linked Evidence Card IDs (SUBSET check: must be a subset
      of MatchResult.card_ids)
    - candidate_id: linked RankedCandidate ID
    - reader_problem: current confusion/problem facing target readers
    - audience:明确的目标读者群体 (e.g., 'backend engineers', 'SREs')
    - intended_effect: intended impact after reading
    - author_position: author's position on whether to write
    - success_criteria: list of success metrics
    - recommended_format: recommended content format (from DraftKind)
    - status: task status
    - missing_questions: open questions needing answers (max 3)
    """

    id: str
    source_card_ids: list[str]
    candidate_id: str | None = None
    reader_problem: str
    audience: str
    intended_effect: IntendedEffect
    author_position: AuthorPosition | None = None
    success_criteria: list[SuccessCriterion]
    recommended_format: DraftKind
    status: ContentJobStatus
    missing_questions: list[str] = Field(default_factory=list, max_length=3)

    def validate_source_cards(self, available_card_ids: list[str]) -> bool:
        """
        校验 source_card_ids 是否为 available_card_ids 的子集。

        返回 True 表示所有 source cards 都在可用范围内。
        """
        return set(self.source_card_ids).issubset(set(available_card_ids))

    def needs_input(self) -> bool:
        """判断 job 是否处于 NEEDS_INPUT 状态（缺少必要信息）。"""
        if self.status == ContentJobStatus.NEEDS_INPUT:
            return True
        if self.author_position is None:
            return True
        if not self.author_position.decision or not self.author_position.tradeoff:
            return True
        return False


def infer_status(job: ContentJob) -> ContentJobStatus:
    """
    根据 job 的内容推断其应处的状态。

    - 缺失 author_position 或 author_position.decision/tradeoff → NEEDS_INPUT
    - 明确 DO_NOT_WRITE → DO_NOT_WRITE
    - 其他 → READY
    """
    if job.status == ContentJobStatus.DO_NOT_WRITE:
        return ContentJobStatus.DO_NOT_WRITE

    if job.author_position is None:
        return ContentJobStatus.NEEDS_INPUT

    if not job.author_position.decision or not job.author_position.tradeoff:
        return ContentJobStatus.NEEDS_INPUT

    return ContentJobStatus.READY


def define_content_jobs(runner: CodexRunner, cards: list[EvidenceCard]) -> ContentJobsOutput:
    """
    调用 Codex runner 生成 Content Job 列表。

    - 加载 prompts/define-content-jobs.md
    - 格式化 evidence cards
    - 返回 ContentJobsOutput (items: list[ContentJob])
    """
    # 用 .replace 而非 .format：prompt 里含 JSON 示例的字面大括号，会被 .format 误解析。
    template = Path("prompts/define-content-jobs.md").read_text()
    prompt = template.replace("{cards}", dumps([c.model_dump(mode="json") for c in cards]))
    result = runner.run(prompt, ContentJobsOutput)
    return cast(ContentJobsOutput, result)
