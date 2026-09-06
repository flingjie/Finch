"""收件箱服务：投影 + 确定性选择 + 双轨决策。

不依赖 finch.cli，避免循环导入。
"""

import difflib
import hashlib
from datetime import UTC, datetime
from typing import Literal

from finch.author.models import PublicationIntent
from finch.codex.runner import CodexRunner
from finch.content.critic import critique
from finch.content.jobs import ContentJob, ContentJobStatus, PositionSource, position_fingerprint
from finch.content.models import Draft
from finch.content.writer import rewrite_with_instruction
from finch.engagement.models import InteractionAction, InteractionCandidate
from finch.evidence.models import ClaimConfidence, EvidenceCard
from finch.inbox.models import DecisionAction, DecisionRecord, InboxItem, InboxTrack
from finch.storage.repositories import (
    ContentJobRepository,
    DecisionRecordRepository,
    DraftRepository,
    EvidenceRepository,
    InteractionRepository,
    PublicationIntentRepository,
)

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


def content_hash(body: str) -> str:
    """正文的确定性 SHA-256 摘要（批准绑定具体文本版本）。"""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _diff(before: str, after: str) -> str:
    return "\n".join(
        difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="")
    )


class InboxDecisionService:
    """把一次「采用/跳过/修订」落到唯一权威记录（DecisionRecord）或互动候选状态。

    original 走 DecisionRecord + PublicationIntent；engagement 走
    InteractionCandidate.status。先查 ContentJob，再查 InteractionCandidate
    （冲突 id 以 ContentJob 为准）。
    """

    def __init__(
        self,
        *,
        jobs: ContentJobRepository,
        drafts: DraftRepository,
        decisions: DecisionRecordRepository,
        publication_intents: PublicationIntentRepository,
        interactions: InteractionRepository,
    ) -> None:
        self.jobs = jobs
        self.drafts = drafts
        self.decisions = decisions
        self.publication_intents = publication_intents
        self.interactions = interactions

    def accept(self, item_id: str) -> DecisionRecord | InteractionCandidate:
        job = self.jobs.get_job(item_id)
        if job is not None:
            return self._accept_original(item_id)
        candidate = self.interactions.get(item_id)
        if candidate is not None:
            self.interactions.approve(item_id)
            return self.interactions.get(item_id)  # type: ignore[return-value]
        raise KeyError(item_id)

    def skip(self, item_id: str, reason: str) -> DecisionRecord | InteractionCandidate:
        job = self.jobs.get_job(item_id)
        if job is not None:
            return self._skip_original(item_id, reason)
        candidate = self.interactions.get(item_id)
        if candidate is not None:
            self.interactions.reject(item_id, reason)
            return self.interactions.get(item_id)  # type: ignore[return-value]
        raise KeyError(item_id)

    def revise(
        self,
        item_id: str,
        instruction: str,
        *,
        runner: CodexRunner,
        cards_by_id: dict,
    ) -> dict:
        job = self.jobs.get_job(item_id)
        if job is not None:
            return self._revise_original(
                item_id, instruction, runner=runner, cards_by_id=cards_by_id
            )
        candidate = self.interactions.get(item_id)
        if candidate is not None:
            raise ValueError(f"engagement revise not supported yet: {item_id}")
        raise KeyError(item_id)

    def _accept_original(self, job_id: str) -> DecisionRecord:
        job = self.jobs.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        drafts = self.drafts.list_by_job(job_id)
        if not drafts:
            raise KeyError(f"no draft for job {job_id}")
        draft = drafts[0]
        position = job.author_position
        fingerprint = position_fingerprint(position) if position is not None else ""
        record = DecisionRecord(
            id=f"dec_{job_id}",
            job_id=job_id,
            draft_id=draft.id,
            action=DecisionAction.ACCEPT,
            position_source=PositionSource.HUMAN_CONFIRMED,
            position_fingerprint=fingerprint,
            approved_content_hash=content_hash(draft.body),
            decided_at=datetime.now(UTC),
        )
        self.publication_intents.save(
            PublicationIntent(
                source_type="draft",
                source_id=draft.id,
                approved_body=draft.body,
                content_hash=content_hash(draft.body),
                approved_at=datetime.now(UTC),
                expected_kind="original",
            )
        )
        self.decisions.save(record)
        return record

    def _skip_original(self, job_id: str, reason: str) -> DecisionRecord:
        job = self.jobs.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        drafts = self.drafts.list_by_job(job_id)
        if not drafts:
            raise KeyError(f"no draft for job {job_id}")
        draft = drafts[0]
        self.jobs.upsert_job(
            job.model_copy(
                update={"status": ContentJobStatus.DO_NOT_WRITE, "reject_reason": reason}
            )
        )
        position = job.author_position
        fingerprint = position_fingerprint(position) if position is not None else ""
        record = DecisionRecord(
            id=f"dec_{job_id}",
            job_id=job_id,
            draft_id=draft.id,
            action=DecisionAction.SKIP,
            position_source=PositionSource.INFERRED,
            position_fingerprint=fingerprint,
            approved_content_hash="",
            decided_at=datetime.now(UTC),
        )
        self.decisions.save(record)
        return record

    def _revise_original(
        self,
        job_id: str,
        instruction: str,
        *,
        runner: CodexRunner,
        cards_by_id: dict,
    ) -> dict:
        job = self.jobs.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        drafts = self.drafts.list_by_job(job_id)
        if not drafts:
            raise KeyError(f"no draft for job {job_id}")
        draft = drafts[0]
        new_draft = rewrite_with_instruction(runner, draft, instruction, cards_by_id, job)
        critic = critique(runner, new_draft, cards_by_id)
        self.drafts.upsert_draft(new_draft)
        position = job.author_position
        fingerprint = position_fingerprint(position) if position is not None else ""
        self.decisions.save(
            DecisionRecord(
                id=f"dec_{job_id}",
                job_id=job_id,
                draft_id=draft.id,
                action=DecisionAction.REVISE,
                position_source=(
                    position.position_source
                    if position is not None and position.position_source
                    else PositionSource.INFERRED
                ),
                position_fingerprint=fingerprint,
                approved_content_hash=content_hash(new_draft.body),
                revised_body=new_draft.body,
                diff=_diff(draft.body, new_draft.body),
                decided_at=datetime.now(UTC),
            )
        )
        return {
            "new_body": new_draft.body,
            "diff": _diff(draft.body, new_draft.body),
            "critic": critic.model_dump(mode="json"),
        }


