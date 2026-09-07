"""Unit tests for Store schema drift cleanup (prune_orphan_tables)."""

from sqlalchemy import inspect

from finch.storage.database import Store


def test_prune_orphan_tables_drops_unknown_table(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()

    # 手动造一张不在 SQLModel.metadata 里的表，模拟模型类删除后的 schema 漂移。
    with store.engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE orphan_table (id INTEGER PRIMARY KEY)")

    dropped = store.prune_orphan_tables()
    assert "orphan_table" in dropped
    assert "orphan_table" not in set(inspect(store.engine).get_table_names())


def test_prune_orphan_tables_keeps_known_tables(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()

    dropped = store.prune_orphan_tables()
    assert dropped == []
    # 已知表（如 ContentJobRecord 的 contentjobrecord）仍在。
    names = set(inspect(store.engine).get_table_names())
    assert "contentjobrecord" in names


def test_prune_orphan_tables_is_idempotent(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()

    with store.engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE orphan_table (id INTEGER PRIMARY KEY)")

    assert "orphan_table" in store.prune_orphan_tables()
    assert store.prune_orphan_tables() == []
