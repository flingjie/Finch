"""GitHub 数据模型与 `gh` JSON 归一化（spec 5.2 / 7）。"""

from datetime import datetime
from pydantic import BaseModel, Field


class RepoInfo(BaseModel):
    name_with_owner: str
    default_branch: str
    url: str
    is_private: bool


class CommitSummary(BaseModel):
    sha: str
    message: str
    author_date: datetime
    html_url: str
    parents: list[str] = Field(default_factory=list)


class CommitFile(BaseModel):
    filename: str
    status: str
    additions: int = 0
    deletions: int = 0
    patch: str | None = None


class CommitDetail(CommitSummary):
    files: list[CommitFile] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
    patch_incomplete: bool = False


class PullRequest(BaseModel):
    number: int
    title: str
    body: str | None = None
    url: str
    state: str


def parse_repo_view(data: dict) -> RepoInfo:
    return RepoInfo(
        name_with_owner=data["nameWithOwner"],
        default_branch=(data.get("defaultBranchRef") or {}).get("name", "main"),
        url=data["url"],
        is_private=data.get("isPrivate", False),
    )


def parse_commit_summary(data: dict) -> CommitSummary:
    commit = data.get("commit", {})
    return CommitSummary(
        sha=data["sha"],
        message=(commit.get("message") or "").split("\n", 1)[0],
        author_date=commit.get("author", {}).get("date"),
        html_url=data.get("html_url", ""),
        parents=[p["sha"] for p in data.get("parents", []) if p.get("sha")],
    )


def parse_commit_detail(data: dict) -> CommitDetail:
    base = parse_commit_summary(data)
    files = [
        CommitFile(
            filename=f["filename"],
            status=f.get("status", "modified"),
            additions=f.get("additions", 0),
            deletions=f.get("deletions", 0),
            patch=f.get("patch"),
        )
        for f in data.get("files", [])
    ]
    patch_incomplete = any(f.patch is None or (f.patch and f.patch.endswith("\n...")) for f in files)
    return CommitDetail(
        **base.model_dump(),
        files=files,
        stats=data.get("stats", {}),
        patch_incomplete=patch_incomplete,
    )


def parse_pull_request(data: dict) -> PullRequest:
    return PullRequest(
        number=data["number"],
        title=data["title"],
        body=data.get("body"),
        url=data.get("url", ""),
        state=(data.get("state") or "").upper(),
    )
