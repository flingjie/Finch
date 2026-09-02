"""Tests for GraphContext envelope (spec C3)."""

import json

from finch.graph.context import GraphContext, MissingContextError, items_payload, parse_items
from pydantic import BaseModel


class Item(BaseModel):
    id: str


def test_project_fail_closed_on_missing_read():
    ctx = GraphContext()
    ctx.put("candidates", {"items": []})
    try:
        ctx.project(["candidates", "evidence_cards"])
        raise AssertionError("should have failed")
    except MissingContextError as exc:
        assert "evidence_cards" in exc.missing


def test_hydrate_from_output_json():
    ctx = GraphContext()
    ctx.hydrate("evidence_cards", json.dumps({"items": [{"id": "ev_1"}]}))
    assert ctx.project(["evidence_cards"])["evidence_cards"]["items"][0]["id"] == "ev_1"


def test_items_payload_roundtrip():
    payload = items_payload([Item(id="a")])
    assert parse_items(payload, Item)[0].id == "a"
