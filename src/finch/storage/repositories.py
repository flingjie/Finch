"""Evidence Card 仓储（spec 7.1/7.2）."""

from datetime import UTC, datetime
from typing import cast

from finch.evidence.models import EvidenceCard
from finch.storage.database import Store
from sqlmodel import Field, Session, SQLModel, select


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
