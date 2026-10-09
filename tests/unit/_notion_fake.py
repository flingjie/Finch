"""Shared fake NotionClient for materials tests (in-memory, deterministic).

模拟「页面 + toggle 块 + 嵌套 children」结构：append 时给块赋 id，嵌套 children 存入
``blocks[toggle_id]``。用于测试 service / queue，不涉及真实网络。
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
        created = [self._materialize(child) for child in children]
        self.blocks.setdefault(block_id, []).extend(created)
        self.appended.append((block_id, children))
        return {"results": created}

    def _materialize(self, block: dict) -> dict:
        """给块赋 id；嵌套 children 存入 ``blocks[id]``。"""
        materialized = dict(block)
        materialized["id"] = self._next_id()
        nested = materialized.pop("children", None)
        if nested:
            self.blocks[materialized["id"]] = [self._materialize(c) for c in nested]
        return materialized

    def seed_toggle(self, page_id: str, toggle_block: dict, toggle_id: str) -> None:
        """把一条已存在的 toggle（含 children）放进页面，供去重/部分写入测试用。"""
        self.blocks.setdefault(page_id, []).append(toggle_block)
        children = toggle_block.get("children", [])
        self.blocks[toggle_id] = list(children)
