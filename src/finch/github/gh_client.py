"""gh CLI 只读封装（spec 5.2）。"""

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

from finch.github.models import (
    CommitDetail,
    CommitSummary,
    PublicRepo,
    PullRequest,
    RepoInfo,
    RepoSummary,
    parse_commit_detail,
    parse_commit_summary,
    parse_public_repo,
    parse_pull_request,
    parse_repo_summary,
    parse_repo_view,
)

_DISCUSSIONS_QUERY = (
    "query($owner: String!, $name: String!) {"
    "repository(owner: $owner, name: $name) {"
    "discussions(first: {first}, orderBy: {field: CREATED_AT, direction: DESC}) {"
    "nodes { title url body createdAt author { login } }"
    "} } }"
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

    def user(self, login: str) -> dict:
        data = self._gh_json(
            ["gh", "api", "-H", "Accept: application/vnd.github+json", f"users/{login}"]
        )
        assert isinstance(data, dict)
        return {
            "login": data["login"],
            "html_url": data.get("html_url") or "",
            "id": data.get("id"),
            "name": data.get("name") or "",
            "bio": data.get("bio") or "",
        }

    def readme(self, repo: str) -> str:
        """只读拉取仓库 README（base64 解码为 Markdown）；失败抛 GhError。"""
        import base64

        data = self._gh_json(
            ["gh", "api", "-H", "Accept: application/vnd.github+json", f"repos/{repo}/readme"]
        )
        if not isinstance(data, dict) or not data.get("content"):
            raise GhError(f"no README content for {repo}")
        try:
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        except (ValueError, TypeError) as exc:
            raise GhError(f"README decode failed for {repo}: {exc}") from exc

    def list_public_repos(self, login: str, limit: int = 20) -> list[PublicRepo]:
        cap = max(1, min(limit, 100))
        data = self._gh_json(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github+json",
                f"users/{login}/repos?type=owner&sort=pushed&direction=desc&per_page={cap}",
            ],
            timeout=60.0,
        )
        assert isinstance(data, list)
        out: list[PublicRepo] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            repo = parse_public_repo(item)
            if repo.is_private or repo.is_fork or repo.archived or repo.disabled:
                continue
            if repo.pushed_at is None:
                continue
            out.append(repo)
            if len(out) >= cap:
                break
        return out

    def public_repo(self, name_with_owner: str) -> PublicRepo:
        data = self._gh_json(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github+json",
                f"repos/{name_with_owner}",
            ]
        )
        assert isinstance(data, dict)
        repo = parse_public_repo(data)
        if repo.is_private:
            raise GhError(f"repository is private: {name_with_owner}")
        return repo

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

    def search_issues(self, query: str, *, limit: int = 30) -> list[dict]:
        """跨仓库 issue 搜索（``search/issues``）；调用方自行拼 q（含 type:issue 等）。"""
        cap = max(1, min(limit, 50))
        q = quote(query, safe=":+ ")
        data = self._gh_json(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github+json",
                f"search/issues?q={q}&sort=updated&order=desc&per_page={cap}",
            ],
            timeout=60.0,
        )
        assert isinstance(data, dict)
        items = data.get("items") or []
        return [i for i in items if isinstance(i, dict)][:cap]

    def list_issue_comments(
        self, repo: str, number: int, *, limit: int = 30
    ) -> list[dict]:
        """Issue 评论（REST ``repos/{repo}/issues/{number}/comments``）。"""
        cap = max(1, min(limit, 100))
        data = self._gh_json(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github+json",
                f"repos/{repo}/issues/{number}/comments?per_page={cap}",
            ],
            timeout=45.0,
        )
        assert isinstance(data, list)
        return [c for c in data if isinstance(c, dict)][:cap]

    def list_user_issues(
        self, login: str, *, limit: int = 10
    ) -> list[dict]:
        """Issues authored by ``login`` (public search API)."""
        return self.search_issues(f"author:{login} type:issue", limit=limit)

    def list_user_pull_requests(
        self, login: str, *, limit: int = 10
    ) -> list[dict]:
        """PRs authored by ``login``."""
        return self.search_issues(f"author:{login} type:pr", limit=limit)

    def list_repo_releases(
        self, name_with_owner: str, *, limit: int = 5
    ) -> list[dict]:
        cap = max(1, min(limit, 20))
        data = self._gh_json(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github+json",
                f"repos/{name_with_owner}/releases?per_page={cap}",
            ],
            timeout=45.0,
        )
        assert isinstance(data, list)
        return [r for r in data if isinstance(r, dict)][:cap]

    def list_repo_issues(
        self, repo: str, *, state: str = "all", limit: int = 30
    ) -> list[dict]:
        """Issues in a repo (REST ``repos/{repo}/issues``，排除 PR 条目)。"""
        cap = max(1, min(limit, 100))
        data = self._gh_json(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github+json",
                f"repos/{repo}/issues?state={state}&sort=created&direction=desc&per_page={cap}",
            ],
            timeout=60.0,
        )
        assert isinstance(data, list)
        # issues 端点同时返回 PR；PR 带 pull_request 字段，单独走 list_repo_pull_requests。
        return [i for i in data if isinstance(i, dict) and "pull_request" not in i][:cap]

    def list_repo_pull_requests(
        self, repo: str, *, state: str = "all", limit: int = 30
    ) -> list[dict]:
        """PRs in a repo (REST ``repos/{repo}/pulls``)。"""
        cap = max(1, min(limit, 100))
        data = self._gh_json(
            [
                "gh",
                "api",
                "-H",
                "Accept: application/vnd.github+json",
                f"repos/{repo}/pulls?state={state}&sort=created&direction=desc&per_page={cap}",
            ],
            timeout=60.0,
        )
        assert isinstance(data, list)
        return [i for i in data if isinstance(i, dict)][:cap]

    def list_repo_discussions(self, repo: str, *, limit: int = 10) -> list[dict]:
        """Repo Discussions via GraphQL（REST 无 discussions 端点）。"""
        owner, _, name = repo.partition("/")
        cap = max(1, min(limit, 30))
        query = _DISCUSSIONS_QUERY.replace("{first}", str(cap))
        data = self._gh_json(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"owner={owner}",
                "-f",
                f"name={name}",
            ],
            timeout=60.0,
        )
        assert isinstance(data, dict)
        repo_node = (data.get("data") or {}).get("repository") or {}
        nodes = (repo_node.get("discussions") or {}).get("nodes") or []
        return [n for n in nodes if isinstance(n, dict)][:cap]
