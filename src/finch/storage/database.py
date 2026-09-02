"""SQLite 持久化：运行记录与节点记录（spec 6/7）。"""

from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Field, Session, SQLModel, create_engine, select


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RunRecord(SQLModel, table=True):
    id: str = Field(primary_key=True)
    state: str
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class NodeRecord(SQLModel, table=True):
    id: str = Field(primary_key=True)
    run_id: str = Field(index=True)
    node_name: str
    idempotency_key: str
    status: str
    output_json: str
    error_code: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class Store:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_path}")

    def init(self) -> None:
        # Import repositories module to register EvidenceCardRecord before create_all
        from finch.storage import repositories as _

        SQLModel.metadata.create_all(self.engine)

    def upsert_run(self, record: RunRecord) -> None:
        with Session(self.engine) as session:
            session.merge(record)
            session.commit()

    def get_run(self, run_id: str) -> RunRecord | None:
        with Session(self.engine) as session:
            return session.get(RunRecord, run_id)

    def upsert_node(self, record: NodeRecord) -> None:
        """插入或更新节点记录（恢复/重放时同键覆盖，避免主键冲突）。"""
        with Session(self.engine) as session:
            session.merge(record)
            session.commit()

    def find_node(self, run_id: str, node_name: str, idempotency_key: str) -> NodeRecord | None:
        with Session(self.engine) as session:
            stmt = select(NodeRecord).where(
                NodeRecord.run_id == run_id,
                NodeRecord.node_name == node_name,
                NodeRecord.idempotency_key == idempotency_key,
            )
            return session.exec(stmt).first()

    def list_nodes(self, run_id: str) -> list[NodeRecord]:
        with Session(self.engine) as session:
            stmt = select(NodeRecord).where(NodeRecord.run_id == run_id)
            return list(session.exec(stmt))
