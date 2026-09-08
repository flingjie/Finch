"""Unit tests for Store schema drift cleanup (prune_orphan_tables)."""

from sqlalchemy import inspect
from sqlmodel import Session

from finch.content.jobs import ContentJob, ContentJobStatus
from finch.content.models import RecommendedFormat
from finch.storage.database import Store
from finch.storage.repositories import ContentJobRecord, ContentJobRepository


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


def test_prune_legacy_content_jobs_deletes_only_unparseable_rows(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    repo = ContentJobRepository(store)
    repo.upsert_job(
        ContentJob(
            id="job_ok",
            source_card_ids=[],
            reader_problem="p",
            author_position=None,
            recommended_format=RecommendedFormat.REPLY,
            status=ContentJobStatus.PROPOSED,
            core_message="m",
        )
    )
    with Session(store.engine) as session:
        session.merge(
            ContentJobRecord(id="job_tp1", payload_json='{"id":"job_tp1","status":"ready"}')
        )
        session.commit()

    assert store.prune_legacy_content_jobs() == ["job_tp1"]
    # 幂等：清理后再次调用无剩余。
    assert store.prune_legacy_content_jobs() == []
    assert [j.id for j in repo.list_jobs()] == ["job_ok"]
