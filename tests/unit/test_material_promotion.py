"""Unit tests for material → IdeaCandidate promotion."""

from __future__ import annotations

from _notion_fake import FakeNotionClient

from finch.content.jobs import ContentJobStatus
from finch.materials.field_map import material_toggle_block
from finch.materials.models import MaterialSnapshot
from finch.materials.promotion import MaterialPromotionService
from finch.materials.service import MaterialService
from finch.storage.workspace import Workspace


def _snapshot() -> MaterialSnapshot:
    return MaterialSnapshot(
        block_id="blk-1",
        page_id="pg-1",
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
    fake.set_page("pg-1")
    created = fake.append_block_children(
        "pg-1", [material_toggle_block("标题A", "发生了什么", "我的感触")]
    )
    block_id = created["results"][0]["id"]
    service = MaterialService(Workspace(tmp_path), fake, "pg-1")
    first = service.promote(block_id, core_point="核心主张")
    second = service.promote(block_id, core_point="核心主张")
    assert first.id == second.id
    assert first.status == ContentJobStatus.PROPOSED
    third = service.promote(block_id, core_point="另一个主张")
    assert third.id != first.id  # 不同主张 → 不同 job
