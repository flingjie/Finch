"""Unit tests for Notion field mapping (create properties + page→snapshot regions)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from finch.materials.field_map import (
    PROP_FINCH_OP,
    PROP_TITLE,
    blocks_for_create,
    blocks_for_discussion,
    page_to_snapshot,
    properties_for_create,
    query_filter_incremental,
)
from finch.materials.models import DiscussionRecord


def _page(page_id: str, properties: dict, last_edited: str | None = None) -> dict:
    return {
        "id": page_id,
        "url": f"https://www.notion.so/{page_id}",
        "properties": properties,
        "last_edited_time": last_edited or "2026-10-09T00:00:00.000Z",
    }


def _props(title: str = "标题A", url: str | None = None, tags: list[str] | None = None):
    props = {
        "标题": {"type": "title", "title": [{"plain_text": title}]},
        "已讨论": {"type": "checkbox", "checkbox": False},
    }
    if url:
        props["来源链接"] = {"type": "url", "url": url}
    props["主题标签"] = {
        "type": "multi_select",
        "multi_select": [{"name": t} for t in (tags or [])],
    }
    return props


def test_properties_for_create_shape():
    props = properties_for_create("标题", ["https://x.com/1"], ["tag1"], "op-1")
    assert props[PROP_TITLE]["title"][0]["text"]["content"] == "标题"
    assert props["来源链接"]["url"] == "https://x.com/1"
    assert props["主题标签"]["multi_select"] == [{"name": "tag1"}]
    assert props["已讨论"]["checkbox"] is False
    assert props[PROP_FINCH_OP]["rich_text"][0]["text"]["content"] == "op-1"


def test_properties_for_create_omits_empty_url():
    props = properties_for_create("标题", [], [], "op-1")
    assert "来源链接" not in props


def test_page_to_snapshot_splits_regions():
    children = blocks_for_create("发生了什么", "很有共鸣")
    page = _page("pg-1", _props(title="标题A", url="https://x.com/1", tags=["tag1"]))
    snapshot = page_to_snapshot(page, children)
    assert snapshot.notion_page_id == "pg-1"
    assert snapshot.title == "标题A"
    assert snapshot.source_urls == ["https://x.com/1"]
    assert snapshot.tags == ["tag1"]
    assert snapshot.user_reflection == "很有共鸣"
    assert "发生了什么" in snapshot.extractable_text
    assert "很有共鸣" in snapshot.extractable_text
    # 区域标记标题不是内容。
    assert "原始记录" not in snapshot.extractable_text
    assert "我的感触" not in snapshot.extractable_text
    assert snapshot.source_hash


def test_page_to_snapshot_no_reflection_when_marker_absent():
    children = blocks_for_create("只有原文", None)
    snapshot = page_to_snapshot(_page("pg-1", _props()), children)
    assert snapshot.user_reflection is None
    assert snapshot.extractable_text == "只有原文"


def test_page_to_snapshot_excludes_finch_region_from_hash():
    raw = blocks_for_create("原文", None)
    record = DiscussionRecord(discussion_id="disc-1", notion_page_id="pg-1", source_hash="h",
                              user_judgment="判断")
    finch = blocks_for_discussion("disc-1", record)
    base = page_to_snapshot(_page("pg-1", _props()), raw)
    with_finch = page_to_snapshot(_page("pg-1", _props()), raw + finch)
    # Finch 追加区不改变用户区哈希。
    assert with_finch.source_hash == base.source_hash
    assert with_finch.extractable_text == base.extractable_text


def test_query_filter_incremental_overlaps_window():
    boundary = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    filter_ = query_filter_incremental(boundary, timedelta(hours=1))
    assert filter_["timestamp"] == "last_edited_time"
    assert filter_["last_edited_time"]["on_or_after"] == "2026-10-09T11:00:00+00:00"
    assert query_filter_incremental(None, timedelta(hours=1)) is None
