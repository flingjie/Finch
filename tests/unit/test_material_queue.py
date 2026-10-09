"""Unit tests for the write queue state machine + idempotency recovery."""

from __future__ import annotations

from _notion_fake import FakeNotionClient

from finch.materials.field_map import (
    blocks_for_discussion,
    material_toggle_block,
    paragraph_block,
)
from finch.materials.models import (
    DiscussionRecord,
    SyncOperation,
    SyncOperationStatus,
    append_discussion_operation_id_for,
    append_material_operation_id_for,
)
from finch.materials.queue import WriteQueue
from finch.materials.repository import SyncOperationLog
from finch.notion.client import NotionError
from finch.storage.workspace import Workspace


def _queue(tmp_path, client, *, max_attempts=3):
    return WriteQueue(
        SyncOperationLog(Workspace(tmp_path)), client, "pg-1", max_attempts=max_attempts
    )


def _append_material_op(title="标题", body="正文"):
    op_id = append_material_operation_id_for(title, body)
    return SyncOperation(
        operation_id=op_id,
        type="append_material",
        target_block="pg-1",
        payload={"children": [material_toggle_block(title, body, None)]},
    )


def _append_discussion_op(discussion_id="disc-1", toggle_id="blk-t", record=None):
    record = record or DiscussionRecord(
        discussion_id=discussion_id, block_id=toggle_id, page_id="pg-1", source_hash="h",
        user_judgment="判断A", ai_proposals=["提议B"], open_questions=["问题C"],
    )
    return SyncOperation(
        operation_id=append_discussion_operation_id_for(discussion_id),
        type="append_discussion",
        target_block=toggle_id,
        payload={"children": blocks_for_discussion(discussion_id, record)},
    )


def test_append_material_success(tmp_path):
    client = FakeNotionClient()
    client.set_page("pg-1")
    queue = _queue(tmp_path, client)
    queue.enqueue(_append_material_op())
    result = queue.drain()
    assert result.succeeded == 1
    op = queue.log.get(append_material_operation_id_for("标题", "正文"))
    assert op.status == SyncOperationStatus.SUCCEEDED
    assert (op.remote_result or {}).get("block_id")
    assert len(client.blocks["pg-1"]) == 1


def test_append_material_retryable_then_blocked(tmp_path):
    client = FakeNotionClient()
    client.set_page("pg-1")
    client.raise_on_append = NotionError("timeout", status_code=None)
    queue = _queue(tmp_path, client, max_attempts=2)
    queue.enqueue(_append_material_op())
    first = queue.drain()
    assert first.retryable_failed == 1
    second = queue.drain()
    assert second.blocked == 1


def test_append_material_dedup_recovers_without_reappend(tmp_path):
    client = FakeNotionClient()
    client.set_page("pg-1")
    # 模拟：第一次 append 已落地但响应丢失（toggle 已在页面里）。
    toggle = material_toggle_block("标题", "正文", None)
    toggle["id"] = "existing-blk"
    client.seed_toggle("pg-1", toggle, "existing-blk")
    client.raise_on_append = NotionError("timeout", status_code=None)
    queue = _queue(tmp_path, client)
    queue.enqueue(_append_material_op())
    queue.drain()  # 第一次：超时 → retryable_failed
    client.raise_on_append = None
    queue.drain()  # 第二次：按标题去重命中 → succeeded，不重复追加
    op = queue.log.get(append_material_operation_id_for("标题", "正文"))
    assert op.status == SyncOperationStatus.SUCCEEDED
    assert (op.remote_result or {}).get("already_appended") is True
    assert len(client.blocks["pg-1"]) == 1  # 没有重复追加


def test_append_discussion_when_marker_absent_appends_full(tmp_path):
    client = FakeNotionClient()
    client.blocks["blk-t"] = []
    queue = _queue(tmp_path, client)
    record = DiscussionRecord(
        discussion_id="disc-1", block_id="blk-t", page_id="pg-1", source_hash="h",
        user_judgment="判断A", ai_proposals=["提议B"],
    )
    children = blocks_for_discussion("disc-1", record)
    queue.enqueue(_append_discussion_op("disc-1", "blk-t", record))
    queue.drain()
    assert client.appended == [("blk-t", children)]


def test_append_discussion_partial_completes_missing_tail(tmp_path):
    client = FakeNotionClient()
    record = DiscussionRecord(
        discussion_id="disc-1", block_id="blk-t", page_id="pg-1", source_hash="h",
        user_judgment="判断A", ai_proposals=["提议B"], open_questions=["问题C"],
    )
    children = blocks_for_discussion("disc-1", record)
    client.blocks["blk-t"] = children[:2]  # 标记 + 判断
    queue = _queue(tmp_path, client)
    queue.enqueue(_append_discussion_op("disc-1", "blk-t", record))
    queue.drain()
    assert client.appended == [("blk-t", children[2:])]


def test_append_discussion_already_complete_is_noop(tmp_path):
    client = FakeNotionClient()
    record = DiscussionRecord(
        discussion_id="disc-1", block_id="blk-t", page_id="pg-1", source_hash="h",
        user_judgment="判断A", ai_proposals=["提议B"],
    )
    children = blocks_for_discussion("disc-1", record)
    client.blocks["blk-t"] = list(children)
    queue = _queue(tmp_path, client)
    queue.enqueue(_append_discussion_op("disc-1", "blk-t", record))
    queue.drain()
    assert client.appended == []


def test_append_discussion_user_edited_not_overwritten(tmp_path):
    client = FakeNotionClient()
    record = DiscussionRecord(
        discussion_id="disc-1", block_id="blk-t", page_id="pg-1", source_hash="h",
        user_judgment="判断A", ai_proposals=["提议B"],
    )
    children = blocks_for_discussion("disc-1", record)
    client.blocks["blk-t"] = [children[0], paragraph_block("用户改过的内容")]
    queue = _queue(tmp_path, client)
    queue.enqueue(_append_discussion_op("disc-1", "blk-t", record))
    queue.drain()
    assert client.appended == []
