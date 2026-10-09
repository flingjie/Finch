"""Shared fake NotionClient for materials tests (in-memory, deterministic)."""

from __future__ import annotations

from finch.notion.client import NotionError


class FakeNotionClient:
    def __init__(self) -> None:
        self.pages: dict[str, dict] = {}
        self.blocks: dict[str, list[dict]] = {}
        self.created: list[tuple] = []
        self.appended: list[tuple] = []
        self.patched: list[tuple] = []
        self.raise_on_create: NotionError | None = None
        self.database_properties: dict[str, dict] = {
            "标题": {"type": "title"},
            "来源链接": {"type": "url"},
            "主题标签": {"type": "multi_select"},
            "已讨论": {"type": "checkbox"},
            "Finch 操作标识": {"type": "rich_text"},
        }

    def get_database(self, database_id: str) -> dict:
        return {"title": [{"plain_text": "素材库"}], "properties": self.database_properties}

    def create_page(self, database_id: str, properties: dict, children=None) -> dict:
        if self.raise_on_create is not None:
            raise self.raise_on_create
        page_id = f"page-{len(self.created) + 1}"
        self.pages[page_id] = {
            "id": page_id,
            "url": f"https://www.notion.so/{page_id}",
            "properties": properties,
            "last_edited_time": "2026-10-09T00:00:00.000Z",
        }
        self.blocks[page_id] = list(children or [])
        self.created.append((database_id, properties, children))
        return self.pages[page_id]

    def query_all(self, database_id, *, filter=None, sorts=None, page_size=100) -> list[dict]:
        return list(self.pages.values())

    def get_page(self, page_id: str) -> dict:
        if page_id not in self.pages:
            raise NotionError("not found", status_code=404)
        return self.pages[page_id]

    def list_all_block_children(self, block_id: str) -> list[dict]:
        return list(self.blocks.get(block_id, []))

    def append_block_children(self, block_id: str, children: list[dict]) -> dict:
        self.appended.append((block_id, children))
        self.blocks.setdefault(block_id, []).extend(children)
        return {"results": children}

    def patch_page_properties(self, page_id: str, properties: dict) -> dict:
        self.patched.append((page_id, properties))
        return self.pages.get(page_id, {})
