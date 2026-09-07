"""Evidence Card 仓储（spec 7.1/7.2）与草稿/审核/反馈/ContentJob 仓储（Phase 6/8）。

另含草稿版本（DraftVersionRecord）与 Critic 报告（CriticReportRecord）仓储（Task 7）。
"""

import json
from datetime import UTC, datetime

from sqlmodel import Field, Session, SQLModel, col, select

from finch.author.models import (
    AuthorAccount,
    AuthorPost,
    AuthorSyncCursor,
    PublicationIntent,
    PublicationLink,
)
from finch.content.checkers.base import CheckResult
from finch.content.jobs import ContentJob
from finch.content.models import Draft
from finch.engagement.models import (
    ConversationEvidence,
    EngagementRunStats,
    FeedbackSnapshot,
    InteractionCandidate,
    InteractionStatus,
)
from finch.evidence.models import EvidenceCard
from finch.github.models import CommitDetail, CommitSummary
from finch.inbox.models import DecisionRecord
from finch.learn.models import Feedback
from finch.storage.database import Store


class EvidenceCardRecord(SQLModel, table=True):
    """EvidenceCard 持久化模型（C5）。"""

    id: str = Field(primary_key=True)
    event_id: str = Field(index=True)
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EvidenceRepository:
    """EvidenceCard 仓储（C5）。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def upsert_card(self, card: EvidenceCard) -> None:
        """按 id merge 插入或更新 EvidenceCard（幂等）。"""
        payload_json = card.model_dump_json()
        record = EvidenceCardRecord(
            id=card.id,
            event_id=card.event_id,
            payload_json=payload_json,
            updated_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session:
            session.merge(record)
            session.commit()

    def upsert_cards(self, cards: list[EvidenceCard]) -> None:
        """按 id 批量 merge 插入或更新（单 Session、单 commit，收紧事务粒度）。"""
        if not cards:
            return
        with Session(self.store.engine) as session:
            for card in cards:
                session.merge(
                    EvidenceCardRecord(
                        id=card.id,
                        event_id=card.event_id,
                        payload_json=card.model_dump_json(),
                        updated_at=datetime.now(UTC),
                    )
                )
            session.commit()

    def get_card(self, card_id: str) -> EvidenceCard | None:
        """按 id 获取 EvidenceCard，不存在返回 None。"""
        with Session(self.store.engine) as session:
            record = session.get(EvidenceCardRecord, card_id)
            if record is None:
                return None
            return EvidenceCard.model_validate_json(record.payload_json)

    def list_cards(self) -> list[EvidenceCard]:
        """列出所有 EvidenceCard。"""
        with Session(self.store.engine) as session:
            stmt = select(EvidenceCardRecord)
            records = list(session.exec(stmt))
            return [EvidenceCard.model_validate_json(r.payload_json) for r in records]


class CommitIngestionRecord(SQLModel, table=True):
    """commit 摄取 ledger（阶段 A）：per-SHA 增量状态。"""

    repository: str = Field(primary_key=True)
    sha: str = Field(primary_key=True)
    status: str
    group_id: str | None = None
    discovered_at: datetime
    authored_at: datetime
    retry_count: int = 0
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RepoCursorRecord(SQLModel, table=True):
    """每仓库 SHA 游标（非权威优化：HEAD 等于 last_synced_sha 时跳过 list_commits）。"""

    repository: str = Field(primary_key=True)
    last_synced_sha: str | None = None
    last_synced_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CommitIngestionRepository:
    """commit 摄取 ledger 仓储：pending → grouped → extracted / skipped / failed。"""

    PENDING = "pending"
    GROUPED = "grouped"
    EXTRACTED = "extracted"
    SKIPPED = "skipped"
    FAILED = "failed"

    def __init__(self, store: Store) -> None:
        self.store = store

    def known_shas(self, repository: str) -> set[str]:
        with Session(self.store.engine) as session:
            stmt = select(CommitIngestionRecord.sha).where(
                CommitIngestionRecord.repository == repository
            )
            return set(session.exec(stmt))

    def upsert_pending(self, repository: str, summaries: list[CommitSummary]) -> None:
        if not summaries:
            return
        now = datetime.now(UTC)
        with Session(self.store.engine) as session:
            for s in summaries:
                session.merge(
                    CommitIngestionRecord(
                        repository=repository,
                        sha=s.sha,
                        status=self.PENDING,
                        discovered_at=now,
                        authored_at=s.author_date,
                        retry_count=0,
                        payload_json=s.model_dump_json(),
                        updated_at=now,
                    )
                )
            session.commit()

    def store_detail(self, repository: str, detail: CommitDetail) -> None:
        with Session(self.store.engine) as session:
            record = session.get(CommitIngestionRecord, (repository, detail.sha))
            if record is None:
                raise KeyError((repository, detail.sha))
            record.status = self.GROUPED
            record.authored_at = detail.author_date
            record.payload_json = detail.model_dump_json()
            record.updated_at = datetime.now(UTC)
            session.add(record)
            session.commit()

    def mark_skipped(self, repository: str, sha: str) -> None:
        self._set_statuses(repository, [sha], self.SKIPPED)

    def mark_extracted(self, repository: str, shas: list[str]) -> None:
        self._set_statuses(repository, shas, self.EXTRACTED)

    def mark_failed(self, repository: str, shas: list[str], max_retries: int) -> None:
        with Session(self.store.engine) as session:
            for sha in shas:
                record = session.get(CommitIngestionRecord, (repository, sha))
                if record is None:
                    continue
                record.retry_count += 1
                record.status = (
                    self.SKIPPED if record.retry_count >= max_retries else self.FAILED
                )
                record.updated_at = datetime.now(UTC)
                session.add(record)
            session.commit()

    def mark_group_ids(self, repository: str, assignments: list[tuple[str, str]]) -> None:
        """持久化 commit 的 group_id（append-only 分组，幂等：重复写同值无害）。"""
        if not assignments:
            return
        with Session(self.store.engine) as session:
            for sha, group_id in assignments:
                record = session.get(CommitIngestionRecord, (repository, sha))
                if record is None:
                    continue
                record.group_id = group_id
                record.updated_at = datetime.now(UTC)
                session.add(record)
            session.commit()

    def list_pending(self, repository: str) -> list[CommitIngestionRecord]:
        return self._list_by_status(repository, [self.PENDING])

    def list_grouped(self, repository: str) -> list[CommitIngestionRecord]:
        """返回可提取的 commit（含 failed，供下一轮重试）。"""
        return self._list_by_status(repository, [self.GROUPED, self.FAILED])

    def _list_by_status(
        self, repository: str, statuses: list[str]
    ) -> list[CommitIngestionRecord]:
        with Session(self.store.engine) as session:
            stmt = select(CommitIngestionRecord).where(
                CommitIngestionRecord.repository == repository,
                CommitIngestionRecord.status.in_(statuses),  # type: ignore[attr-defined]
            )
            return list(session.exec(stmt))

    def _set_statuses(self, repository: str, shas: list[str], status: str) -> None:
        if not shas:
            return
        with Session(self.store.engine) as session:
            for sha in shas:
                record = session.get(CommitIngestionRecord, (repository, sha))
                if record is None:
                    continue
                record.status = status
                record.updated_at = datetime.now(UTC)
                session.add(record)
            session.commit()


class RepoCursorRepository:
    """每仓库 SHA 游标仓储（非权威，仅用于零新 commit 快速返回）。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def get_sha(self, repository: str) -> str | None:
        with Session(self.store.engine) as session:
            record = session.get(RepoCursorRecord, repository)
            return record.last_synced_sha if record is not None else None

    def advance(self, repository: str, sha: str | None) -> None:
        if sha is None:
            return
        with Session(self.store.engine) as session:
            session.merge(
                RepoCursorRecord(
                    repository=repository,
                    last_synced_sha=sha,
                    last_synced_at=datetime.now(UTC),
                )
            )
            session.commit()


