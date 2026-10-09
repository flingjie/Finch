"""Unit tests for the write queue state machine + idempotency recovery."""

from __future__ import annotations

from _notion_fake import FakeNotionClient

from finch.materials.field_map import (
    blocks_for_create,
    blocks_for_discussion,
    paragraph_block,
    properties_for_create,
)
from finch.materials.models import (
    DiscussionRecord,
    SyncOperation,
    SyncOperationStatus,
    append_operation_id_for,
)
from finch.materials.queue import WriteQueue
from finch.materials.repository import SyncOperationLog
from finch.notion.client import NotionError
from finch.storage.workspace import Workspace


def _queue(tmp_path, client, *, max_attempts=3):
    return WriteQueue(
        SyncOperationLog(Workspace(tmp_path)), client, "db-1", max_attempts=max_attempts
    )


def _create_op(operation_id="op-1", title="标题", body="正文"):
    props = properties_for_create(title, [], [], operation_id)
    return SyncOperation(
        operation_id=operation_id,
        type="create_material",
        payload={"properties": props, "children": blocks_for_create(body, None)},
    )


def test_create_success(tmp_path):
    client = FakeNotionClient()
    queue = _queue(tmp_path, client)
    queue.enqueue(_create_op())
    result = queue.drain()
    assert result.succeeded == 1
    op = queue.log.get("op-1")
    assert op.status == SyncOperationStatus.SUCCEEDED
    assert op.target_page == "page-1"


def test_create_retryable_then_blocked_on_max_attempts(tmp_path):
    client = FakeNotionClient()
    client.raise_on_create = NotionError("timeout", status_code=None)
    queue = _queue(tmp_path, client, max_attempts=2)
    queue.enqueue(_create_op())
    first = queue.drain()
    assert first.retryable_failed == 1
    assert queue.log.get("op-1").status == SyncOperationStatus.RETRYABLE_FAILED
    second = queue.drain()
    assert second.blocked == 1
    assert queue.log.get("op-1").status == SyncOperationStatus.BLOCKED


def test_create_dedup_recovers_without_repost(tmp_path):
    client = FakeNotionClient()
    # 模拟：第一次 POST 已落地但响应丢失（页已存在）。
    op_id = "op-1"
    props = properties_for_create("标题", [], [], op_id)
    client.pages["page-existing"] = {
        "id": "page-existing",
        "url": "https://www.notion.so/page-existing",
        "properties": props,
        "last_edited_time": "2026-10-09T00:00:00.000Z",
    }
    client.raise_on_create = NotionError("timeout", status_code=None)
    queue = _queue(tmp_path, client)
    queue.enqueue(_create_op(op_id))
    queue.drain()  # 第一次：超时 → retryable_failed
    client.raise_on_create = None
    queue.drain()  # 第二次：先查 → 命中已有页 → succeeded，不重 POST
    op = queue.log.get(op_id)
    assert op.status == SyncOperationStatus.SUCCEEDED
    assert op.target_page == "page-existing"
    assert len(client.created) == 0  # 没有再次 create


def test_create_ambiguous_matches_blocked(tmp_path):
    client = FakeNotionClient()
    client.pages["page-a"] = {"id": "page-a", "url": "u-a", "properties": {}}
    client.pages["page-b"] = {"id": "page-b", "url": "u-b", "properties": {}}
    client.raise_on_create = NotionError("timeout", status_code=None)
    queue = _queue(tmp_path, client)
    queue.enqueue(_create_op())
    queue.drain()  # attempts=1 → 超时
    client.raise_on_create = None
    queue.drain()  # attempts=2 → query 命中 2 页 → blocked
    assert queue.log.get("op-1").status == SyncOperationStatus.BLOCKED


def _append_op(discussion_id, record):
    return SyncOperation(
        operation_id=append_operation_id_for(discussion_id),
        type="append_discussion",
        target_page="pg-1",
        payload={"children": blocks_for_discussion(discussion_id, record)},
    )


def _record(discussion_id="disc-1"):
    return DiscussionRecord(
        discussion_id=discussion_id, notion_page_id="pg-1", source_hash="h",
        user_judgment="判断A", ai_proposals=["提议B"], open_questions=["问题C"],
    )


def test_append_when_marker_absent_appends_full(tmp_path):
    client = FakeNotionClient()
    client.blocks["pg-1"] = []
    queue = _queue(tmp_path, client)
    record = _record()
    queue.enqueue(_append_op("disc-1", record))
    queue.drain()
    assert client.appended == [("pg-1", blocks_for_discussion("disc-1", record))]
    assert queue.log.get(append_operation_id_for("disc-1")).status == SyncOperationStatus.SUCCEEDED


def test_append_partial_completes_only_missing_tail(tmp_path):
    client = FakeNotionClient()
    record = _record()
    children = blocks_for_discussion("disc-1", record)
    client.blocks["pg-1"] = children[:2]  # 只有标记 + 第一个内容块
    queue = _queue(tmp_path, client)
    queue.enqueue(_append_op("disc-1", record))
    queue.drain()
    # 只补缺失的尾部（children[2:]）。
    assert client.appended == [("pg-1", children[2:])]


def test_append_already_complete_is_noop(tmp_path):
    client = FakeNotionClient()
    record = _record()
    children = blocks_for_discussion("disc-1", record)
    client.blocks["pg-1"] = list(children)  # 已完整
    queue = _queue(tmp_path, client)
    queue.enqueue(_append_op("disc-1", record))
    queue.drain()
    assert client.appended == []


def test_append_user_edited_content_not_overwritten(tmp_path):
    client = FakeNotionClient()
    record = _record()
    children = blocks_for_discussion("disc-1", record)
    client.blocks["pg-1"] = [children[0], paragraph_block("用户改过的内容")]
    queue = _queue(tmp_path, client)
    queue.enqueue(_append_op("disc-1", record))
    queue.drain()
    assert client.appended == []  # 不覆盖、不重复追加
