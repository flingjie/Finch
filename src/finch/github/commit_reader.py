"""增量读取 Commit 并过滤噪声（spec 12.1「Commit 游标推进」）。"""

import json
from datetime import UTC, datetime
from pathlib import Path

from .gh_client import GhClient
from .models import CommitDetail, CommitSummary

_LOCKFILE_MARKERS = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
                     "Cargo.lock", "Gemfile.lock", "go.sum", "uv.lock")
_FORMAT_MARKERS = ("format", "formatting", "prettier", "lint", "style", "black", "ruff")


def _cursor_path() -> Path:
    return Path("var/cache/github_sync_state.json")


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

    def sync(self, since: str | None = None) -> list[CommitSummary]:
        if since is None:
            since = self._load_cursor()
        commits = self.gh.list_commits(self.repo, since=since)
        self._save_cursor()
        return commits

    def filter_noise(self, commits: list[CommitDetail]) -> list[CommitDetail]:
        return [c for c in commits if not is_noise(c)]

    def _load_cursor(self) -> str:
        path = _cursor_path()
        if path.exists():
            data = json.loads(path.read_text())
            return data.get("last_synced_at") or "1970-01-01T00:00:00Z"
        return "1970-01-01T00:00:00Z"

    def _save_cursor(self) -> None:
        path = _cursor_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_synced_at": datetime.now(UTC).isoformat()}))
