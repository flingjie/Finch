"""IdeaService：idea 候选流的幂等键 + 状态转换（Skill 架构 Step 1 领域核心）。

把 Skill 层产出的统一 ``IdeaCandidate`` 契约持久化为 ``ContentJob``，并驱动状态机。
本模块是纯领域服务：只依赖注入的 ``ContentJobRepository``，不直接访问 DB、
不调用 LLM、不做自动重试。

状态机（与 ``ContentJobStatus`` 一致）：

- ``PROPOSED → CONFIRMED``
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
from finch.content.models import RecommendedFormat
from finch.ideas.models import (
    FactBundle,
    IdeaAngle,
    IdeaBoundaries,
    IdeaCandidate,
    IdeaGenerator,
)
from finch.storage.repositories import ContentJobRepository

# 稳定分隔符：ASCII unit separator，字段值几乎不可能包含该控制字符。
_SEP = "\x1f"


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
        # 兜底：generation_key 因 generator.skill 改名等变化时（同 core_point 的 id 不变），
        # 按内容指纹（id）命中已有 job，避免重新落库覆盖其 confirmed/drafted/revised 状态。
        existing_by_id = self.jobs.get_job(f"idea_{fingerprint[:8]}")
        if existing_by_id is not None:
            return existing_by_id
        job = ContentJob(
            id=f"idea_{fingerprint[:8]}",
            source_card_ids=[],
            reader_problem=idea.reader_problem,
            author_position=idea.author_position,
            recommended_format=idea.recommended_format,
            status=ContentJobStatus.PROPOSED,
            core_message=idea.core_point,
            why_now=idea.why_worth_saying,
            origin=idea.origin,
            observation=idea.observation,
            intent=idea.intent,
            open_question=idea.open_question,
            communication_goal=idea.communication_goal,
            generation_key=key,
            generator_name=idea.generator.skill,
            generator_version=idea.generator.version,
            content_fingerprint=fingerprint,
            source_kind=idea.source_kind,
            facts=list(idea.facts),
            interpretation=idea.interpretation,
            evidence_status=idea.evidence_status,
            limitations=idea.limitations,
            method_id=idea.method_id,
            method_use_as=idea.method_use_as,
            method_fit_reason=idea.method_fit_reason,
            method_version_hash=idea.method_version_hash,
            content_type=idea.content_type,
            attempt_id=idea.attempt_id,
            problem_id=idea.problem_id,
        )
        self.jobs.upsert_job(job)
        return job

    def create_from_angle(
        self,
        angle: IdeaAngle,
        *,
        bundle: FactBundle,
        generator: IdeaGenerator,
        method_version_hash: str = "",
    ) -> ContentJob:
        """把一个发散角度映射为 IdeaCandidate 并幂等落库（复用 create_candidate）。"""
        candidate = IdeaCandidate(
            id=f"idea_{hashlib.sha256(angle.core_point.encode('utf-8')).hexdigest()[:8]}",
            origin=bundle.origin,
            core_point=angle.core_point,
            observation="\n".join(bundle.facts),
            reader_problem=angle.reader_situation,
            why_worth_saying=angle.reader_takeaway,
            intent="stance",
            author_position=AuthorPosition(
                claim=angle.core_point,
                decision=angle.reader_takeaway,
                tradeoff=angle.counterexample_or_limit,
            ),
            source_refs=list(bundle.source_refs),
            boundaries=self._boundaries_for(angle, bundle),
            recommended_format=RecommendedFormat.SHORT_POST,
            generator=generator,
            source_kind=bundle.source_kind,
            facts=list(bundle.facts),
            interpretation=angle.core_point,
            evidence_status=bundle.evidence_status,
            limitations=angle.counterexample_or_limit,
            reader_situation=angle.reader_situation,
            reader_takeaway=angle.reader_takeaway,
            takeaway_kind=angle.takeaway_kind,
            evidence_support=angle.evidence_support,
            counterexample_or_limit=angle.counterexample_or_limit,
            method_id=angle.method_id,
            method_use_as=angle.use_as,
            method_fit_reason=angle.fit_reason,
            method_version_hash=method_version_hash,
        )
        return self.create_candidate(candidate)

    def _boundaries_for(self, angle: IdeaAngle, bundle: FactBundle) -> IdeaBoundaries:
        """事实按 bundle 已有置信度归类；角度主张按 evidence_support 归类。"""
        known = list(bundle.boundaries.known)
        inferred = list(bundle.boundaries.inferred)
        unknown = list(bundle.boundaries.unknown)
        claim_bucket = {
            "observed_this_run": "known",
            "inferred_cause": "inferred",
            "unverified_general": "unknown",
        }[angle.evidence_support]
        bucket = {"known": known, "inferred": inferred, "unknown": unknown}[claim_bucket]
        if angle.core_point not in bucket:
            bucket.append(angle.core_point)
        return IdeaBoundaries(known=known, inferred=inferred, unknown=unknown)

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

    def revise_position(
        self,
        idea_id: str,
        position: AuthorPosition,
        *,
        change_reason: str = "",
        assumptions: list[str] | None = None,
        counterexample: str = "",
        scope: str = "",
        source_refs: list[str] | None = None,
        confirmed_by_user: bool = False,
    ) -> ContentJob:
        """更新立场并追加修订历史（不改状态；DRAFTED ≠ 观点已证实）。"""
        from datetime import UTC, datetime

        from finch.content.jobs import PositionRevision

        job = self._get_job(idea_id)
        if job.status not in (ContentJobStatus.PROPOSED, ContentJobStatus.CONFIRMED):
            raise ValueError(
                f"illegal transition: cannot revise position of idea {idea_id} "
                f"in status {job.status.value}; revise is legal from proposed/confirmed"
            )
        revision = PositionRevision(
            claim=position.claim,
            assumptions=assumptions or [],
            counterexample=counterexample,
            scope=scope,
            source_refs=source_refs or [],
            change_reason=change_reason,
            confirmed_by_user=confirmed_by_user,
            created_at=datetime.now(UTC),
            decision=position.decision,
            tradeoff=position.tradeoff,
        )
        revisions = [*job.position_revisions, revision]
        job = job.model_copy(
            update={"author_position": position, "position_revisions": revisions}
        )
        self.jobs.upsert_job(job)
        return job

    def require_confirmed(self, idea_id: str) -> ContentJob:
        """CONFIRMED 才返回，否则抛 ValueError（消息含 needs_confirmation 与 idea_id）。"""
        job = self.jobs.get_job(idea_id)
        if job is None or job.status != ContentJobStatus.CONFIRMED:
            status = job.status.value if job is not None else "missing"
            raise ValueError(f"idea {idea_id} needs_confirmation (status={status})")
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
