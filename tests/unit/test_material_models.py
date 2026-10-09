"""Unit tests for materials domain models (deterministic ids + status)."""

from __future__ import annotations

from finch.materials.models import (
    SyncOperation,
    SyncOperationStatus,
    append_material_operation_id_for,
    discussion_id_for,
    material_id_for,
    source_hash_for,
)


def test_material_id_is_deterministic():
    assert material_id_for("blk-123") == material_id_for("blk-123")
    assert material_id_for("blk-123") != material_id_for("blk-124")
    assert material_id_for("blk-123").startswith("mat_")


def test_source_hash_is_stable_and_content_addressed():
    assert source_hash_for("原文") == source_hash_for("原文")
    assert source_hash_for("原文") != source_hash_for("改动后")


def test_append_material_operation_id_has_no_timestamp():
    # 幂等键不含时间，重试必须命中同一 operation_id。
    a = append_material_operation_id_for("标题", "正文")
    b = append_material_operation_id_for("标题", "正文")
    assert a == b
    assert append_material_operation_id_for("标题", "") != a


def test_discussion_id_binds_block_and_version():
    d1 = discussion_id_for("blk-1", "hash-a")
    d2 = discussion_id_for("blk-1", "hash-b")
    assert d1 != d2
    assert d1.startswith("disc_")


def test_sync_operation_defaults_to_pending():
    op = SyncOperation(operation_id="op-1", type="append_material", payload={})
    assert op.status == SyncOperationStatus.PENDING
    assert op.attempts == 0
    assert op.target_block is None
