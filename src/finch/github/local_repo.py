"""Read commits from a local git checkout instead of the GitHub API.

Finch prefers local data when a checkout for the configured repository exists,
which avoids rate limits and network round trips for the bulk commit reads.
"""

import subprocess
from datetime import datetime
from pathlib import Path

from .models import CommitDetail, CommitFile, CommitSummary


def _run_git(root: Path, args: list[str], *, timeout: float = 30.0) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def normalize_remote(url: str) -> str | None:
    """Normalize a git remote URL to its `owner/name` form."""
    value = url.strip().rstrip("/")
    if not value:
        return None
    if "://" in value:
        value = value.split("://", 1)[1]
    if "@" in value:
        value = value.split("@", 1)[1]
    if ":" in value:
        value = value.replace(":", "/", 1)
    if "/" in value:
        value = value.split("/", 1)[1]
    if value.endswith(".git"):
        value = value[:-4]
    return value or None


def find_local_clone(repo: str, base_dirs: list[Path]) -> Path | None:
    """Find a checkout under `base_dirs` whose origin matches `owner/name`."""
    for base in base_dirs:
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if not child.is_dir() or not (child / ".git").exists():
                continue
            try:
                url = _run_git(child, ["remote", "get-url", "origin"]).strip()
            except RuntimeError:
                continue
            if normalize_remote(url) == repo:
                return child
    return None


def _strip_ab_prefix(path: str) -> str:
    if path.startswith("a/"):
        return path[2:]
    if path.startswith("b/"):
        return path[2:]
    return path


def _parse_patch(text: str) -> list[CommitFile]:
    """Parse `git show --patch` output into per-file metadata and patches."""
    files: list[CommitFile] = []
    current: dict | None = None

    def finalize() -> CommitFile:
        assert current is not None
        patch = "\n".join(current["patch"]) if current["patch"] else None
        return CommitFile(
            filename=current["filename"],
            status=current["status"],
            additions=current["additions"],
            deletions=current["deletions"],
            patch=patch,
        )

    for line in text.splitlines():
        if line.startswith("diff --git "):
            if current is not None:
                files.append(finalize())
            rest = line[len("diff --git "):]
            new_path = rest.partition(" b/")[2] or rest
            current = {
                "filename": _strip_ab_prefix(new_path),
                "status": "modified",
                "additions": 0,
                "deletions": 0,
                "patch": [],
                "in_patch": False,
            }
        elif current is None:
            continue
        elif line.startswith("new file mode"):
            current["status"] = "added"
        elif line.startswith("deleted file mode"):
            current["status"] = "removed"
        elif line.startswith("rename from "):
            current["status"] = "renamed"
        elif line.startswith("@@ "):
            current["in_patch"] = True
            current["patch"].append(line)
        elif current["in_patch"]:
            if line.startswith("+"):
                if not line.startswith("+++"):
                    current["additions"] += 1
                current["patch"].append(line)
            elif line.startswith("-"):
                if not line.startswith("---"):
                    current["deletions"] += 1
                current["patch"].append(line)
            else:
                current["patch"].append(line)
        # Skip index / similarity / rename to / --- / +++ / binary header lines.

    if current is not None:
        files.append(finalize())
    return files


class LocalRepoClient:
    """Local checkout reader with the same commit surface as GhClient."""

    def __init__(self, repo: str, root: Path):
        self.repo = repo
        self.root = root

    def list_commits(self, repo: str, since: str | None = None) -> list[CommitSummary]:
        args = ["log", "--format=%H%x1f%s%x1f%aI%x1f%P"]
        if since:
            args.append(f"--since={since}")
        out = _run_git(self.root, args)
        return [_parse_summary(line, self.repo) for line in out.splitlines() if line]

    def commit_detail(self, repo: str, sha: str) -> CommitDetail:
        summary_line = _run_git(
            self.root, ["show", "-s", "--format=%H%x1f%s%x1f%aI%x1f%P", sha]
        ).strip()
        summary = _parse_summary(summary_line, self.repo)
        patch = _run_git(self.root, ["show", "--format=", "--patch", "--find-renames", sha])
        files = _parse_patch(patch)
        additions = sum(f.additions for f in files)
        deletions = sum(f.deletions for f in files)
        return CommitDetail(
            **summary.model_dump(),
            files=files,
            stats={"total": additions + deletions, "additions": additions, "deletions": deletions},
            patch_incomplete=any(f.patch is None for f in files),
        )


def _parse_summary(line: str, repo: str) -> CommitSummary:
    parts = line.split("\x1f")
    if len(parts) not in (3, 4):
        raise ValueError(f"unexpected git summary format: {line!r}")
    sha, subject, author_date = parts[:3]
    parents = parts[3] if len(parts) == 4 else ""
    return CommitSummary(
        sha=sha,
        message=subject,
        author_date=datetime.fromisoformat(author_date),
        html_url=f"https://github.com/{repo}/commit/{sha}",
        parents=parents.split() if parents else [],
    )
