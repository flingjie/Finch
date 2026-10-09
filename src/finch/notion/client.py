"""Notion REST adapter（直连 ``api.notion.com/v1``，标准库 ``urllib``）。

镜像 ``llm/openai_compatible.py`` 的做法：不引入第三方 HTTP 依赖；每次调用带超时；
把 ``HTTPError`` / 超时 / 网络错误统一映射为带状态的 ``NotionError``。适配层只做
传输与 JSON 往返，不承载领域规则（字段映射在 ``materials/field_map.py``）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import quote


class NotionError(RuntimeError):
    """Notion 请求失败：携带 HTTP 状态码与 Notion 侧错误码（``body["code"]``）。"""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        notion_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.notion_code = notion_code


class NotionClient:
    """Notion REST 客户端：query / create / read / patch / append 的薄封装。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.notion.com/v1",
        version: str = "2022-06-28",
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.version = version
        self.timeout = timeout

    # ---- transport ----

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": self.version,
            "Content-Type": "application/json",
        }
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            notion_code: str | None = None
            try:
                parsed = json.loads(body)
                notion_code = parsed.get("code") if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                notion_code = None
            raise NotionError(
                f"Notion {method} {path} failed ({exc.code}): {body[:200]}",
                status_code=exc.code,
                notion_code=notion_code,
            ) from exc
        except TimeoutError as exc:
            # urlopen 只把 request() 的 OSError 包成 URLError；getresponse()/read()
            # 超时会直接抛 TimeoutError（CPython http.client），必须单独接住。
            raise NotionError(
                f"Notion {method} {path} timed out after {self.timeout:g}s", status_code=None
            ) from exc
        except urllib.error.URLError as exc:
            raise NotionError(
                f"Notion {method} {path} network error: {exc.reason}", status_code=None
            ) from exc

    # ---- database / page ----

    def get_database(self, database_id: str) -> dict:
        return self._request("GET", f"/v1/databases/{database_id}")

    def query_database(
        self,
        database_id: str,
        *,
        filter: dict | None = None,
        start_cursor: str | None = None,
        page_size: int = 100,
        sorts: list[dict] | None = None,
    ) -> dict:
        payload: dict = {"page_size": page_size}
        if filter is not None:
            payload["filter"] = filter
        if start_cursor:
            payload["start_cursor"] = start_cursor
        if sorts is not None:
            payload["sorts"] = sorts
        return self._request("POST", f"/v1/databases/{database_id}/query", payload)

    def query_all(
        self,
        database_id: str,
        *,
        filter: dict | None = None,
        sorts: list[dict] | None = None,
        page_size: int = 100,
    ) -> list[dict]:
        """分页读完整个查询结果（纯传输循环，无领域规则）。"""
        pages: list[dict] = []
        cursor: str | None = None
        while True:
            resp = self.query_database(
                database_id,
                filter=filter,
                start_cursor=cursor,
                page_size=page_size,
                sorts=sorts,
            )
            pages.extend(resp.get("results", []))
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return pages

    def create_page(
        self,
        database_id: str,
        properties: dict,
        children: list[dict] | None = None,
    ) -> dict:
        payload: dict = {"parent": {"database_id": database_id}, "properties": properties}
        if children:
            payload["children"] = children
        return self._request("POST", "/v1/pages", payload)

    def get_page(self, page_id: str) -> dict:
        return self._request("GET", f"/v1/pages/{page_id}")

    def list_block_children(
        self, block_id: str, *, start_cursor: str | None = None, page_size: int = 100
    ) -> dict:
        query = f"?page_size={page_size}"
        if start_cursor:
            query += f"&start_cursor={quote(start_cursor)}"
        return self._request("GET", f"/v1/blocks/{block_id}/children{query}")

    def list_all_block_children(self, block_id: str) -> list[dict]:
        blocks: list[dict] = []
        cursor: str | None = None
        while True:
            resp = self.list_block_children(block_id, start_cursor=cursor)
            blocks.extend(resp.get("results", []))
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return blocks

    def patch_page_properties(self, page_id: str, properties: dict) -> dict:
        return self._request("PATCH", f"/v1/pages/{page_id}", {"properties": properties})

    def append_block_children(self, block_id: str, children: list[dict]) -> dict:
        return self._request("PATCH", f"/v1/blocks/{block_id}/children", {"children": children})
