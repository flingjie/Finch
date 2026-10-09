"""Unit tests for materials repositories (YAML roundtrip + JSONL latest-line-wins)."""

from __future__ import annotations

from finch.materials.models import (
    MaterialSnapshot,
    MaterialUseLink,
    SyncOperation,
    SyncOperationStatus,
)
from finch.materials.repository import (
    MaterialRepository,
    MaterialUseLinkRepository,
    SyncOperationLog,
)
from finch.storage.workspace import Workspace


def _snapshot(block_id: str, title: str = "标题", source_hash: str = "h") -> MaterialSnapshot:
    return MaterialSnapshot(
        block_id=block_id, page_id="pg-1", page_url="https://www.notion.so/pg-1",
        title=title, source_hash=source_hash,
    )


def test_snapshot_roundtrip_and_list(tmp_path):
    repo = MaterialRepository(Workspace(tmp_path))
    repo.save_snapshot(_snapshot("blk-1", "A"))
    repo.save_snapshot(_snapshot("blk-2", "B"))
    assert repo.get_snapshot("blk-1").title == "A"
    assert len(repo.list_snapshots()) == 2


def test_snapshot_overwrite_on_resync(tmp_path):
    repo = MaterialRepository(Workspace(tmp_path))
    repo.save_snapshot(_snapshot("blk-1", "旧标题"))
    repo.save_snapshot(_snapshot("blk-1", "新标题", source_hash="h2"))
    assert repo.get_snapshot("blk-1").title == "新标题"
    assert len(repo.list_snapshots()) == 1


def test_sync_operation_log_latest_line_wins(tmp_path):
    log = SyncOperationLog(Workspace(tmp_path))
    log.append(
        SyncOperation(
            operation_id="op-1", type="append_material", status=SyncOperationStatus.PENDING
        )
    )
    log.append(
        SyncOperation(
            operation_id="op-1", type="append_material",
            status=SyncOperationStatus.SUCCEEDED, target_block="pg-1",
        )
    )
    log.append(SyncOperation(operation_id="op-2", type="append_material"))
    assert log.get("op-1").status == SyncOperationStatus.SUCCEEDED
    assert log.get("op-1").target_block == "pg-1"
    assert len(log.list_all()) == 2
    assert [o.operation_id for o in log.list_pending()] == ["op-2"]


def test_usage_log_filters_by_block(tmp_path):
    repo = MaterialUseLinkRepository(Workspace(tmp_path))
    repo.append(MaterialUseLink(block_id="blk-1", page_id="pg-1", source_hash="h", usage="read"))
    repo.append(
        MaterialUseLink(
            block_id="blk-1", page_id="pg-1", source_hash="h", usage="promoted",
            candidate_ref="idea_x",
        )
    )
    repo.append(MaterialUseLink(block_id="blk-2", page_id="pg-1", source_hash="h", usage="read"))
    links = repo.list_for_block("blk-1")
    assert [link.usage for link in links] == ["read", "promoted"]