def _original_ask(job: ContentJob) -> tuple[bool, list[str]]:
    """立场不完整 → must_ask（Graph 不停，问题放到卡上）。"""
    position = job.author_position
    if position is None or not position.decision or not position.tradeoff:
        return True, ["position_incomplete"]
    return False, []


def next_item(
    *,
    jobs: ContentJobRepository,
    drafts: DraftRepository,
    decisions: DecisionRecordRepository,
    interactions: InteractionRepository,
    cards: EvidenceRepository,
) -> dict:
    """组装收件箱并返回第一条待决策卡（JSON 载荷），空则 {"status": "none"}。"""
    decided_job_ids = {
        r.job_id
        for r in decisions.list()
        if r.action in {DecisionAction.ACCEPT, DecisionAction.SKIP}
    }
    cards_by_id = {c.id: c for c in cards.list_cards()}
    items: list[InboxItem] = []
    for draft in drafts.list_drafts():
        if not draft.content_job_id or draft.content_job_id in decided_job_ids:
            continue
        job = jobs.get_job(draft.content_job_id)
        if job is None:
            continue
        must_ask, ask_reasons = _original_ask(job)
        items.append(
            build_original_item(
                job, draft, cards_by_id=cards_by_id,
                must_ask=must_ask, ask_reasons=ask_reasons, risks=[],
            )
        )
    for candidate in interactions.list_pending():
        if candidate.draft or candidate.revised_draft:
            items.append(build_engagement_item(candidate))

    first = select_next(items)
    if first is None:
        return {"status": "none"}
    payload = first.model_dump(mode="json")
    payload["status"] = "review_required"
    if first.track == InboxTrack.ORIGINAL:
        payload["job_id"] = first.id  # 兼容旧客户端
        payload["topic"] = first.position.get("decision") if first.position else first.id
    return payload
