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


def test_user_parses_login(monkeypatch):
    _set(json.dumps({"login": "iFurySt", "html_url": "https://github.com/iFurySt"}), monkeypatch)
    data = GhClient().user("iFurySt")
    assert data["login"] == "iFurySt"
    assert data["html_url"] == "https://github.com/iFurySt"


def test_list_public_repos_filters_and_caps(monkeypatch):
    _set(
        json.dumps(
            [
                {
                    "full_name": "iFurySt/keep",
                    "html_url": "https://github.com/iFurySt/keep",
                    "description": "agent eval harness",
                    "owner": {"login": "iFurySt"},
                    "private": False,
                    "fork": False,
                    "archived": False,
                    "disabled": False,
                    "pushed_at": "2026-09-04T06:05:12Z",
                },
                {
                    "full_name": "iFurySt/forked",
                    "html_url": "https://github.com/iFurySt/forked",
                    "description": "nope",
                    "owner": {"login": "iFurySt"},
                    "private": False,
                    "fork": True,
                    "archived": False,
                    "disabled": False,
                    "pushed_at": "2026-09-04T06:05:12Z",
                },
            ]
        ),
        monkeypatch,
    )
    repos = GhClient().list_public_repos("iFurySt", limit=20)
    assert [r.name_with_owner for r in repos] == ["iFurySt/keep"]
    assert repos[0].description == "agent eval harness"
    assert repos[0].pushed_at is not None


def test_list_repo_issues_excludes_pull_requests(monkeypatch):
    _set(
        json.dumps(
            [
                {"id": 1, "number": 10, "title": "bug", "html_url": "u/10", "body": "x"},
                {"id": 2, "number": 11, "title": "pr", "html_url": "u/11", "pull_request": {}},
            ]
        ),
        monkeypatch,
    )
    issues = GhClient().list_repo_issues("acme/widget")
    assert [i["number"] for i in issues] == [10]


def test_list_repo_pull_requests_parses(monkeypatch):
    _set(
        json.dumps([{"id": 3, "number": 12, "title": "fix", "html_url": "u/12"}]),
        monkeypatch,
    )
    prs = GhClient().list_repo_pull_requests("acme/widget")
    assert [p["number"] for p in prs] == [12]


def test_list_repo_discussions_parses(monkeypatch):
    captured = {}

    def fake(argv, timeout):
        captured["argv"] = argv
        return {
            "ok": True,
            "exit_code": 0,
            "stdout": json.dumps(
                {
                    "data": {
                        "repository": {
                            "discussions": {
                                "nodes": [
                                    {
                                        "title": "how do I import?",
                                        "url": "https://github.com/acme/widget/discussions/1",
                                        "body": "stuck",
                                        "createdAt": "2026-09-01T00:00:00Z",
                                        "author": {"login": "alice"},
                                    }
                                ]
                            }
                        }
                    }
                }
            ),
            "stderr": "",
        }

    monkeypatch.setattr("finch.github.gh_client._run", fake)
    discs = GhClient().list_repo_discussions("acme/widget", limit=5)
    assert [d["title"] for d in discs] == ["how do I import?"]
    joined = " ".join(captured["argv"])
    assert "graphql" in joined
    assert "first: 5" in joined

