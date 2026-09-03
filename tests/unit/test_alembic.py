"""Alembic 迁移测试（Task 7）：`alembic upgrade head` 在全新 SQLite 上建齐所有表。

选择方案：通过 alembic 的 Python API（``Config`` + ``command.upgrade``）在临时 SQLite 文件上
执行 ``upgrade head``，再断言所有表（含两张新表）存在。这比仅断言迁移文件存在/可导入更强，
因为同时验证了迁移链真实可执行。
"""

import importlib.util
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parents[2]

ALL_TABLES = {
    "runrecord",
    "noderecord",
    "evidencecardrecord",
    "draftrecord",
    "reviewrecord",
    "reviewhistoryrecord",
    "feedbackrecord",
    "contentjobrecord",
    "draftversionrecord",
    "criticreportrecord",
}


def _alembic_config(db_path: Path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.attributes["db_url"] = f"sqlite:///{db_path}"
    return cfg


def test_alembic_upgrade_head_creates_all_tables(tmp_path):
    db_path = tmp_path / "migrated.db"
    command.upgrade(_alembic_config(db_path), "head")

    engine = create_engine(f"sqlite:///{db_path}")
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert ALL_TABLES <= tables


def test_alembic_migrations_are_importable():
    versions_dir = ROOT / "alembic" / "versions"
    migration_files = sorted(versions_dir.glob("*.py"))
    assert len(migration_files) >= 2

    for path in migration_files:
        spec = importlib.util.spec_from_file_location(f"finch_migration_{path.stem}", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert callable(module.upgrade)
        assert callable(module.downgrade)
        assert module.revision
