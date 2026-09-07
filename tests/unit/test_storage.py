# tests/unit/test_storage.py
from finch.storage.database import Store


def test_store_enables_wal_and_normal_synchronous(tmp_path):
    store = Store(tmp_path / "test.db")
    store.init()
    with store.engine.connect() as conn:
        journal = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
        synchronous = conn.exec_driver_sql("PRAGMA synchronous").scalar()
    assert journal == "wal"
    assert synchronous == 1
