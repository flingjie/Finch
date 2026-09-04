# tests/unit/test_gh_client.py
import json

import pytest

from finch.github.gh_client import GhClient, GhError
from finch.github.models import CommitDetail


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


def test_list_user_repos_parses(monkeypatch):
    _set(
        json.dumps(
            [
                {
                    "full_name": "flingjie/Finch",
                    "private": False,
                    "fork": False,
                    "archived": False,
                    "disabled": False,
                    "pushed_at": "2026-09-04T06:05:12Z",
                    "size": 468,
                }
            ]
        ),
        monkeypatch,
    )
    repos = GhClient().list_user_repos()
    assert [r.name_with_owner for r in repos] == ["flingjie/Finch"]
    assert repos[0].is_private is False


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


def test_backoff_respects_rate_limit(monkeypatch):
    calls = {"n": 0}
    sleeps = []

    def fake(argv, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            return {
                "ok": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "HTTP 403: API rate limit exceeded",
            }
        return {
            "ok": True,
            "exit_code": 0,
            "stdout": json.dumps(
                {
                    "nameWithOwner": "x/y",
                    "defaultBranchRef": {"name": "main"},
                    "url": "u",
                    "isPrivate": False,
                }
            ),
            "stderr": "",
        }

    monkeypatch.setattr("finch.github.gh_client._run", fake)
    monkeypatch.setattr("finch.github.gh_client._sleep", sleeps.append)
    GhClient().repo_view("x/y")
    assert calls["n"] == 3
    assert sleeps == [30.0, 30.0]


def test_list_commit_details_preserves_order(monkeypatch):
    client = GhClient()

    def fake_commit_detail(repo, sha):
        return CommitDetail(
            sha=sha,
            message=f"m {sha}",
            author_date="2026-09-01T00:00:00Z",
            html_url="u",
            parents=[],
            files=[],
            stats={},
        )

    monkeypatch.setattr(client, "commit_detail", fake_commit_detail)
    shas = ["c", "a", "b"]
    out = client.list_commit_details("x/y", shas, workers=2)
    assert [d.sha for d in out] == shas
