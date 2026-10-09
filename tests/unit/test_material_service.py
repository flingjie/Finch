"""Unit tests for MaterialService (capture / read / sync / record_discussion)."""

from __future__ import annotations

from _notion_fake import FakeNotionClient

from finch.materials.field_map import blocks_for_create
from finch.materials.models import SyncOperationStatus
from finch.materials.service import MaterialService
from finch.storage.workspace import Workspace


def _seed_page(
    fake: FakeNotionClient, page_id: str, title: str, body: str, reflection=None
) -> None:
    props = {
        "标题": {"type": "title", "title": [{"plain_text": title}]},
        "主题标签": {"type": "multi_select", "multi_select": []},
        "已讨论": {"type": "checkbox", "checkbox": False},
    }
    fake.pages[page_id] = {
        "id": page_id,
        "url": f"https://www.notion.so/{page_id}",
        "properties": props,
        "last_edited_time": "2026-10-09T00:00:00.000Z",
    }
    fake.blocks[page_id] = blocks_for_create(body, reflection)


def _service(tmp_path, fake):
    return MaterialService(Workspace(tmp_path), fake, "db-1")


def test_capture_enqueues_create_op(tmp_path):
    fake = FakeNotionClient()
    service = _service(tmp_path, fake)
    op = service.capture(title="标题", body_text="正文")
    assert op.type == "create_material"
    assert op.status == SyncOperationStatus.PENDING
    assert op.payload["properties"]["标题"]["title"][0]["text"]["content"] == "标题"
    # 幂等：相同内容再 capture 命中同一 operation_id。
    again = service.capture(title="标题", body_text="正文")
    assert again.operation_id == op.operation_id


def test_read_caches_and_records_usage(tmp_path):
    fake = FakeNotionClient()
    _seed_page(fake, "pg-1", "标题A", "发生了什么")
    service = _service(tmp_path, fake)
    snapshot = service.read("pg-1")
    assert snapshot.title == "标题A"
    assert snapshot.extractable_text == "发生了什么"
    assert service.repo.get_snapshot("pg-1") is not None
    assert [link.usage for link in service.usage.list_for_page("pg-1")] == ["read"]


def test_sync_full_pulls_and_advances_boundary(tmp_path):
    fake = FakeNotionClient()
    _seed_page(fake, "pg-1", "标题A", "正文A")
    _seed_page(fake, "pg-2", "标题B", "正文B")
    service = _service(tmp_path, fake)
    result = service.sync(full=True)
    assert result.scanned == 2
    assert result.updated == 2
    assert result.committed_scan_boundary is not None
    assert service.repo.get_sync_state().last_full_sync_at is not None


def test_sync_incremental_skips_unchanged(tmp_path):
    fake = FakeNotionClient()
    _seed_page(fake, "pg-1", "标题A", "正文A")
    service = _service(tmp_path, fake)
    service.sync(full=True)
    result = service.sync()  # 无变化
    assert result.updated == 0
    assert result.unchanged >= 0


def test_sync_detects_remote_edit(tmp_path):
    fake = FakeNotionClient()
    _seed_page(fake, "pg-1", "标题A", "正文A")
    service = _service(tmp_path, fake)
    service.sync(full=True)
    # 模拟远端修改正文。
    fake.blocks["pg-1"] = blocks_for_create("正文改过了", None)
    result = service.sync()
    assert result.updated == 1


def test_record_discussion_enqueues_append_and_patch(tmp_path):
    fake = FakeNotionClient()
    _seed_page(fake, "pg-1", "标题A", "正文A")
    service = _service(tmp_path, fake)
    service.read("pg-1")
    record = service.record_discussion(
        notion_page_id="pg-1", user_judgment="判断", ai_proposals=["提议"], open_questions=["问题"]
    )
    assert record.writeback_operation_id is not None
    pending = service.log.list_pending()
    types = {op.type for op in pending}
    assert "append_discussion" in types
    assert "patch_user_field" in types
    # 幂等：同一素材同一版本再次记录，讨论 id 不变。
    again = service.record_discussion(notion_page_id="pg-1", user_judgment="判断")
    assert again.discussion_id == record.discussion_id
