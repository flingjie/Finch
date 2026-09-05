"""Content Job 模型（Spec §8）：定义内容目标与作者立场。"""

import re
from enum import StrEnum
from json import dumps
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field

from finch.content.models import DraftKind
from finch.evidence.models import ClaimConfidence, EvidenceCard, MatchResult
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


class DeferredJob(BaseModel):
    """未当选 primary 的 ContentJob 及其「not now」延期理由。

    延期 ≠ 删除、≠ 永久 do_not_write：``job`` 保留原内容（含 author_position），
    仅附带一个可读的延期理由，供下游 brief 展示「NOT NOW」并保留恢复能力。
    """

    job: ContentJob
    reason: str


_STRONG_CONFIDENCE = {ClaimConfidence.VERIFIED, ClaimConfidence.SUPPORTED}

# 与 _substantive_key 返回的前五维一一对应（不含稳定序 tie-break）。
_DEFER_LABELS = (
    "position not confirmed or not ready",
    "no external discussion context",
    "lower verified/supported evidence ratio",
    "incomplete decision/tradeoff",
    "missing why-now",
)


def _evidence_ratio(job: ContentJob, cards_by_id: dict[str, EvidenceCard]) -> float:
    """source cards 中 VERIFIED/SUPPORTED 的占比（无卡记 0.0）。

    仅用于内部确定性排序，绝不作为主观小数评分对外展示。
    """
    cards = [cards_by_id[cid] for cid in job.source_card_ids if cid in cards_by_id]
    if not cards:
        return 0.0
    strong = sum(1 for card in cards if card.confidence in _STRONG_CONFIDENCE)
    return strong / len(cards)


def _substantive_key(
    job: ContentJob, cards_by_id: dict[str, EvidenceCard]
) -> tuple[bool, bool, float, bool, bool]:
    """§2.3 排序主键的前五维（不含「原始稳定顺序」tie-break）。"""
    position = job.author_position
    confirmed = position is not None and position.confirmed
    has_decision_tradeoff = (
        position is not None
        and bool(position.decision)
        and bool(position.tradeoff)
    )
    return (
        job.status == ContentJobStatus.READY and confirmed,
        job.candidate_id is not None,
        _evidence_ratio(job, cards_by_id),
        has_decision_tradeoff,
        bool(job.why_now),
    )


def _defer_reason(
    job: ContentJob, primary: ContentJob, cards_by_id: dict[str, EvidenceCard]
) -> str:
    """延期理由：按排序顺序找到第一个弱于 primary 的维度，只输出可读文案。"""
    job_key = _substantive_key(job, cards_by_id)
    primary_key = _substantive_key(primary, cards_by_id)
    for job_value, primary_value, label in zip(
        job_key, primary_key, _DEFER_LABELS, strict=True
    ):
        if job_value != primary_value:
            return label
    return "same priority as primary (tie-break by stable order)"


def select_primary_job(
    jobs: list[ContentJob],
    cards_by_id: dict[str, EvidenceCard] | None = None,
) -> tuple[ContentJob | None, list[DeferredJob]]:
    """确定性选出单个 primary job（纯函数，无 LLM 节点，无小数主观评分）。

    排序顺序（计划 §2.3）：
    1. ``status`` 为 READY 且 ``author_position.confirmed``；
    2. 有 candidate / 外部讨论上下文（``candidate_id`` 非空）；
    3. source cards 中 VERIFIED/SUPPORTED 占比更高；
    4. ``decision`` 与 ``tradeoff`` 均非空；
    5. ``why_now`` 非空；
    6. 原始稳定顺序（输入顺序）作为 tie-break。

    ``cards_by_id`` 用于第 3 维证据占比；缺省时该维对所有 job 持平，仍按其余
    维度确定性排序。返回 ``(primary, deferred)``；未选中的 job 只记录为
    :class:`DeferredJob`（not now），不删除、不永久 do_not_write。调用方应只
    传入非 ``DO_NOT_WRITE`` 的 job（被拒 job 由调用方单独剔除）。
    """
    if not jobs:
        return None, []
    cards = cards_by_id or {}
    primary_index = max(
        range(len(jobs)),
        key=lambda i: (*_substantive_key(jobs[i], cards), -i),
    )
    primary = jobs[primary_index]
    deferred = [
        DeferredJob(job=jobs[i], reason=_defer_reason(jobs[i], primary, cards))
        for i in range(len(jobs))
        if i != primary_index
    ]
    return primary, deferred


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
