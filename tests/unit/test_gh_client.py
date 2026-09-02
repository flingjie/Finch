# tests/unit/test_gh_client.py
import json

import pytest

from finch.github.gh_client import GhClient, GhError


@pytest.fixture
def gh(monkeypatch):
    client = GhClient()
    monkeypatch.setattr("finch.github.gh_client._run", lambda argv, timeout: (
        {"ok": True, "exit_code": 0, "stdout": _FAKE, "stderr": ""}
    ))
    return client


_FAKE = ""


def _set(stdout: str, monkeypatch):
    def fake(argv, timeout):
        return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
    monkeypatch.setattr("finch.github.gh_client._run", fake)


def test_repo_view_parses(monkeypatch):
    _set(json.dumps({"nameWithOwner": "flingjie/FDE-Gym", "defaultBranchRef": {"name": "main"},
                     "url": "u", "isPrivate": False}), monkeypatch)
    r = GhClient().repo_view("flingjie/FDE-Gym")
    assert r.name_with_owner == "flingjie/FDE-Gym"
    assert r.is_private is False


def test_list_commits_uses_since(monkeypatch):
    captured = {}

    def fake(argv, timeout):
        captured["argv"] = argv
        return {"ok": True, "exit_code": 0, "stdout": "[]", "stderr": ""}

    monkeypatch.setattr("finch.github.gh_client._run", fake)
    GhClient().list_commits("flingjie/FDE-Gym", since="2026-01-01T00:00:00Z")
    joined = " ".join(captured["argv"])
    assert "since=2026-01-01T00:00:00Z" in joined
    assert "commits" in joined


def test_backoff_retries_then_raises(monkeypatch):
    calls = {"n": 0}

    def always_fail(argv, timeout):
        calls["n"] += 1
        return {"ok": False, "exit_code": 1, "stdout": "", "stderr": "boom"}

    monkeypatch.setattr("finch.github.gh_client._run", always_fail)
    monkeypatch.setattr("finch.github.gh_client._sleep", lambda s: None)  # 避免真实退避 sleep
    with pytest.raises(GhError):
        GhClient().repo_view("x/y")
    assert calls["n"] == 3