class DraftRecord(SQLModel, table=True):
    """Draft 持久化模型（C2）。"""

    id: str = Field(primary_key=True)  # = draft.id
    kind: str  # DraftKind.value
    candidate_id: str | None = None
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DraftRepository:
    """Draft 仓储（C2）。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def upsert_draft(self, draft: Draft) -> None:
        """按 id merge 插入或更新 Draft（幂等）。"""
        payload_json = draft.model_dump_json()
        record = DraftRecord(
            id=draft.id,
            kind=draft.kind.value,
            candidate_id=draft.candidate_id,
            payload_json=payload_json,
            updated_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session:
            session.merge(record)
            session.commit()

    def get_draft(self, draft_id: str) -> Draft | None:
        """按 id 获取 Draft，不存在返回 None。"""
        with Session(self.store.engine) as session:
            record = session.get(DraftRecord, draft_id)
            if record is None:
                return None
            return Draft.model_validate_json(record.payload_json)

    def list_drafts(self) -> list[Draft]:
        """列出所有 Draft（按 id 稳定排序，保证 next --json 的 pending[0] 确定性）。"""
        with Session(self.store.engine) as session:
            stmt = select(DraftRecord).order_by(col(DraftRecord.id))
            records = list(session.exec(stmt))
            return [Draft.model_validate_json(r.payload_json) for r in records]

    def list_by_job(self, job_id: str) -> list[Draft]:
        """按 content_job_id 列出草稿（payload_json 内字段，需全表扫描后过滤）。"""
        return [d for d in self.list_drafts() if d.content_job_id == job_id]


class DecisionRecordRecord(SQLModel, table=True):
    """DecisionRecord 持久化模型（单一决策点）。"""

    id: str = Field(primary_key=True)  # "dec_<job_id>"
    job_id: str = Field(index=True)
    draft_id: str = Field(index=True)
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DecisionRecordRepository:
    """DecisionRecord 仓储（加性，与旧 ReviewRepository 并存）。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def save(self, record: DecisionRecord) -> None:
        with Session(self.store.engine) as session:
            session.merge(
                DecisionRecordRecord(
                    id=record.id,
                    job_id=record.job_id,
                    draft_id=record.draft_id,
                    payload_json=record.model_dump_json(),
                    updated_at=datetime.now(UTC),
                )
            )
            session.commit()

    def get(self, job_id: str) -> DecisionRecord | None:
        with Session(self.store.engine) as session:
            record = session.get(DecisionRecordRecord, f"dec_{job_id}")
            if record is None:
                return None
            return DecisionRecord.model_validate_json(record.payload_json)

    def list(self) -> list[DecisionRecord]:
        with Session(self.store.engine) as session:
            records = list(session.exec(select(DecisionRecordRecord)))
            return [DecisionRecord.model_validate_json(r.payload_json) for r in records]


