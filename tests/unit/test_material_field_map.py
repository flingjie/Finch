"""Unit tests for Notion toggle-block mapping (material toggle + snapshot regions)."""

from __future__ import annotations

from finch.materials.field_map import (
    blocks_for_discussion,
    material_toggle_block,
    toggle_to_snapshot,
)
from finch.materials.models import DiscussionRecord


def _page(page_id: str = "pg-1") -> dict:
    return {
        "id": page_id,
        "url": f"https://www.notion.so/{page_id}",
        "last_edited_time": "2026-10-09T00:00:00.000Z",
    }


def _toggle_with_id(
    title: str, body: str, reflection: str | None = None
) -> tuple[dict, list[dict]]:
    block = material_toggle_block(title, body, reflection)
    block["id"] = "blk-1"
    return block, block["children"]


def test_material_toggle_block_shape():
    block = material_toggle_block("标题", "发生了什么", "很有共鸣")
    assert block["type"] == "toggle"
    assert block["toggle"]["rich_text"][0]["text"]["content"] == "标题"
    children = block["children"]
    assert children[0]["type"] == "paragraph"
    assert children[1]["type"] == "heading_3"  # 我的感触标记


def test_toggle_to_snapshot_splits_regions():
    block, children = _toggle_with_id("标题A", "发生了什么", "很有共鸣")
    snapshot = toggle_to_snapshot(_page(), block, children)
    assert snapshot.block_id == "blk-1"
    assert snapshot.title == "标题A"
    assert snapshot.user_reflection == "很有共鸣"
    assert "发生了什么" in snapshot.extractable_text
    assert "很有共鸣" in snapshot.extractable_text
    assert "我的感触" not in snapshot.extractable_text  # 标记不是内容
    assert snapshot.source_hash


def test_toggle_to_snapshot_no_reflection_when_marker_absent():
    block, children = _toggle_with_id("标题A", "只有原文", None)
    snapshot = toggle_to_snapshot(_page(), block, children)
    assert snapshot.user_reflection is None
    assert snapshot.extractable_text == "只有原文"


def test_toggle_to_snapshot_excludes_finch_region_from_hash():
    block, children = _toggle_with_id("标题A", "原文", None)
    record = DiscussionRecord(
        discussion_id="disc-1", block_id="blk-1", page_id="pg-1", source_hash="h",
        user_judgment="判断",
    )
    finch = blocks_for_discussion("disc-1", record)
    base = toggle_to_snapshot(_page(), block, children)
    with_finch = toggle_to_snapshot(_page(), block, children + finch)
    assert with_finch.source_hash == base.source_hash  # Finch 区不影响用户区哈希
    assert with_finch.discussed is True
    assert base.discussed is False
