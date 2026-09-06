"""单一决策点服务：accept/skip 把「确认立场 + 批准草稿」合并为一次原子落地（加性）。"""

import hashlib
from datetime import UTC, datetime

from finch.codex.runner import CodexRunner
from finch.content.critic import critique
from finch.content.jobs import ContentJobStatus, PositionSource, position_fingerprint
from finch.content.writer import rewrite_with_instruction
from finch.review.models import (
    DecisionAction,
    DecisionRecord,
    ReviewAction,
    ReviewDecision,
)
from finch.review.service import compute_diff
from finch.settings import QualityGates
from finch.storage.repositories import (
    ContentJobRepository,
    DecisionRecordRepository,
    DraftRepository,
    PositionApprovalRepository,
    ReviewRepository,
)


def content_hash(body: str) -> str:
    """返回正文的确定性 SHA-256 摘要（批准绑定具体文本版本）。"""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class DecisionService:
    """把一次「采用/跳过」落到 job/approval/review/decision 上（权威 = DecisionRecord）。

    多仓写入不是单一 DB 事务，但依赖稳定 id（``rev_<draft_id>``、``dec_<job_id>``、
    fingerprint 合并的 ``PositionApproval``）幂等，部分写入可通过重跑命令恢复。
    """

    def __init__(
        self,
        *,
        jobs: ContentJobRepository,
        drafts: DraftRepository,
        approvals: PositionApprovalRepository,
        reviews: ReviewRepository,
        decisions: DecisionRecordRepository,
    ) -> None:
        self.jobs = jobs
        self.drafts = drafts
        self.approvals = approvals
        self.reviews = reviews
        self.decisions = decisions

    def _require_job_and_draft(self, job_id: str) -> tuple:
        job = self.jobs.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        drafts = self.drafts.list_by_job(job_id)
        if not drafts:
            raise KeyError(f"no draft for job {job_id}")
        return job, drafts[0]

    def accept(self, job_id: str) -> DecisionRecord:
        job, draft = self._require_job_and_draft(job_id)
        position = job.author_position
        if position is None or not position.claim or not position.decision or not position.tradeoff:
            raise ValueError(f"position incomplete for job {job_id}")
        confirmed = position.model_copy(
            update={"confirmed": True, "position_source": PositionSource.HUMAN_CONFIRMED}
        )
        # 向后兼容投影 1：ContentJob.author_position
        self.jobs.upsert_job(job.model_copy(update={"author_position": confirmed}))
        # 向后兼容投影 2：PositionApproval（fingerprint 复用）
        self.approvals.approve(position_fingerprint(confirmed), job_id)
        # 向后兼容投影 3：ReviewDecision(APPROVE)（周复盘/voice 读旧记录）
        self.reviews.save_review(
            ReviewDecision(
                id=f"rev_{draft.id}", draft_id=draft.id,
                action=ReviewAction.APPROVE, decided_at=datetime.now(UTC),
            )
        )
        # 权威记录
        record = DecisionRecord(
            id=f"dec_{job_id}", job_id=job_id, draft_id=draft.id,
            action=DecisionAction.ACCEPT,
            position_source=PositionSource.HUMAN_CONFIRMED,
            position_fingerprint=position_fingerprint(confirmed),
            approved_content_hash=content_hash(draft.body),
            decided_at=datetime.now(UTC),
        )
        self.decisions.save(record)
        return record

    def revise(
        self,
        job_id: str,
        instruction: str,
        *,
        runner: CodexRunner,
        cards_by_id: dict,
        gates: QualityGates | None,
    ) -> dict:
        """按 NL 指令重写 + 重跑 Critic + 持久化修订正文，返回 {new_body, diff, critic}。

        ``revise`` 不写 ACCEPT 记录；它把修订正文落库（``upsert_draft``），
        使后续 ``accept`` 的 ``approved_content_hash`` 绑定到最新正文。
        """
        job, draft = self._require_job_and_draft(job_id)
        new_draft = rewrite_with_instruction(runner, draft, instruction, cards_by_id, job)
        critic = critique(runner, new_draft, cards_by_id)
        diff = compute_diff(draft.body, new_draft.body)

        self.drafts.upsert_draft(new_draft)
        self.reviews.save_review(
            ReviewDecision(
                id=f"rev_{draft.id}", draft_id=draft.id,
                action=ReviewAction.REVISE, revised_body=new_draft.body, diff=diff,
                decided_at=datetime.now(UTC),
            )
        )
        fingerprint = (
            position_fingerprint(job.author_position) if job.author_position else ""
        )
        self.decisions.save(
            DecisionRecord(
                id=f"dec_{job_id}", job_id=job_id, draft_id=draft.id,
                action=DecisionAction.REVISE,
                position_source=(
                    job.author_position.position_source
                    if job.author_position and job.author_position.position_source
                    else PositionSource.INFERRED
                ),
                position_fingerprint=fingerprint,
                approved_content_hash=content_hash(new_draft.body),
                revised_body=new_draft.body, diff=diff,
                decided_at=datetime.now(UTC),
            )
        )
        return {
            "new_body": new_draft.body,
            "diff": diff,
            "critic": critic.model_dump(mode="json"),
        }

    def skip(self, job_id: str, reason: str) -> DecisionRecord:
        job, draft = self._require_job_and_draft(job_id)
        self.jobs.upsert_job(
            job.model_copy(
                update={"status": ContentJobStatus.DO_NOT_WRITE, "reject_reason": reason}
            )
        )
        if job.author_position is not None:
            self.approvals.revoke(position_fingerprint(job.author_position))
        # 向后兼容投影：ReviewDecision(SKIP)（周复盘读旧 ReviewAction.SKIP 记录）
        self.reviews.save_review(
            ReviewDecision(
                id=f"rev_{draft.id}",
                draft_id=draft.id,
                action=ReviewAction.SKIP,
                reason=reason,
                decided_at=datetime.now(UTC),
            )
        )
        record = DecisionRecord(
            id=f"dec_{job_id}", job_id=job_id, draft_id=draft.id,
            action=DecisionAction.SKIP,
            position_source=PositionSource.INFERRED,
            position_fingerprint=(
                position_fingerprint(job.author_position) if job.author_position else ""
            ),
            approved_content_hash="",
            decided_at=datetime.now(UTC),
        )
        self.decisions.save(record)
        return record