class FeedbackRecord(SQLModel, table=True):
    """Feedback 持久化模型（C2）。"""

    id: str = Field(primary_key=True)  # = feedback.draft_id
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FeedbackRepository:
    """Feedback 仓储（C2）。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def save_feedback(self, feedback: Feedback) -> None:
        """按 id merge 保存 Feedback（幂等）。"""
        payload_json = feedback.model_dump_json()
        record = FeedbackRecord(
            id=feedback.draft_id,
            payload_json=payload_json,
            updated_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session:
            session.merge(record)
            session.commit()

    def get_feedback(self, draft_id: str) -> Feedback | None:
        """按 draft_id 获取 Feedback，不存在返回 None。"""
        with Session(self.store.engine) as session:
            record = session.get(FeedbackRecord, draft_id)
            if record is None:
                return None
            return Feedback.model_validate_json(record.payload_json)

    def list_feedbacks(self) -> list[Feedback]:
        """列出所有 Feedback。"""
        with Session(self.store.engine) as session:
            stmt = select(FeedbackRecord)
            records = list(session.exec(stmt))
            return [Feedback.model_validate_json(r.payload_json) for r in records]


class ContentJobRecord(SQLModel, table=True):
    """ContentJob 持久化模型（C8）。"""

    id: str = Field(primary_key=True)  # = job.id
    origin: str | None = Field(default=None, index=True)
    generation_key: str | None = Field(default=None, index=True)
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ContentJobRepository:
    """ContentJob 仓储（C8）。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def upsert_job(self, job: ContentJob) -> None:
        """按 id merge 插入或更新 ContentJob（幂等）。"""
        payload_json = job.model_dump_json()
        record = ContentJobRecord(
            id=job.id,
            origin=job.origin,
            generation_key=job.generation_key,
            payload_json=payload_json,
            updated_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session:
            session.merge(record)
            session.commit()

    def upsert_jobs(self, jobs: list[ContentJob]) -> None:
        """按 id 批量 merge 插入或更新（单 Session、单 commit，收紧事务粒度）。"""
        if not jobs:
            return
        with Session(self.store.engine) as session:
            for job in jobs:
                session.merge(
                    ContentJobRecord(
                        id=job.id,
                        origin=job.origin,
                        generation_key=job.generation_key,
                        payload_json=job.model_dump_json(),
                        updated_at=datetime.now(UTC),
                    )
                )
            session.commit()

    def get_job(self, job_id: str) -> ContentJob | None:
        """按 id 获取 ContentJob，不存在返回 None。"""
        with Session(self.store.engine) as session:
            record = session.get(ContentJobRecord, job_id)
            if record is None:
                return None
            return ContentJob.model_validate_json(record.payload_json)

    def find_by_generation_key(self, generation_key: str) -> ContentJob | None:
        """按 generation_key 获取 ContentJob，不存在返回 None（幂等键查询）。"""
        with Session(self.store.engine) as session:
            stmt = select(ContentJobRecord).where(
                ContentJobRecord.generation_key == generation_key
            )
            record = session.exec(stmt).first()
            if record is None:
                return None
            return ContentJob.model_validate_json(record.payload_json)

    def list_jobs(self) -> list[ContentJob]:
        """列出所有 ContentJob（单查询，避免 N+1）。"""
        with Session(self.store.engine) as session:
            stmt = select(ContentJobRecord)
            records = list(session.exec(stmt))
            return [ContentJob.model_validate_json(r.payload_json) for r in records]


class DraftVersionRecord(SQLModel, table=True):
    """草稿每轮版本持久化模型（Task 7：C8）。"""

    id: str = Field(primary_key=True)  # f"{draft_id}:{round}"
    draft_id: str = Field(index=True)
    round: int
    payload_json: str  # Draft.model_dump_json()
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DraftVersionRepository:
    """草稿版本仓储（Task 7）：逐轮保留 Critique 前的草稿快照。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def upsert_version(self, draft_id: str, round: int, draft: Draft) -> None:
        """按 id（draft_id:round）merge 插入或更新某一轮草稿版本（幂等）。"""
        record = DraftVersionRecord(
            id=f"{draft_id}:{round}",
            draft_id=draft_id,
            round=round,
            payload_json=draft.model_dump_json(),
            updated_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session:
            session.merge(record)
            session.commit()

    def list_versions(self, draft_id: str) -> list[Draft]:
        """按 round 升序返回某草稿的全部版本（单查询，避免 N+1）。"""
        with Session(self.store.engine) as session:
            stmt = (
                select(DraftVersionRecord)
                .where(DraftVersionRecord.draft_id == draft_id)
                .order_by("round")
            )
            records = list(session.exec(stmt))
            return [Draft.model_validate_json(r.payload_json) for r in records]


class CriticReportRecord(SQLModel, table=True):
    """Critic 每轮报告持久化模型（Task 7：C8）。"""

    id: str = Field(primary_key=True)  # f"{draft_id}:{round}"
    draft_id: str = Field(index=True)
    round: int
    payload_json: str  # {"checks": [...], "outcome": "..."}
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CriticReportRepository:
    """Critic 报告仓储（Task 7）：逐轮保留检查结果与聚合去向。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def upsert_report(
        self, draft_id: str, round: int, checks: list[CheckResult], outcome: str
    ) -> None:
        """按 id（draft_id:round）merge 插入或更新某轮 Critic 报告（幂等）。"""
        payload = {
            "checks": [check.model_dump(mode="json") for check in checks],
            "outcome": outcome,
        }
        record = CriticReportRecord(
            id=f"{draft_id}:{round}",
            draft_id=draft_id,
            round=round,
            payload_json=json.dumps(payload),
            updated_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session:
            session.merge(record)
            session.commit()

    def list_reports(self, draft_id: str) -> list[dict]:
        """按 round 升序返回某草稿的全部报告（单查询，避免 N+1）。"""
        with Session(self.store.engine) as session:
            stmt = (
                select(CriticReportRecord)
                .where(CriticReportRecord.draft_id == draft_id)
                .order_by("round")
            )
            records = list(session.exec(stmt))
            return [json.loads(r.payload_json) for r in records]

    def list_all_reports(self, since: datetime | None = None) -> dict[str, list[dict]]:
        """按 draft_id 分组返回 Critic 报告（单查询，供周复盘批量统计，避免 N+1）。

        `since` 非 None 时仅返回 ``updated_at >= since`` 的报告，使周复盘指标遵守时间窗。
        """
        with Session(self.store.engine) as session:
            stmt = select(CriticReportRecord).order_by("draft_id", "round")
            if since is not None:
                stmt = stmt.where(CriticReportRecord.updated_at >= since)
            records = list(session.exec(stmt))
        grouped: dict[str, list[dict]] = {}
        for record in records:
            grouped.setdefault(record.draft_id, []).append(json.loads(record.payload_json))
        return grouped


class InteractionCandidateRecord(SQLModel, table=True):
    """InteractionCandidate 持久化模型（Phase 5 审批队列）。"""

    id: str = Field(primary_key=True)  # = candidate.id（稳定幂等键）
    run_id: str = Field(index=True)
    post_id: str = Field(index=True)
    action: str  # InteractionAction.value
    status: str  # InteractionStatus.value
    payload_json: str
    execution_outcome: str | None = None  # ExecutionStatus.value（record_execution 时写入）
    execution_detail: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class InteractionRepository:
    """互动候选审批队列仓储（Phase 5）。

    ``upsert`` 按 ``candidate.id`` merge（幂等）；``approve``/``reject``/``edit`` 通过
    ``_update`` 读回原记录、改写 payload 后按同 ``run_id`` merge，保留 run_id/post_id/action
    等索引列。``record_execution`` 以 ``candidate.id`` 为幂等键，重复调用只覆盖同一条记录，
    不会重复执行。
    """

    def __init__(self, store: Store) -> None:
        self.store = store

    @staticmethod
    def _to_record(candidate: InteractionCandidate, run_id: str) -> InteractionCandidateRecord:
        return InteractionCandidateRecord(
            id=candidate.id,
            run_id=run_id,
            post_id=candidate.post.id,
            action=candidate.action.value,
            status=candidate.status.value,
            payload_json=candidate.model_dump_json(),
            updated_at=datetime.now(UTC),
        )

    def upsert(self, candidate: InteractionCandidate, run_id: str) -> None:
        """按 id merge 插入或更新候选（幂等，可重放）。"""
        with Session(self.store.engine) as session:
            session.merge(self._to_record(candidate, run_id=run_id))
            session.commit()

    def get(self, candidate_id: str) -> InteractionCandidate | None:
        """按 id 获取候选，不存在返回 None。"""
        with Session(self.store.engine) as session:
            record = session.get(InteractionCandidateRecord, candidate_id)
            if record is None:
                return None
            return InteractionCandidate.model_validate_json(record.payload_json)

    def list_pending(self) -> list[InteractionCandidate]:
        """列出全部仍处 PROPOSED 状态的候选（未批准、未拒绝、未执行）。"""
        with Session(self.store.engine) as session:
            stmt = select(InteractionCandidateRecord).where(
                InteractionCandidateRecord.status == InteractionStatus.PROPOSED.value
            )
            records = list(session.exec(stmt))
            return [InteractionCandidate.model_validate_json(r.payload_json) for r in records]

    def list_all(self) -> list[InteractionCandidate]:
        """列出全部候选（含已批准/已拒绝/已执行，供指标聚合）。"""
        with Session(self.store.engine) as session:
            records = list(session.exec(select(InteractionCandidateRecord)))
            return [InteractionCandidate.model_validate_json(r.payload_json) for r in records]

    def list_executed(self) -> list[InteractionCandidate]:
        """列出全部已执行（EXECUTED）候选。"""
        with Session(self.store.engine) as session:
            stmt = select(InteractionCandidateRecord).where(
                InteractionCandidateRecord.status == InteractionStatus.EXECUTED.value
            )
            records = list(session.exec(stmt))
            return [InteractionCandidate.model_validate_json(r.payload_json) for r in records]

    def _update(self, candidate_id: str, **updates: object) -> None:
        """读回候选、应用 updates 后按同 run_id merge（幂等）。不存在时抛 KeyError。"""
        with Session(self.store.engine) as session:
            record = session.get(InteractionCandidateRecord, candidate_id)
            if record is None:
                raise KeyError(candidate_id)
            candidate = InteractionCandidate.model_validate_json(record.payload_json)
            candidate = candidate.model_copy(update=updates)
            session.merge(self._to_record(candidate, run_id=record.run_id))
            session.commit()

    def approve(self, candidate_id: str) -> None:
        """PROPOSED→APPROVED（幂等：重复批准仍是 APPROVED，不重复执行）。"""
        self._update(candidate_id, status=InteractionStatus.APPROVED, reject_reason=None)

    def reject(self, candidate_id: str, reason: str) -> None:
        """→ REJECTED，并记录 reject_reason（幂等）。"""
        self._update(candidate_id, status=InteractionStatus.REJECTED, reject_reason=reason)

    def edit(self, candidate_id: str, revised_draft: str) -> None:
        """保存人工修订草稿到 ``revised_draft``（不自动批准、不改变发布权限）。"""
        self._update(candidate_id, revised_draft=revised_draft)

    def record_execution(self, candidate_id: str, outcome: str, detail: str) -> None:
        """记录执行结果（``outcome`` ∈ ExecutionStatus 值）并置 status=EXECUTED。

        以 ``candidate.id`` 为幂等键：重复调用只 merge 覆盖同一条记录，不重复执行。
        真正的「未批准不可执行」由纯函数 ``evaluate_execution`` 在执行前强制，本方法
        只是被动的结果记录器。
        """
        with Session(self.store.engine) as session:
            record = session.get(InteractionCandidateRecord, candidate_id)
            if record is None:
                raise KeyError(candidate_id)
            candidate = InteractionCandidate.model_validate_json(record.payload_json)
            candidate = candidate.model_copy(update={"status": InteractionStatus.EXECUTED})
            merged = self._to_record(candidate, run_id=record.run_id)
            merged.execution_outcome = outcome
            merged.execution_detail = detail
            session.merge(merged)
            session.commit()


class FeedbackSnapshotRecord(SQLModel, table=True):
    """FeedbackSnapshot 持久化模型（Phase 6 反馈回流）。"""

    id: str = Field(primary_key=True)  # = snapshot.id（幂等键）
    interaction_id: str = Field(index=True)
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class FeedbackSnapshotRepository:
    """互动反馈快照仓储（Phase 6）。

    ``upsert`` 按 ``snapshot.id`` merge（幂等）；``get`` 按 ``interaction_id`` 查询，
    返回该互动最近一次写入的快照。
    """

    def __init__(self, store: Store) -> None:
        self.store = store

    def upsert(self, snapshot: FeedbackSnapshot) -> None:
        """按 id merge 插入或更新快照（幂等，可重放）。"""
        record = FeedbackSnapshotRecord(
            id=snapshot.id,
            interaction_id=snapshot.interaction_id,
            payload_json=snapshot.model_dump_json(),
            updated_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session:
            session.merge(record)
            session.commit()

    def get(self, interaction_id: str) -> FeedbackSnapshot | None:
        """按 interaction_id 获取快照，不存在返回 None。"""
        with Session(self.store.engine) as session:
            stmt = select(FeedbackSnapshotRecord).where(
                FeedbackSnapshotRecord.interaction_id == interaction_id
            )
            record = session.exec(stmt).first()
            if record is None:
                return None
            return FeedbackSnapshot.model_validate_json(record.payload_json)

    def list_all(self) -> list[FeedbackSnapshot]:
        """列出所有快照。"""
        with Session(self.store.engine) as session:
            stmt = select(FeedbackSnapshotRecord)
            records = list(session.exec(stmt))
            return [FeedbackSnapshot.model_validate_json(r.payload_json) for r in records]


class ConversationEvidenceRecord(SQLModel, table=True):
    """ConversationEvidence 持久化模型（Phase 6 反馈回流）。"""

    id: str = Field(primary_key=True)  # = evidence.id（幂等键）
    interaction_id: str = Field(index=True)
    post_id: str = Field(index=True)
    origin: str  # 恒为 "conversation"
    kind: str  # question / disagreement / hypothesis / experiment
    verified: bool = Field(index=True)
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConversationEvidenceRepository:
    """conversation 证据仓储（Phase 6）。

    ``upsert`` 按 ``evidence.id`` merge（幂等）；``mark_verified`` 读回记录、置
    ``verified=True`` 后按同 id merge；``list_unverified`` 只返回未验证的讨论信号，
    供升级流程筛选待验证项。
    """

    def __init__(self, store: Store) -> None:
        self.store = store

    @staticmethod
    def _to_record(evidence: ConversationEvidence) -> ConversationEvidenceRecord:
        return ConversationEvidenceRecord(
            id=evidence.id,
            interaction_id=evidence.interaction_id,
            post_id=evidence.post_id,
            origin=evidence.origin,
            kind=evidence.kind,
            verified=evidence.verified,
            payload_json=evidence.model_dump_json(),
            updated_at=datetime.now(UTC),
        )

    def upsert(self, evidence: ConversationEvidence) -> None:
        """按 id merge 插入或更新证据（幂等，可重放）。"""
        with Session(self.store.engine) as session:
            session.merge(self._to_record(evidence))
            session.commit()

    def list_unverified(self) -> list[ConversationEvidence]:
        """列出全部未验证（``verified=False``）的证据。"""
        with Session(self.store.engine) as session:
            stmt = select(ConversationEvidenceRecord).where(
                ConversationEvidenceRecord.verified == False  # noqa: E712
            )
            records = list(session.exec(stmt))
            return [ConversationEvidence.model_validate_json(r.payload_json) for r in records]

    def list_all(self) -> list[ConversationEvidence]:
        """列出全部 conversation 证据（含已验证与未验证，供指标聚合）。"""
        with Session(self.store.engine) as session:
            records = list(session.exec(select(ConversationEvidenceRecord)))
            return [ConversationEvidence.model_validate_json(r.payload_json) for r in records]

    def list_verified(self) -> list[ConversationEvidence]:
        """列出全部已验证（``verified=True``）的证据。"""
        with Session(self.store.engine) as session:
            stmt = select(ConversationEvidenceRecord).where(
                ConversationEvidenceRecord.verified == True  # noqa: E712
            )
            records = list(session.exec(stmt))
            return [ConversationEvidence.model_validate_json(r.payload_json) for r in records]

    def mark_verified(self, evidence_id: str) -> None:
        """置 ``verified=True``（幂等；不存在时抛 KeyError）。"""
        with Session(self.store.engine) as session:
            record = session.get(ConversationEvidenceRecord, evidence_id)
            if record is None:
                raise KeyError(evidence_id)
            evidence = ConversationEvidence.model_validate_json(record.payload_json)
            evidence = evidence.model_copy(update={"verified": True})
            session.merge(self._to_record(evidence))
            session.commit()

    def list_by_interaction(self, interaction_id: str) -> list[ConversationEvidence]:
        """按 interaction_id 列出全部证据。"""
        with Session(self.store.engine) as session:
            stmt = select(ConversationEvidenceRecord).where(
                ConversationEvidenceRecord.interaction_id == interaction_id
            )
            records = list(session.exec(stmt))
            return [ConversationEvidence.model_validate_json(r.payload_json) for r in records]


class EngagementRunStatsRecord(SQLModel, table=True):
    """互动轨道运行级计数持久化模型（Phase 7 可观测性）。"""

    run_id: str = Field(primary_key=True)
    posts_scanned: int = 0
    candidates: int = 0
    drafts: int = 0
    latency_ms: int = 0
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EngagementRunStatsRepository:
    """互动轨道运行级计数仓储（Phase 7）。

    ``upsert`` 按 ``run_id`` merge（幂等）；``list_all`` 返回全部运行级计数，供 CLI
    ``finch engagement metrics`` 聚合 ``no_evidence_runs`` / ``posts_scanned`` / 延迟。
    """

    def __init__(self, store: Store) -> None:
        self.store = store

    def upsert(self, stats: EngagementRunStats) -> None:
        """按 run_id merge 插入或更新运行级计数（幂等，可重放）。"""
        record = EngagementRunStatsRecord(
            run_id=stats.run_id,
            posts_scanned=stats.posts_scanned,
            candidates=stats.candidates,
            drafts=stats.drafts,
            latency_ms=stats.latency_ms,
            payload_json=stats.model_dump_json(),
            updated_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session:
            session.merge(record)
            session.commit()

    def list_all(self) -> list[EngagementRunStats]:
        """列出全部运行级计数。"""
        with Session(self.store.engine) as session:
            records = list(session.exec(select(EngagementRunStatsRecord)))
            return [EngagementRunStats.model_validate_json(r.payload_json) for r in records]


class AuthorAccountRecord(SQLModel, table=True):
    """AuthorAccount 持久化模型（P0：已验证作者账号）。"""

    id: str = Field(primary_key=True)  # f"acct:{platform}:{user_id}"
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuthorAccountRepository:
    """作者账号仓储（P0）：按 platform:user_id 主键 merge（幂等）。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def save(self, account: AuthorAccount) -> None:
        """按 ``acct:{platform}:{user_id}`` merge 插入或更新（幂等）。"""
        record = AuthorAccountRecord(
            id=f"acct:{account.platform}:{account.user_id}",
            payload_json=account.model_dump_json(),
            updated_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session:
            session.merge(record)
            session.commit()

    def get(self, platform: str, user_id: str) -> AuthorAccount | None:
        """按 platform:user_id 获取账号，不存在返回 None。"""
        with Session(self.store.engine) as session:
            record = session.get(AuthorAccountRecord, f"acct:{platform}:{user_id}")
            if record is None:
                return None
            return AuthorAccount.model_validate_json(record.payload_json)

    def list(self) -> list[AuthorAccount]:
        """列出全部已验证作者账号。"""
        with Session(self.store.engine) as session:
            records = list(session.exec(select(AuthorAccountRecord)))
            return [AuthorAccount.model_validate_json(r.payload_json) for r in records]


class AuthorPostRecord(SQLModel, table=True):
    """AuthorPost 持久化模型（P0：作者发布的一条帖子）。"""

    id: str = Field(primary_key=True)  # f"post:{platform}:{remote_post_id}"
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuthorPostRepository:
    """作者帖子仓储（P0）：按 platform:remote_post_id 主键 merge（幂等）。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def save(self, post: AuthorPost) -> None:
        """按 ``post:{platform}:{remote_post_id}`` merge 插入或更新（幂等）。"""
        record = AuthorPostRecord(
            id=f"post:{post.platform}:{post.remote_post_id}",
            payload_json=post.model_dump_json(),
            updated_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session:
            session.merge(record)
            session.commit()

    def get(self, platform: str, remote_post_id: str) -> AuthorPost | None:
        """按 platform:remote_post_id 获取帖子，不存在返回 None。"""
        with Session(self.store.engine) as session:
            record = session.get(AuthorPostRecord, f"post:{platform}:{remote_post_id}")
            if record is None:
                return None
            return AuthorPost.model_validate_json(record.payload_json)

    def list(self) -> list[AuthorPost]:
        """列出全部作者帖子。"""
        with Session(self.store.engine) as session:
            records = list(session.exec(select(AuthorPostRecord)))
            return [AuthorPost.model_validate_json(r.payload_json) for r in records]


class PublicationIntentRecord(SQLModel, table=True):
    """PublicationIntent 持久化模型（P0：批准时保存的期待发布记录）。"""

    id: str = Field(primary_key=True)  # f"intent:{source_id}"
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PublicationIntentRepository:
    """发布意图仓储（P0）：按 source_id 主键 merge（幂等）。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def save(self, intent: PublicationIntent) -> None:
        """按 ``intent:{source_id}`` merge 插入或更新（幂等）。"""
        record = PublicationIntentRecord(
            id=f"intent:{intent.source_id}",
            payload_json=intent.model_dump_json(),
            updated_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session:
            session.merge(record)
            session.commit()

    def get(self, source_id: str) -> PublicationIntent | None:
        """按 source_id 获取发布意图，不存在返回 None。"""
        with Session(self.store.engine) as session:
            record = session.get(PublicationIntentRecord, f"intent:{source_id}")
            if record is None:
                return None
            return PublicationIntent.model_validate_json(record.payload_json)

    def list(self) -> list[PublicationIntent]:
        """列出全部发布意图。"""
        with Session(self.store.engine) as session:
            records = list(session.exec(select(PublicationIntentRecord)))
            return [PublicationIntent.model_validate_json(r.payload_json) for r in records]


class PublicationLinkRecord(SQLModel, table=True):
    """PublicationLink 持久化模型（P0：确定性匹配结果）。"""

    id: str = Field(primary_key=True)  # f"link:{source_id}"
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PublicationLinkRepository:
    """发布关联仓储（P0）：按 source_id 主键 merge（幂等）。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def save(self, link: PublicationLink) -> None:
        """按 ``link:{source_id}`` merge 插入或更新（幂等）。"""
        record = PublicationLinkRecord(
            id=f"link:{link.source_id}",
            payload_json=link.model_dump_json(),
            updated_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session:
            session.merge(record)
            session.commit()

    def get(self, source_id: str) -> PublicationLink | None:
        """按 source_id 获取匹配结果，不存在返回 None。"""
        with Session(self.store.engine) as session:
            record = session.get(PublicationLinkRecord, f"link:{source_id}")
            if record is None:
                return None
            return PublicationLink.model_validate_json(record.payload_json)

    def list(self) -> list[PublicationLink]:
        """列出全部匹配结果。"""
        with Session(self.store.engine) as session:
            records = list(session.exec(select(PublicationLinkRecord)))
            return [PublicationLink.model_validate_json(r.payload_json) for r in records]


class AuthorSyncCursorRecord(SQLModel, table=True):
    """AuthorSyncCursor 持久化模型（P0：同步水位）。"""

    id: str = Field(primary_key=True)  # f"cursor:{platform}:{author_account_id}"
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AuthorSyncCursorRepository:
    """同步游标仓储（P0）：按 platform:author_account_id 主键 merge（幂等）。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def save(self, cursor: AuthorSyncCursor) -> None:
        """按 ``cursor:{platform}:{author_account_id}`` merge 插入或更新（幂等）。"""
        record = AuthorSyncCursorRecord(
            id=f"cursor:{cursor.platform}:{cursor.author_account_id}",
            payload_json=cursor.model_dump_json(),
            updated_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session:
            session.merge(record)
            session.commit()

    def get(self, platform: str, author_account_id: str) -> AuthorSyncCursor | None:
        """按 platform:author_account_id 获取游标，不存在返回 None。"""
        with Session(self.store.engine) as session:
            record = session.get(
                AuthorSyncCursorRecord, f"cursor:{platform}:{author_account_id}"
            )
            if record is None:
                return None
            return AuthorSyncCursor.model_validate_json(record.payload_json)

    def list(self) -> list[AuthorSyncCursor]:
        """列出全部同步游标。"""
        with Session(self.store.engine) as session:
            records = list(session.exec(select(AuthorSyncCursorRecord)))
            return [AuthorSyncCursor.model_validate_json(r.payload_json) for r in records]
