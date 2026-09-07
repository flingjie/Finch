"""SQLite 持久化：Store（engine + schema 初始化）。"""

from pathlib import Path

from sqlalchemy import event
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
