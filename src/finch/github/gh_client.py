"""gh CLI 只读封装（spec 5.2）。"""

import json
import subprocess
import time

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


def _run(argv: list[str], timeout: float, stdin: str | None = None) -> dict:
    """子进程数组传参 + 超时，返回结构化结果；可选 stdin 输入。"""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            input=stdin,
        )
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "timeout"}
    except FileNotFoundError:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "not found"}


class GhError(RuntimeError):
    pass


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


class GhClient:
    def version(self) -> str:
        r = _run(["gh", "--version"], timeout=10.0)
        return r["stdout"].splitlines()[0] if r["ok"] else ""

    def auth_status(self) -> dict:
        r = _run(["gh", "auth", "status"], timeout=10.0)
        detail = (r["stderr"] or r["stdout"]).strip()
        return {"ok": r["ok"], "exit_code": r["exit_code"], "detail": detail}

    def _gh_json(self, argv: list[str], timeout: float = 30.0, retries: int = 3) -> dict | list:
        last: dict | None = None
        for attempt in range(retries):
            r = _run(argv, timeout)
            if r["ok"]:
                try:
                    return json.loads(r["stdout"])
                except json.JSONDecodeError as exc:
                    raise GhError(f"gh returned non-JSON: {exc}") from exc
            last = r
            if attempt < retries - 1:
                _sleep(2 ** attempt)
        assert last is not None
        raise GhError(f"gh failed after {retries} attempts: {last['stderr'].strip()}")

    def repo_view(self, repo: str) -> RepoInfo:
        data = self._gh_json(
            ["gh", "repo", "view", repo, "--json", "nameWithOwner,defaultBranchRef,url,isPrivate"]
        )
        return parse_repo_view(data)

    def list_commits(self, repo: str, since: str, per_page: int = 100) -> list[CommitSummary]:
        url = f"repos/{repo}/commits?sha=main&since={since}&per_page={per_page}"
        data = self._gh_json(
            ["gh", "api", "--paginate", "-H", "Accept: application/vnd.github+json", url],
            timeout=60.0,
        )
        return [parse_commit_summary(item) for item in data]

    def commit_detail(self, repo: str, sha: str) -> CommitDetail:
        data = self._gh_json(
            ["gh", "api", "-H", "Accept: application/vnd.github+json", f"repos/{repo}/commits/{sha}"]
        )
        return parse_commit_detail(data)

    def pr_view(self, repo: str, number: int) -> PullRequest:
        data = self._gh_json(
            ["gh", "pr", "view", str(number), "--repo", repo,
             "--json", "number,title,body,url,state,commits,files,reviews,comments"]
        )
        return parse_pull_request(data)
