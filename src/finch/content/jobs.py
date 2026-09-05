"""Content Job 模型（Spec §8）：定义内容目标与作者立场。"""

import re
from enum import StrEnum
from json import dumps
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field

from finch.content.models import DraftKind
from finch.evidence.models import EvidenceCard, MatchResult
from finch.llm.base import StructuredInferenceRunner
from finch.twitter.models import DiscussionCandidate


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


class ContentScope(StrEnum):
    """内容可泛化范围：决定首稿采用的最小表达结构。"""

    GENERAL = "general"
    BOUNDED_LESSON = "bounded_lesson"
    BUILD_LOG = "build_log"
    REPLY = "reply"


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
    reject_reason: str | None = None
    core_message: str = ""
    why_now: str = ""
    scope: ContentScope = ContentScope.BOUNDED_LESSON
    audience_evidence: str | None = None

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


class TopicProposal(BaseModel):
    """plan-topics 输出的一个内容主题：主题 id、一句话标题、所属卡片与候选归属。"""

    id: str
    title: str
    card_ids: list[str]
    candidate_id: str | None = None


class PlanTopicsOutput(BaseModel):
    items: list[TopicProposal]


_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _render_prompt(template: str, values: dict[str, str]) -> str:
    """单遍占位符替换：只扫描原模板一次，插入的数据不会被再次扫描。

    链式 ``.replace`` 会重复扫描先前插入的不可信数据（卡片 claim / 主题标题 /
    候选文本），一旦其中出现 ``{cards}``/``{candidate}`` 等字面量就会被二次替换，
    造成 prompt 静默污染。这里用一次 ``re.sub`` 只替换模板里出现的占位符，
    未命中的 ``{word}``（如 JSON 示例里的裸花括号）原样保留。
    """

    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        return values.get(key, match.group(0))

    return _PLACEHOLDER_RE.sub(_sub, template)


def plan_content_topics(
    runner: StructuredInferenceRunner,
    cards: list[EvidenceCard],
    match_results: list[MatchResult],
    candidates: list[DiscussionCandidate],
) -> PlanTopicsOutput:
    """一次 flash 调用：把证据卡聚类成少量主题并决定 reply/original 归属。"""
    if not cards:
        return PlanTopicsOutput(items=[])
    slim_cards = [
        {"id": c.id, "claim": c.claim, "topics": c.topics} for c in cards
    ]
    slim_matches = [
        {"candidate_id": m.candidate_id, "card_ids": m.card_ids} for m in match_results
    ]
    slim_candidates = [{"id": c.id, "text": c.text} for c in candidates]
    template = Path("prompts/plan-content-topics.md").read_text()
    prompt = _render_prompt(
        template,
        {
            "cards": dumps(slim_cards),
            "matches": dumps(slim_matches),
            "candidates": dumps(slim_candidates),
        },
    )
    return cast(PlanTopicsOutput, runner.run(prompt, PlanTopicsOutput))


def expand_content_job(
    runner: StructuredInferenceRunner,
    topic: TopicProposal,
    cards_by_id: dict[str, EvidenceCard],
    candidate: DiscussionCandidate | None,
) -> ContentJob:
    """一次 flash 调用：把单个主题展开成完整 ContentJob，并强制 confirmed=False。"""
    cards = [cards_by_id[cid] for cid in topic.card_ids if cid in cards_by_id]
    template = Path("prompts/expand-content-job.md").read_text()
    prompt = _render_prompt(
        template,
        {
            "topic": dumps(topic.model_dump(mode="json")),
            "cards": dumps([c.model_dump(mode="json") for c in cards]),
            "candidate": (
                dumps(candidate.model_dump(mode="json")) if candidate else "null"
            ),
        },
    )
    job = cast(ContentJob, runner.run(prompt, ContentJob))
    if job.author_position is not None:
        job.author_position = job.author_position.model_copy(update={"confirmed": False})
    return job
