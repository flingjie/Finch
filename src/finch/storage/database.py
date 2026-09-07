"""SQLite 持久化：Store（engine + schema 初始化）。"""

from pathlib import Path

from sqlalchemy import event, inspect
from sqlmodel import SQLModel, create_engine


def _enable_wal(engine) -> None:
    """在每条连接上启用 WAL + synchronous=NORMAL（synchronous 是连接级 PRAGMA）。

    WAL 落库一次即可，重复设置无害；synchronous 必须在 connect 钩子里逐连接设置。
    """

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ANN001 - SQLAlchemy 回调签名
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


class Store:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        _enable_wal(self.engine)

    def init(self) -> None:
        # Import repositories module to register all Record models (incl. ContentJobRecord
        # 的 idea 候选投影列 origin/generation_key) before create_all.
        from finch.storage import repositories as _  # noqa: F401

        SQLModel.metadata.create_all(self.engine)

    def prune_orphan_tables(self) -> list[str]:
        """删除数据库里已不在 SQLModel.metadata 中的表（schema 漂移清理）。

        模型类删除后 ``create_all`` 不再创建它们，但旧库仍留着这些孤儿表。
        返回被删除的表名列表（按名排序）；无孤儿时返回空列表。
        """
        from finch.storage import repositories as _  # noqa: F401

        existing = set(inspect(self.engine).get_table_names())
        expected = set(SQLModel.metadata.tables)
        orphans = sorted(existing - expected)
        if not orphans:
            return []
        with self.engine.begin() as conn:
            for name in orphans:
                conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{name}"')
        return orphans

    def prune_legacy_content_jobs(self) -> list[str]:
        """删除 contentjobrecord 中 payload 无法解析为 ContentJob 的旧行（如 job_tp*）。

        幂等：解析失败的行被删后再次调用返回空列表。返回被删除的 id（按名排序）。
        """
        from pydantic import ValidationError
        from sqlmodel import Session, select

        from finch.content.jobs import ContentJob
        from finch.storage import repositories as _  # noqa: F401
        from finch.storage.repositories import ContentJobRecord

        with Session(self.engine) as session:
            records = list(session.exec(select(ContentJobRecord)))
            stale = []
            for record in records:
                try:
                    ContentJob.model_validate_json(record.payload_json)
                except ValidationError:
                    stale.append(record)
            for record in stale:
                session.delete(record)
            session.commit()
        return sorted(record.id for record in stale)
