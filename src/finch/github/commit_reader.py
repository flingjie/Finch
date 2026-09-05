"""Commit 噪声过滤与详情加载（spec 12.1「Commit 游标推进」已由 ingestion ledger 取代）。"""

from pathlib import Path

from .gh_client import GhClient
from .local_repo import LocalRepoClient, find_local_clone
from .models import CommitDetail

_LOCKFILE_MARKERS = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
                     "Cargo.lock", "Gemfile.lock", "go.sum", "uv.lock")
_FORMAT_MARKERS = ("format", "formatting", "prettier", "lint", "style", "black", "ruff")


def is_noise(commit: CommitDetail) -> bool:
    if not commit.files:
        return True
    paths = [f.filename.lower() for f in commit.files]
    if all(any(m in p for m in _LOCKFILE_MARKERS) for p in paths):
        return True
    if all(f.status == "renamed" for f in commit.files):
        return True
    if any(m in commit.message.lower() for m in _FORMAT_MARKERS) and not any(
        f.additions > 0 and f.status == "added" for f in commit.files
    ):
        return True
    return False


class CommitReader:
    def __init__(self, gh: GhClient, repo: str):
        self.gh = gh
        self.repo = repo

    def filter_noise(self, commits: list[CommitDetail]) -> list[CommitDetail]:
        return [c for c in commits if not is_noise(c)]


def load_commit_details(
    repo: str,
    gh: GhClient,
    *,
    local_dirs: list[Path],
    since: str | None = None,
    workers: int = 6,
) -> list[CommitDetail]:
    """Load commit details from a local checkout when available, else gh."""
    local = find_local_clone(repo, local_dirs)
    if local is not None:
        client = LocalRepoClient(repo, local)
        return client.list_commit_details(repo, since=since)
    summaries = gh.list_commits(repo, since=since)
    return gh.list_commit_details(repo, [s.sha for s in summaries], workers=workers)
