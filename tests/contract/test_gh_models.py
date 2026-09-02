# tests/contract/test_gh_models.py
import json
from pathlib import Path

from finch.github.models import (
    CommitDetail,
    CommitSummary,
    PullRequest,
    RepoInfo,
    parse_commit_detail,
    parse_commit_summary,
    parse_pull_request,
    parse_repo_view,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "gh"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_repo_view():
    r = parse_repo_view(_load("repo-view.json"))
    assert isinstance(r, RepoInfo)
    assert r.name_with_owner == "flingjie/FDE-Gym"
    assert r.is_private is False
    assert r.default_branch == "main"


def test_parse_commit_summary_list():
    data = _load("commits-page.json")
    assert isinstance(data, list) and len(data) >= 1
    c = parse_commit_summary(data[0])
    assert isinstance(c, CommitSummary)
    assert len(c.sha) == 40
    assert c.author_date.tzinfo is not None
    assert c.parents


def test_parse_commit_detail_has_files_and_patch_flag():
    d = parse_commit_detail(_load("commit-detail.json"))
    assert isinstance(d, CommitDetail)
    assert d.files, "commit detail must carry files"
    assert d.stats
    # patch 缺失/截断时必须能显式表达
    assert isinstance(d.patch_incomplete, bool)


def test_patch_incomplete_when_patch_missing():
    data = _load("commit-detail.json")
    data["files"][0]["patch"] = None
    assert parse_commit_detail(data).patch_incomplete is True


def test_parse_pull_request():
    p = parse_pull_request(_load("pr-view.json"))
    assert isinstance(p, PullRequest)
    assert p.number == 1
    assert p.state in {"OPEN", "MERGED", "CLOSED"}
