"""Evidence Card 仓储（spec 7.1/7.2）与草稿/审核/反馈/ContentJob 仓储（Phase 6/8）。"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlmodel import Field, Session, SQLModel, select

from finch.content.jobs import ContentJob
from finch.content.models import Draft
from finch.evidence.models import EvidenceCard
from finch.review.models import Feedback, ReviewDecision
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
        """列出所有 Draft。"""
        with Session(self.store.engine) as session:
            stmt = select(DraftRecord)
            records = list(session.exec(stmt))
            return [Draft.model_validate_json(r.payload_json) for r in records]


class ReviewRecord(SQLModel, table=True):
    """ReviewDecision 持久化模型（C2）。"""

    id: str = Field(primary_key=True)  # = decision.id（"rev_<draft_id>"）
    draft_id: str = Field(index=True)
    payload_json: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReviewRepository:
    """ReviewDecision 仓储（C2）。"""

    def __init__(self, store: Store) -> None:
        self.store = store

    def save_review(self, decision: ReviewDecision) -> None:
        """按 id merge 保存 ReviewDecision（幂等，可重放）。"""
        payload_json = decision.model_dump_json()
        record = ReviewRecord(
            id=decision.id,
            draft_id=decision.draft_id,
            payload_json=payload_json,
            updated_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session:
            session.merge(record)
            session.commit()

    def get_review(self, draft_id: str) -> ReviewDecision | None:
        """按 draft_id 获取 ReviewDecision，不存在返回 None。"""
        with Session(self.store.engine) as session:
            stmt = select(ReviewRecord).where(ReviewRecord.draft_id == draft_id)
            record = session.exec(stmt).first()
            if record is None:
                return None
            return ReviewDecision.model_validate_json(record.payload_json)

    def list_reviews(self) -> list[ReviewDecision]:
        """列出所有 ReviewDecision。"""
        with Session(self.store.engine) as session:
            stmt = select(ReviewRecord)
            records = list(session.exec(stmt))
            return [ReviewDecision.model_validate_json(r.payload_json) for r in records]

    def append_history(self, decision: ReviewDecision) -> None:
        """追加一条审核历史（不覆盖，供周复盘统计修改次数）。"""
        record = ReviewHistoryRecord(
            id=f"revhist_{uuid4().hex}",
            draft_id=decision.draft_id,
            payload_json=decision.model_dump_json(),
            created_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session:
            session.add(record)
            session.commit()

    def list_history(self) -> list[ReviewDecision]:
        """列出全部审核历史事件。"""
        with Session(self.store.engine) as session:
            stmt = select(ReviewHistoryRecord)
            records = list(session.exec(stmt))
            return [ReviewDecision.model_validate_json(r.payload_json) for r in records]


class ReviewHistoryRecord(SQLModel, table=True):
    """ReviewDecision 追加式历史模型（Phase 9）。"""

    id: str = Field(primary_key=True)  # 唯一事件 id
    draft_id: str = Field(index=True)
    payload_json: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
            payload_json=payload_json,
            updated_at=datetime.now(UTC),
        )
        with Session(self.store.engine) as session:
            session.merge(record)
            session.commit()

    def get_job(self, job_id: str) -> ContentJob | None:
        """按 id 获取 ContentJob，不存在返回 None。"""
        with Session(self.store.engine) as session:
            record = session.get(ContentJobRecord, job_id)
            if record is None:
                return None
            return ContentJob.model_validate_json(record.payload_json)

    def list_jobs(self) -> list[ContentJob]:
        """列出所有 ContentJob（单查询，避免 N+1）。"""
        with Session(self.store.engine) as session:
            stmt = select(ContentJobRecord)
            records = list(session.exec(stmt))
            return [ContentJob.model_validate_json(r.payload_json) for r in records]
