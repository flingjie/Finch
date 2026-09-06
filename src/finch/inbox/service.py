"""收件箱服务：投影 + 确定性选择（决策服务在 Task 3 追加）。

纯函数、不访问 DB（next_item 只读仓储调用方传入的结果）。不依赖 finch.cli，避免循环导入。
"""

from typing import Literal

from finch.content.jobs import ContentJob
from finch.content.models import Draft
from finch.engagement.models import InteractionAction, InteractionCandidate
from finch.evidence.models import ClaimConfidence, EvidenceCard
from finch.inbox.models import InboxItem, InboxTrack

_STRONG = {ClaimConfidence.VERIFIED, ClaimConfidence.SUPPORTED}


def original_score(job: ContentJob, cards_by_id: dict[str, EvidenceCard]) -> float:
    """原创轨道的确定性排序分（0–1，仅同轨可比，跨轨分数不可比）。

    只反映「是否值得先写」的稳定维度，不依赖 confirmed/position_source
    （Phase 2 将删除立场确认机制，本函数保持不变）。
    """
    cards = [cards_by_id[cid] for cid in job.source_card_ids if cid in cards_by_id]
    strong = sum(1 for c in cards if c.confidence in _STRONG) / len(cards) if cards else 0.0
    position = job.author_position
    return (
        0.5 * (1.0 if job.candidate_id is not None else 0.0)
        + 0.25 * strong
        + 0.15 * (1.0 if position is not None and position.decision and position.tradeoff else 0.0)
        + 0.10 * (1.0 if job.why_now else 0.0)
    )


def _provenance(job: ContentJob) -> Literal["personal", "idea"]:
    return "idea" if job.id.startswith("idea_") else "personal"


def _content_type(job: ContentJob) -> Literal["original", "reply"]:
    return "reply" if job.candidate_id is not None else "original"


def build_original_item(
    job: ContentJob,
    draft: Draft,
    *,
    cards_by_id: dict[str, EvidenceCard],
    must_ask: bool,
    ask_reasons: list[str],
    risks: list[str],
) -> InboxItem:
    """把 ContentJob + Draft 投影成 InboxItem（original/idea 轨道）。"""
    source_refs = [
        src.url
        for cid in job.source_card_ids
        if cid in cards_by_id
        for src in cards_by_id[cid].sources
    ]
    position = None
    if job.author_position is not None:
        position = {
            "claim": job.author_position.claim,
            "decision": job.author_position.decision,
            "tradeoff": job.author_position.tradeoff,
        }
    return InboxItem(
        id=job.id,
        track=InboxTrack.ORIGINAL,
        content_type=_content_type(job),
        provenance=_provenance(job),
        source_refs=source_refs,
        why_now=job.why_now,
        score=original_score(job, cards_by_id),
        draft_id=draft.id,
        draft=draft.body,
        position=position,
        must_ask=must_ask,
        ask_reasons=ask_reasons,
        risks=risks,
    )


def build_engagement_item(candidate: InteractionCandidate) -> InboxItem:
    """把 InteractionCandidate 投影成 InboxItem（engagement 轨道）。"""
    content_type: Literal["reply", "quote"] = (
        "quote" if candidate.action == InteractionAction.DRAFT_QUOTE else "reply"
    )
    body = candidate.revised_draft or candidate.draft or ""
    return InboxItem(
        id=candidate.id,
        track=InboxTrack.ENGAGEMENT,
        content_type=content_type,
        provenance="external",
        source_refs=[candidate.post.url],
        why_now="；".join(candidate.score.reasons),
        score=candidate.score.total,
        draft_id=None,
        draft=body,
        position=None,
        must_ask=bool(candidate.factual_risks),
        ask_reasons=list(candidate.factual_risks),
        risks=list(candidate.factual_risks),
    )


def select_next(items: list[InboxItem]) -> InboxItem | None:
    """§7.2 确定性选择：must_ask 优先 → original 先于 engagement → score 降序、id 升序。"""
    if not items:
        return None
    rank = {InboxTrack.ORIGINAL: 0, InboxTrack.ENGAGEMENT: 1}
    return sorted(
        items,
        key=lambda it: (not it.must_ask, rank[it.track], -it.score, it.id),
    )[0]
