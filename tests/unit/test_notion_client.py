"""Unit tests for the Notion REST adapter."""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import patch

import pytest

from finch.notion.client import NotionClient, NotionError


def _resp(payload: dict):
    class _Resp:
        def read(self) -> bytes:
            return json.dumps(payload).encode("utf-8")

    return _Resp()


def test_list_children_sets_headers_and_url():
    client = NotionClient(api_key="secret", version="2022-06-28")
    with patch("urllib.request.urlopen") as mock:
        mock.return_value.__enter__.return_value = _resp({"results": [], "has_more": False})
        client.list_block_children("blk-1", page_size=100)
        request = mock.call_args[0][0]
        assert request.full_url == "https://api.notion.com/v1/blocks/blk-1/children?page_size=100"
        assert request.get_header("Authorization") == "Bearer secret"
        # urllib 将头名规范化为首字母大写（Notion-Version → Notion-version）。
        assert request.get_header("Notion-version") == "2022-06-28"
        assert request.get_header("Content-type") == "application/json"


def test_append_block_children_sends_children_body():
    client = NotionClient(api_key="k")
    block = {"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}}
    with patch("urllib.request.urlopen") as mock:
        mock.return_value.__enter__.return_value = _resp({"results": []})
        client.append_block_children("blk-1", [block])
        request = mock.call_args[0][0]
        assert request.full_url == "https://api.notion.com/v1/blocks/blk-1/children"
        assert json.loads(request.data)["children"] == [block]


def test_http_error_maps_to_notion_error():
    client = NotionClient(api_key="k")
    err = urllib.error.HTTPError("url", 400, "Bad Request", {}, None)
    err.read = lambda: b'{"code": "validation_error", "message": "bad"}'
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(NotionError) as exc_info:
            client.get_page("p1")
    assert exc_info.value.status_code == 400
    assert exc_info.value.notion_code == "validation_error"


def test_timeout_maps_to_notion_error_without_status():
    client = NotionClient(api_key="k")
    with patch("urllib.request.urlopen", side_effect=TimeoutError()):
        with pytest.raises(NotionError) as exc_info:
            client.get_page("p1")
    assert exc_info.value.status_code is None


def test_network_error_maps_to_notion_error_without_status():
    client = NotionClient(api_key="k")
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
        with pytest.raises(NotionError) as exc_info:
            client.get_page("p1")
    assert exc_info.value.status_code is None
