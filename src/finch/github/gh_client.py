"""gh CLI 只读封装（spec 5.2）。"""

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from finch.github.models import (
    CommitDetail,
    CommitSummary,
    PullRequest,
    RepoInfo,
    RepoSummary,
    parse_commit_detail,
    parse_commit_summary,
    parse_pull_request,
    parse_repo_summary,
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
                detail = (r["stderr"] or r["stdout"]).strip().lower()
                delay = 2 ** attempt
                if "rate limit" in detail or "secondary rate limit" in detail:
                    delay = max(delay, 30.0)
                _sleep(delay)
        assert last is not None
        raise GhError(f"gh failed after {retries} attempts: {last['stderr'].strip()}")

    def repo_view(self, repo: str) -> RepoInfo:
        data = self._gh_json(
            ["gh", "repo", "view", repo, "--json", "nameWithOwner,defaultBranchRef,url,isPrivate"]
        )
        assert isinstance(data, dict)
        return parse_repo_view(data)

    def list_user_repos(self) -> list[RepoSummary]:
        data = self._gh_json(
            [
                "gh",
                "api",
                "--paginate",
                "-H",
                "Accept: application/vnd.github+json",
                "user/repos?sort=pushed&direction=desc&per_page=100",
            ],
            timeout=60.0,
        )
        assert isinstance(data, list)
        return [parse_repo_summary(item) for item in data]

    def list_commits(
        self, repo: str, since: str | None = None, per_page: int = 100
    ) -> list[CommitSummary]:
        since_clause = f"&since={since}" if since else ""
        url = f"repos/{repo}/commits?sha=main{since_clause}&per_page={per_page}"
        data = self._gh_json(
            ["gh", "api", "--paginate", "-H", "Accept: application/vnd.github+json", url],
            timeout=60.0,
        )
        assert isinstance(data, list)
        return [parse_commit_summary(item) for item in data]

    def list_commits_newest_first(
        self, repo: str, *, per_page: int = 100, max_commits: int = 200
    ) -> list[CommitSummary]:
        """从 main 分支 HEAD 起、按新到旧分页拉取，最多 ``max_commits`` 条（有界）。"""
        out: list[CommitSummary] = []
        page = 1
        while len(out) < max_commits:
            url = f"repos/{repo}/commits?sha=main&per_page={per_page}&page={page}"
            data = self._gh_json(
                ["gh", "api", "-H", "Accept: application/vnd.github+json", url],
                timeout=60.0,
            )
            assert isinstance(data, list)
            if not data:
                break
            for item in data:
                out.append(parse_commit_summary(item))
            if len(data) < per_page:
                break
            page += 1
        return out[:max_commits]

    def commit_detail(self, repo: str, sha: str) -> CommitDetail:
        data = self._gh_json(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github+json",
                f"repos/{repo}/commits/{sha}",
            ]
        )
        assert isinstance(data, dict)
        return parse_commit_detail(data)

    def list_commit_details(
        self, repo: str, shas: list[str], *, workers: int = 6
    ) -> list[CommitDetail]:
        """Fetch commit details concurrently, preserving input order."""
        results: dict[str, CommitDetail] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.commit_detail, repo, sha): sha for sha in shas}
            for future in as_completed(futures):
                results[futures[future]] = future.result()
        return [results[sha] for sha in shas]

    def pr_view(self, repo: str, number: int) -> PullRequest:
        data = self._gh_json(
            ["gh", "pr", "view", str(number), "--repo", repo,
             "--json", "number,title,body,url,state,commits,files,reviews,comments"]
        )
        assert isinstance(data, dict)
        return parse_pull_request(data)
