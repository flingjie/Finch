"""Unit tests for material → IdeaCandidate promotion."""

from __future__ import annotations

from _notion_fake import FakeNotionClient

from finch.content.jobs import ContentJobStatus
from finch.materials.field_map import blocks_for_create
from finch.materials.models import MaterialSnapshot
from finch.materials.promotion import MaterialPromotionService
from finch.materials.service import MaterialService
from finch.storage.workspace import Workspace


def _snapshot() -> MaterialSnapshot:
    return MaterialSnapshot(
        notion_page_id="pg-1",
        page_url="https://www.notion.so/pg-1",
        title="标题A",
        extractable_text="发生了什么",
        user_reflection="我的感触",
        source_hash="h",
    )


def test_from_material_maps_contract_fields():
    candidate = MaterialPromotionService().from_material(
        _snapshot(), core_point="核心主张", reader_problem="读者问题", why_worth_saying="为什么"
    )
    assert candidate.origin == "synthesis"
    assert candidate.source_kind == "note"
    assert candidate.evidence_status == "unverified"
    assert candidate.facts == []
    assert candidate.source_refs[0].type == "notion"
    assert candidate.source_refs[0].ref == "https://www.notion.so/pg-1"
    assert candidate.interpretation == "我的感触"


def test_promote_is_idempotent(tmp_path):
    fake = FakeNotionClient()
    fake.pages["pg-1"] = {
        "id": "pg-1",
        "url": "https://www.notion.so/pg-1",
        "properties": {
            "标题": {"type": "title", "title": [{"plain_text": "标题A"}]},
            "主题标签": {"type": "multi_select", "multi_select": []},
            "已讨论": {"type": "checkbox", "checkbox": False},
        },
        "last_edited_time": "2026-10-09T00:00:00.000Z",
    }
    fake.blocks["pg-1"] = blocks_for_create("发生了什么", "我的感触")
    service = MaterialService(Workspace(tmp_path), fake, "db-1")
    first = service.promote("pg-1", core_point="核心主张")
    second = service.promote("pg-1", core_point="核心主张")
    assert first.id == second.id
    assert first.status == ContentJobStatus.PROPOSED
    # 不同主张 → 不同 job（素材可提炼多个观点）。
    third = service.promote("pg-1", core_point="另一个主张")
    assert third.id != first.id
