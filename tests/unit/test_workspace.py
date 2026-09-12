"""Workspace 原子写 / YAML / frontmatter / JSONL 原语测试。"""

from datetime import UTC, datetime

import pytest

from finch.peers.models import PeerProfile, PlatformIdentity
from finch.storage.workspace import Workspace


def _profile() -> PeerProfile:
    return PeerProfile(
        id="p1",
        platform_identities=[PlatformIdentity(platform="x", author_id="alice")],
        last_meaningful_interaction_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )


def test_yaml_roundtrip(tmp_path):
    ws = Workspace(tmp_path)
    model = _profile()
    p = tmp_path / "p.yaml"
    ws.write_yaml(p, model)
    assert ws.read_yaml(p, PeerProfile) == model


def test_yaml_write_idempotent_bytes(tmp_path):
    ws = Workspace(tmp_path)
    p = tmp_path / "p.yaml"
    ws.write_yaml(p, _profile())
    first = p.read_text()
    ws.write_yaml(p, _profile())
    assert p.read_text() == first


def test_read_yaml_missing_returns_none(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.read_yaml(tmp_path / "nope.yaml", PeerProfile) is None


def test_atomic_write_leaves_no_tmp(tmp_path):
    ws = Workspace(tmp_path)
    p = tmp_path / "x.txt"
    ws.atomic_write(p, "hello")
    assert p.read_text() == "hello"
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_atomic_write_uses_unique_tmp_names(tmp_path, monkeypatch):
    ws = Workspace(tmp_path)
    p = tmp_path / "x.txt"
    seen: list[str] = []
    real_replace = __import__("os").replace

    def tracking_replace(src, dst):
        seen.append(str(src))
        return real_replace(src, dst)

    monkeypatch.setattr("os.replace", tracking_replace)
    ws.atomic_write(p, "a")
    ws.atomic_write(p, "b")
    assert len(seen) == 2
    assert seen[0] != seen[1]
    assert all(".tmp" in name for name in seen)
    assert p.read_text() == "b"


def test_frontmatter_roundtrip_preserves_body_horizontal_rule(tmp_path):
    ws = Workspace(tmp_path)
    meta = {"id": "d1", "kind": "original", "candidate_id": None}
    body = "# Title\n\nSome text.\n\n---\n\nMore."
    p = tmp_path / "draft.md"
    ws.write_frontmatter(p, meta, body)
    m2, b2 = ws.read_frontmatter(p)
    assert m2 == meta
    assert b2 == body


def test_safe_filename_rejects_path_traversal(tmp_path):
    ws = Workspace(tmp_path)
    with pytest.raises(ValueError):
        ws.safe_filename("../evil")


def test_safe_filename_maps_colon(tmp_path):
    ws = Workspace(tmp_path)
    assert ws.safe_filename("intent:abc") == "intent_abc"


def test_jsonl_append_and_read(tmp_path):
    ws = Workspace(tmp_path)
    p = tmp_path / "critic.jsonl"
    ws.append_jsonl(p, {"round": 0, "outcome": "pass"})
    ws.append_jsonl(p, {"round": 1, "outcome": "rewrite"})
    assert ws.read_jsonl(p) == [
        {"round": 0, "outcome": "pass"},
        {"round": 1, "outcome": "rewrite"},
    ]


def test_read_jsonl_skips_malformed_trailing_line(tmp_path):
    ws = Workspace(tmp_path)
    p = tmp_path / "critic.jsonl"
    p.write_text('{"round": 0}\n{"round": 1', encoding="utf-8")
    assert ws.read_jsonl(p) == [{"round": 0}]
