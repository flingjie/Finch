"""单一决策点服务：accept/skip 把「确认立场 + 批准草稿」合并为一次原子落地（加性）。"""

import hashlib
from datetime import UTC, datetime

from finch.content.jobs import ContentJobStatus, PositionSource, position_fingerprint
from finch.review.models import (
    DecisionAction,
    DecisionRecord,
    ReviewAction,
    ReviewDecision,
)
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
    """把一次「采用/跳过」落到 job/approval/review/decision 上（权威 = DecisionRecord）。"""

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

    def skip(self, job_id: str, reason: str) -> DecisionRecord:
        job, draft = self._require_job_and_draft(job_id)
        self.jobs.upsert_job(
            job.model_copy(
                update={"status": ContentJobStatus.DO_NOT_WRITE, "reject_reason": reason}
            )
        )
        if job.author_position is not None:
            self.approvals.revoke(position_fingerprint(job.author_position))
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
