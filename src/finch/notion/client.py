"""Notion REST adapter（直连 ``api.notion.com/v1``，标准库 ``urllib``）。

镜像 ``llm/openai_compatible.py`` 的做法：不引入第三方 HTTP 依赖；每次调用带超时；
把 ``HTTPError`` / 超时 / 网络错误统一映射为带状态的 ``NotionError``。适配层只做
传输与 JSON 往返，不承载领域规则（字段映射在 ``materials/field_map.py``）。
素材库只读写页面与其块（page / block children），不涉及数据库（database）。
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
    """Notion REST 客户端：读页 / 读块 / 追加块的薄封装（页面即块）。"""

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
            raise NotionError(
                f"Notion {method} {path} timed out after {self.timeout:g}s", status_code=None
            ) from exc
        except urllib.error.URLError as exc:
            raise NotionError(
                f"Notion {method} {path} network error: {exc.reason}", status_code=None
            ) from exc

    def get_page(self, page_id: str) -> dict:
        return self._request("GET", f"/pages/{page_id}")

    def list_block_children(
        self, block_id: str, *, start_cursor: str | None = None, page_size: int = 100
    ) -> dict:
        query = f"?page_size={page_size}"
        if start_cursor:
            query += f"&start_cursor={quote(start_cursor)}"
        return self._request("GET", f"/blocks/{block_id}/children{query}")

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

    def append_block_children(self, block_id: str, children: list[dict]) -> dict:
        return self._request("PATCH", f"/blocks/{block_id}/children", {"children": children})
