"""Read commits from a local git checkout instead of the GitHub API.

Finch prefers local data when a checkout for the configured repository exists,
which avoids rate limits and network round trips for the bulk commit reads.
"""

import subprocess
from datetime import datetime
from pathlib import Path

from .models import CommitDetail, CommitFile, CommitSummary

# 单条 commit 的 `git show`/`git log` 组合格式：`\x1e`（RS）作记录分隔、`\x1f`（US）
# 作字段分隔。这样一次子进程即可同时拿到 summary 与 patch，且可逐条切分。
_DETAIL_FORMAT = "%x1e%H%x1f%s%x1f%aI%x1f%P"


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


def _extract_origin_url(config_text: str) -> str | None:
    """从 .git/config 文本提取 `[remote "origin"]` 段的 `url` 值。"""
    section: str | None = None
    for raw in config_text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section == 'remote "origin"' and line.startswith("url"):
            _, sep, value = line.partition("=")
            if sep:
                return value.strip()
    return None


def _origin_from_config(root: Path) -> str | None:
    """普通 clone（`.git` 为目录）直接读 `.git/config`，免起 git 子进程。

    `.git` 为文件（worktree）或读取/解析失败返回 None，由调用方回退子进程路径。
    """
    git_dir = root / ".git"
    if not git_dir.is_dir():
        return None
    try:
        return _extract_origin_url((git_dir / "config").read_text())
    except OSError:
        return None


def find_local_clone(repo: str, base_dirs: list[Path]) -> Path | None:
    """Find a checkout under `base_dirs` whose origin matches `owner/name`."""
    for base in base_dirs:
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if not child.is_dir() or not (child / ".git").exists():
                continue
            url = _origin_from_config(child)
            if url is None:
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


def _parse_detail(chunk: str, repo: str) -> CommitDetail:
    """解析一条 commit 记录（summary 行 + patch）为 CommitDetail。

    chunk 是 ``_DETAIL_FORMAT`` 输出按 ``\x1e`` 切出的一段（可带前导 ``\x1e``）：
    首行 summary，其后为 patch。注意不能用 ``splitlines()``——它会视 ``\x1e`` 为行界。
    """
    if chunk.startswith("\x1e"):
        chunk = chunk[1:]
    summary_line, _, patch = chunk.partition("\n")
    summary = _parse_summary(summary_line, repo)
    files = _parse_patch(patch)
    additions = sum(f.additions for f in files)
    deletions = sum(f.deletions for f in files)
    return CommitDetail(
        **summary.model_dump(),
        files=files,
        stats={"total": additions + deletions, "additions": additions, "deletions": deletions},
        patch_incomplete=any(f.patch is None for f in files),
    )


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
        out = _run_git(
            self.root, ["show", f"--format={_DETAIL_FORMAT}", "--patch", "--find-renames", sha]
        )
        return _parse_detail(out, self.repo)

    def list_commit_details(self, repo: str, since: str | None = None) -> list[CommitDetail]:
        """一次 `git log --patch` 读全量 commit 详情（替代 2N+1 次子进程），newest-first。"""
        args = ["log", f"--format={_DETAIL_FORMAT}", "--patch", "--find-renames"]
        if since:
            args.append(f"--since={since}")
        out = _run_git(self.root, args)
        return [_parse_detail(chunk, self.repo) for chunk in out.split("\x1e") if chunk.strip()]


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
