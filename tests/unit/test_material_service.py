"""Unit tests for MaterialService (capture / read / sync / record_discussion)."""

from __future__ import annotations

from _notion_fake import FakeNotionClient

from finch.materials.field_map import material_toggle_block
from finch.materials.models import SyncOperationStatus
from finch.materials.service import MaterialService
from finch.storage.workspace import Workspace


def _service(tmp_path, fake) -> MaterialService:
    return MaterialService(Workspace(tmp_path), fake, "pg-1")


def _seed_material(
    fake: FakeNotionClient, title: str = "标题A", body: str = "发生了什么", reflection=None
) -> str:
    fake.set_page("pg-1")
    created = fake.append_block_children("pg-1", [material_toggle_block(title, body, reflection)])
    return created["results"][0]["id"]


def test_capture_enqueues_append_material(tmp_path):
    fake = FakeNotionClient()
    fake.set_page("pg-1")
    service = _service(tmp_path, fake)
    op = service.capture(title="标题", body_text="正文")
    assert op.type == "append_material"
    assert op.status == SyncOperationStatus.PENDING
    assert op.payload["children"][0]["type"] == "toggle"
    again = service.capture(title="标题", body_text="正文")
    assert again.operation_id == op.operation_id  # 幂等


def test_read_caches_and_records_usage(tmp_path):
    fake = FakeNotionClient()
    block_id = _seed_material(fake, "标题A", "发生了什么")
    service = _service(tmp_path, fake)
    snapshot = service.read(block_id)
    assert snapshot.title == "标题A"
    assert snapshot.extractable_text == "发生了什么"
    assert service.repo.get_snapshot(block_id) is not None
    assert [link.usage for link in service.usage.list_for_block(block_id)] == ["read"]


def test_sync_pulls_and_advances_boundary(tmp_path):
    fake = FakeNotionClient()
    _seed_material(fake, "标题A", "正文A")
    _seed_material(fake, "标题B", "正文B")
    service = _service(tmp_path, fake)
    result = service.sync(full=True)
    assert result.scanned == 2
    assert result.updated == 2
    assert result.committed_scan_boundary is not None


def test_sync_skips_unchanged(tmp_path):
    fake = FakeNotionClient()
    _seed_material(fake, "标题A", "正文A")
    service = _service(tmp_path, fake)
    service.sync(full=True)
    result = service.sync()
    assert result.updated == 0


def test_sync_detects_remote_edit(tmp_path):
    fake = FakeNotionClient()
    block_id = _seed_material(fake, "标题A", "正文A")
    service = _service(tmp_path, fake)
    service.sync(full=True)
    # 模拟远端修改正文：改写该 toggle 的 children。
    fake.blocks[block_id] = [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": "改过了"}}]},
        }
    ]
    result = service.sync()
    assert result.updated == 1


def test_record_discussion_enqueues_append(tmp_path):
    fake = FakeNotionClient()
    block_id = _seed_material(fake, "标题A", "正文A")
    service = _service(tmp_path, fake)
    service.read(block_id)
    record = service.record_discussion(
        block_id=block_id, user_judgment="判断", ai_proposals=["提议"], open_questions=["问题"]
    )
    assert record.writeback_operation_id is not None
    types = {op.type for op in service.log.list_pending()}
    assert types == {"append_discussion"}
    again = service.record_discussion(block_id=block_id, user_judgment="判断")
    assert again.discussion_id == record.discussion_id  # 同一版本幂等
