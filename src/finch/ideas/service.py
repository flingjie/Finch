"""IdeaService：idea 候选流的幂等键 + 状态转换（Skill 架构 Step 1 领域核心）。

把 Skill 层产出的统一 ``IdeaCandidate`` 契约持久化为 ``ContentJob``，并驱动状态机。
本模块是纯领域服务：只依赖注入的 ``ContentJobRepository``，不直接访问 DB、
不调用 LLM、不做自动重试。

状态机（与 ``ContentJobStatus`` 一致）：

- ``PROPOSED → CONFIRMED → DRAFTED``
- ``PROPOSED → SKIPPED``（也允许 ``CONFIRMED → SKIPPED``）
- ``revise_position`` 只改立场、不改状态（PROPOSED/CONFIRMED 均合法）。

幂等：``generation_key`` 由 (skill, version, 规范化来源, 输入指纹) 的 sha256 决定，
同一候选重复创建命中同一 key 时返回已存在的 job。
"""

import hashlib

from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
)
from finch.content.models import DraftKind
from finch.ideas.models import IdeaCandidate
from finch.storage.repositories import ContentJobRepository

# 稳定分隔符：ASCII unit separator，字段值几乎不可能包含该控制字符。
_SEP = "\x1f"

_FORMAT_MAP = {
    "original": DraftKind.ORIGINAL,
    "reply": DraftKind.REPLY,
    "thread": DraftKind.REPLY,
}


def idea_generation_key(
    skill: str,
    skill_version: str,
    canonical_source_refs: str,
    input_fingerprint: str,
) -> str:
    """由生成器元数据 + 规范化来源 + 输入指纹确定幂等键（sha256 hex digest）。

    同一 (skill, version, 来源集合, 核心主张) 的候选重复产出时命中同一 key。
    """
    raw = _SEP.join([skill, skill_version, canonical_source_refs, input_fingerprint])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _canonical_source_refs(candidate: IdeaCandidate) -> str:
    """规范化来源：排序后的 ``type:ref`` 逗号连接，保证同一来源集合稳定。"""
    return ",".join(sorted(f"{s.type}:{s.ref}" for s in candidate.source_refs))


def _input_fingerprint(candidate: IdeaCandidate) -> str:
    """输入指纹：核心主张的 sha256。"""
    return hashlib.sha256(candidate.core_point.encode("utf-8")).hexdigest()


class IdeaService:
    """idea 候选流领域服务：持久化候选并驱动状态转换。"""

    def __init__(self, jobs: ContentJobRepository) -> None:
        self.jobs = jobs

    def create_candidate(self, idea: IdeaCandidate) -> ContentJob:
        """按幂等键创建候选：命中已有 job 直接返回，否则落库为 PROPOSED。"""
        fingerprint = _input_fingerprint(idea)
        key = idea_generation_key(
            idea.generator.skill,
            idea.generator.version,
            _canonical_source_refs(idea),
            fingerprint,
        )
        existing = self.jobs.find_by_generation_key(key)
        if existing is not None:
            return existing
        job = ContentJob(
            id=f"idea_{fingerprint[:8]}",
            source_card_ids=[],
            reader_problem=idea.reader_problem,
            author_position=idea.author_position,
            recommended_format=_FORMAT_MAP[idea.recommended_format],
            status=ContentJobStatus.PROPOSED,
            core_message=idea.core_point,
            why_now=idea.why_worth_saying,
            origin=idea.origin,
            generation_key=key,
            generator_name=idea.generator.skill,
            generator_version=idea.generator.version,
            content_fingerprint=fingerprint,
        )
        self.jobs.upsert_job(job)
        return job

    def confirm_position(self, idea_id: str) -> ContentJob:
        """PROPOSED → CONFIRMED。"""
        job = self._get_job(idea_id)
        if job.status != ContentJobStatus.PROPOSED:
            raise ValueError(
                f"illegal transition: {job.status.value} -> confirmed for idea {idea_id}; "
                "only proposed -> confirmed is legal"
            )
        job = job.model_copy(update={"status": ContentJobStatus.CONFIRMED})
        self.jobs.upsert_job(job)
        return job

    def revise_position(self, idea_id: str, position: AuthorPosition) -> ContentJob:
        """更新立场（job 的 AuthorPosition），不改状态。"""
        job = self._get_job(idea_id)
        if job.status not in (ContentJobStatus.PROPOSED, ContentJobStatus.CONFIRMED):
            raise ValueError(
                f"illegal transition: cannot revise position of idea {idea_id} "
                f"in status {job.status.value}; revise is legal from proposed/confirmed"
            )
        job = job.model_copy(update={"author_position": position})
        self.jobs.upsert_job(job)
        return job

    def require_confirmed(self, idea_id: str) -> ContentJob:
        """CONFIRMED 才返回，否则抛 ValueError（消息含 needs_confirmation 与 idea_id）。"""
        job = self.jobs.get_job(idea_id)
        if job is None or job.status != ContentJobStatus.CONFIRMED:
            status = job.status.value if job is not None else "missing"
            raise ValueError(f"idea {idea_id} needs_confirmation (status={status})")
        return job

    def mark_drafted(self, idea_id: str) -> ContentJob:
        """CONFIRMED → DRAFTED。"""
        job = self._get_job(idea_id)
        if job.status != ContentJobStatus.CONFIRMED:
            raise ValueError(
                f"illegal transition: {job.status.value} -> drafted for idea {idea_id}; "
                "only confirmed -> drafted is legal"
            )
        job = job.model_copy(update={"status": ContentJobStatus.DRAFTED})
        self.jobs.upsert_job(job)
        return job

    def skip(self, idea_id: str, reason: str) -> ContentJob:
        """→ SKIPPED，记 reject_reason；合法来源为 PROPOSED/CONFIRMED。"""
        job = self._get_job(idea_id)
        if job.status not in (ContentJobStatus.PROPOSED, ContentJobStatus.CONFIRMED):
            raise ValueError(
                f"illegal transition: {job.status.value} -> skipped for idea {idea_id}; "
                "skip is legal from proposed/confirmed"
            )
        job = job.model_copy(
            update={"status": ContentJobStatus.SKIPPED, "reject_reason": reason}
        )
        self.jobs.upsert_job(job)
        return job

    def _get_job(self, idea_id: str) -> ContentJob:
        job = self.jobs.get_job(idea_id)
        if job is None:
            raise ValueError(f"idea {idea_id} not found")
        return job
