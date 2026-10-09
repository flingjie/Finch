"""Shared fake NotionClient for materials tests (in-memory, deterministic).

模拟「页面 + toggle 块 + children」结构：append 时给块赋 id（平面，不嵌套）。
用于测试 service / queue，不涉及真实网络。
"""

from __future__ import annotations

from finch.notion.client import NotionError


class FakeNotionClient:
    def __init__(self) -> None:
        self.blocks: dict[str, list[dict]] = {}
        self.pages: dict[str, dict] = {}
        self.appended: list[tuple[str, list[dict]]] = []
        self.raise_on_append: NotionError | None = None
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"blk-{self._counter}"

    def set_page(self, page_id: str, title: str = "月度页") -> str:
        self.pages[page_id] = {
            "id": page_id,
            "url": f"https://www.notion.so/{page_id}",
            "properties": {"title": {"title": [{"plain_text": title}]}},
            "last_edited_time": "2026-10-09T00:00:00.000Z",
        }
        self.blocks.setdefault(page_id, [])
        return page_id

    def get_page(self, page_id: str) -> dict:
        if page_id not in self.pages:
            raise NotionError("not found", status_code=404)
        return self.pages[page_id]

    def list_all_block_children(self, block_id: str) -> list[dict]:
        return list(self.blocks.get(block_id, []))

    def append_block_children(self, block_id: str, children: list[dict]) -> dict:
        if self.raise_on_append is not None:
            raise self.raise_on_append
        created = []
        for child in children:
            materialized = dict(child)
            materialized["id"] = self._next_id()
            created.append(materialized)
        self.blocks.setdefault(block_id, []).extend(created)
        self.appended.append((block_id, children))
        return {"results": created}

    def seed_toggle(self, page_id: str, toggle_id: str, title: str, body_children=None) -> None:
        """把一条已存在的 toggle（summary + children）放进页面，供去重/部分写入测试用。"""
        toggle = {
            "id": toggle_id,
            "type": "toggle",
            "toggle": {"rich_text": [{"type": "text", "text": {"content": title}}]},
        }
        self.blocks.setdefault(page_id, []).append(toggle)
        self.blocks[toggle_id] = list(body_children or [])

    def seed_child_page(self, parent_id: str, child_id: str, title: str = "") -> None:
        """把一张子页面（child_page 块）放进父页面，供多页发现测试用。"""
        self.blocks.setdefault(parent_id, []).append(
            {"id": child_id, "type": "child_page", "child_page": {"title": title}}
        )

    def create_page(self, parent_page_id: str, title: str) -> dict:
        """创建一张子页面，并在父页面登记 child_page 块（供写入定位测试用）。"""
        page_id = self._next_id()
        self.pages[page_id] = {
            "id": page_id,
            "url": f"https://www.notion.so/{page_id}",
            "properties": {"title": {"title": [{"plain_text": title}]}},
            "last_edited_time": "2026-10-09T00:00:00.000Z",
        }
        self.blocks.setdefault(page_id, [])
        self.blocks.setdefault(parent_page_id, []).append(
            {"id": page_id, "type": "child_page", "child_page": {"title": title}}
        )
        return self.pages[page_id]
