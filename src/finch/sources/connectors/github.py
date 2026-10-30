"""GitHub Source Connector — 执行仍走 GhClient，不经 opencli gh 写路径。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from finch.github.gh_client import GhClient
from finch.sources.connectors import DiscoveryContext
from finch.sources.fingerprint import artifact_id, content_fingerprint
from finch.sources.models import (
    AuthorIdentity,
    OpenCliCapabilities,
    OpenCliRequest,
    OpenCliResult,
    RawArtifact,
    ResultKind,
    Source,
    SourceStatus,
)


@dataclass
class RepoFeedbackResult:
    """仓库反馈抓取结果：工件 + 端点级错误（失败不伪装成空成功）。"""

    artifacts: list[RawArtifact] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def stderr_summary(self) -> str:
        return "; ".join(self.errors)[:300]

_MAX_REPOS = 5
_MAX_COMMITS_PER_REPO = 5
_MAX_ISSUES = 5
_MAX_PRS = 5
_MAX_RELEASES_PER_REPO = 2
# 反馈研究有界抓取上限（区别于 creator 深度的 5 条）。
_MAX_REPO_ISSUES = 30
_MAX_REPO_PRS = 30
_MAX_REPO_DISCUSSIONS = 10


class GitHubConnector:
    """GitHub 观察面：用户 / repo / commit / issue / PR / release。"""

    source = Source.GITHUB

    def __init__(self, gh: GhClient | None = None) -> None:
        self.gh = gh or GhClient()

    def probe(self, capabilities: OpenCliCapabilities) -> SourceStatus:
        # gh binary is authoritative; opencli may also list gh hub.
        ver = self.gh.version()
        if not ver:
            return SourceStatus.UNAVAILABLE
        auth = self.gh.auth_status()
        if not auth.get("ok"):
            return SourceStatus.AUTH_REQUIRED
        return SourceStatus.READY

    def plan(
        self,
        context: DiscoveryContext,
        capabilities: OpenCliCapabilities | None = None,
    ) -> list[OpenCliRequest]:
        # Placeholder requests for orchestrator bookkeeping; fetch() does real work.
        _ = capabilities
        reqs: list[OpenCliRequest] = []
        for q in context.queries:
            reqs.append(
                OpenCliRequest(
                    surface="github",
                    command="user",
                    args=(q,),
                    timeout_seconds=30,
                    use_browser_lock=False,
                )
            )
        return reqs

    def normalize(self, result: OpenCliResult) -> list[RawArtifact]:
        now = datetime.now(UTC)
        out: list[RawArtifact] = []
        for row in result.rows:
            art = self._row_to_artifact(row, now)
            if art is not None:
                out.append(art)
        return out

    def fetch_user(self, login: str) -> OpenCliResult:
        """通过 GhClient 拉取用户，包装为 OpenCliResult。"""
        try:
            data = self.gh.user(login)
        except Exception as exc:  # noqa: BLE001
            return OpenCliResult(
                rows=[],
                exit_code=1,
                kind=ResultKind.ERROR,
                stderr_summary=str(exc)[:300],
            )
        if not data:
            return OpenCliResult(rows=[], exit_code=66, kind=ResultKind.EMPTY)
        row = data if isinstance(data, dict) else {"login": login, "raw": data}
        return OpenCliResult(rows=[row], exit_code=0, kind=ResultKind.SUCCESS)

    def fetch_creator(self, login: str) -> OpenCliResult:
        """有界拉取用户创造行为：profile + repos + commits + issues/PRs + releases。"""
        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        try:
            user = self.gh.user(login)
            if user:
                rows.append({"_kind": "user", **user})
        except Exception as exc:  # noqa: BLE001
            return OpenCliResult(
                rows=[],
                exit_code=1,
                kind=ResultKind.ERROR,
                stderr_summary=str(exc)[:300],
            )

        login_l = login.lower()
        author = {
            "login": user.get("login") or login,
            "id": user.get("id") or login_l,
            "html_url": user.get("html_url") or f"https://github.com/{login}",
        }

        repos = []
        try:
            repos = self.gh.list_public_repos(login, limit=_MAX_REPOS)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"repos: {exc}"[:120])

        for repo in repos:
            rows.append(
                {
                    "_kind": "repo",
                    "full_name": repo.name_with_owner,
                    "html_url": repo.url,
                    "description": repo.description,
                    "pushed_at": repo.pushed_at.isoformat() if repo.pushed_at else None,
                    "owner_login": repo.owner_login or author["login"],
                    "owner_id": author["id"],
                }
            )
            try:
                commits = self.gh.list_commits_newest_first(
                    repo.name_with_owner,
                    per_page=_MAX_COMMITS_PER_REPO,
                    max_commits=_MAX_COMMITS_PER_REPO,
                )
                for c in commits:
                    rows.append(
                        {
                            "_kind": "commit",
                            "sha": c.sha,
                            "message": c.message,
                            "html_url": c.html_url,
                            "author_date": c.author_date.isoformat(),
                            "repo": repo.name_with_owner,
                            "login": author["login"],
                            "id": author["id"],
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"commits {repo.name_with_owner}: {exc}"[:120])

            try:
                releases = self.gh.list_repo_releases(
                    repo.name_with_owner, limit=_MAX_RELEASES_PER_REPO
                )
                for rel in releases:
                    rows.append(
                        {
                            "_kind": "release",
                            "id": rel.get("id") or rel.get("tag_name"),
                            "tag_name": rel.get("tag_name") or "",
                            "name": rel.get("name") or rel.get("tag_name") or "",
                            "body": rel.get("body") or "",
                            "html_url": rel.get("html_url") or "",
                            "published_at": rel.get("published_at"),
                            "repo": repo.name_with_owner,
                            "login": author["login"],
                            "author_id": author["id"],
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"releases {repo.name_with_owner}: {exc}"[:120])

        try:
            for issue in self.gh.list_user_issues(login, limit=_MAX_ISSUES):
                rows.append(
                    {
                        "_kind": "issue",
                        "id": issue.get("id") or issue.get("number"),
                        "number": issue.get("number"),
                        "title": issue.get("title") or "",
                        "body": issue.get("body") or "",
                        "html_url": issue.get("html_url") or "",
                        "updated_at": issue.get("updated_at"),
                        "login": author["login"],
                        "author_id": author["id"],
                    }
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"issues: {exc}"[:120])

        try:
            for pr in self.gh.list_user_pull_requests(login, limit=_MAX_PRS):
                rows.append(
                    {
                        "_kind": "pull_request",
                        "id": pr.get("id") or pr.get("number"),
                        "number": pr.get("number"),
                        "title": pr.get("title") or "",
                        "body": pr.get("body") or "",
                        "html_url": pr.get("html_url") or "",
                        "updated_at": pr.get("updated_at"),
                        "login": author["login"],
                        "author_id": author["id"],
                    }
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"prs: {exc}"[:120])

        if not rows:
            return OpenCliResult(
                rows=[],
                exit_code=66,
                kind=ResultKind.EMPTY,
                stderr_summary="; ".join(errors)[:300] if errors else None,
            )
        return OpenCliResult(
            rows=rows,
            exit_code=0,
            kind=ResultKind.SUCCESS,
            stderr_summary="; ".join(errors)[:300] if errors else None,
        )

    def fetch_issue_search(self, query: str, *, limit: int = 30) -> OpenCliResult:
        """跨仓库 issue 搜索；附带评论摘录与关联 PR 状态（若有）。"""
        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        try:
            q = query if "type:" in query else f"{query} type:issue"
            items = self.gh.search_issues(q, limit=limit)
        except Exception as exc:  # noqa: BLE001
            return OpenCliResult(
                rows=[],
                exit_code=1,
                kind=ResultKind.ERROR,
                stderr_summary=str(exc)[:300],
            )
        for item in items:
            try:
                rows.append(self._enrich_issue_row(item))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"enrich: {exc}"[:120])
        if not rows and errors:
            return OpenCliResult(
                rows=[],
                exit_code=1,
                kind=ResultKind.ERROR,
                stderr_summary="; ".join(errors)[:300],
            )
        if not rows:
            return OpenCliResult(rows=[], exit_code=66, kind=ResultKind.EMPTY)
        return OpenCliResult(
            rows=rows,
            exit_code=0,
            kind=ResultKind.SUCCESS,
            stderr_summary="; ".join(errors)[:300] if errors else None,
        )

    def fetch_watchlist_repo(
        self, owner_repo: str, *, limit: int = 30, since: str | None = None
    ) -> OpenCliResult:
        """观察清单仓库：issues（含评论 / PR 关联）+ discussions。"""
        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        try:
            issues = self.gh.list_repo_issues(owner_repo, limit=limit)
        except Exception as exc:  # noqa: BLE001
            return OpenCliResult(
                rows=[],
                exit_code=1,
                kind=ResultKind.ERROR,
                stderr_summary=str(exc)[:300],
            )
        for item in issues:
            updated = str(item.get("updated_at") or "")
            if since and updated and updated <= since:
                continue
            try:
                rows.append(self._enrich_issue_row(item, default_repo=owner_repo))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"issue enrich: {exc}"[:120])
        try:
            for item in self.gh.list_repo_discussions(owner_repo, limit=min(limit, 10)):
                rows.append(
                    {
                        "_kind": "discussion",
                        "id": item.get("id") or item.get("url"),
                        "title": item.get("title") or "",
                        "body": item.get("body") or "",
                        "html_url": item.get("url") or "",
                        "updated_at": item.get("updatedAt") or item.get("createdAt"),
                        "state": "open",
                        "login": ((item.get("author") or {}) or {}).get("login") or "",
                        "repo": owner_repo,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"discussion: {exc}"[:120])
        if not rows and errors:
            return OpenCliResult(
                rows=[],
                exit_code=1,
                kind=ResultKind.ERROR,
                stderr_summary="; ".join(errors)[:300],
            )
        if not rows:
            return OpenCliResult(
                rows=[],
                exit_code=66,
                kind=ResultKind.EMPTY,
                stderr_summary="; ".join(errors)[:300] if errors else None,
            )
        return OpenCliResult(
            rows=rows,
            exit_code=0,
            kind=ResultKind.SUCCESS,
            stderr_summary="; ".join(errors)[:300] if errors else None,
        )

    def _enrich_issue_row(
        self, item: dict[str, Any], *, default_repo: str = ""
    ) -> dict[str, Any]:
        """把 issue 字典扩成 normalize 可用行：评论摘录 + 关联 PR URL/state。"""
        repo = default_repo
        if not repo:
            repo_url = str(item.get("repository_url") or "")
            # https://api.github.com/repos/owner/name
            if "/repos/" in repo_url:
                repo = repo_url.split("/repos/", 1)[1].strip("/")
            html = str(item.get("html_url") or "")
            if not repo and "github.com/" in html:
                parts = html.split("github.com/", 1)[1].split("/")
                if len(parts) >= 2:
                    repo = f"{parts[0]}/{parts[1]}"
        number = int(item.get("number") or 0)
        body = str(item.get("body") or "")
        comments_text = ""
        if repo and number:
            try:
                comments = self.gh.list_issue_comments(repo, number, limit=20)
                chunks: list[str] = []
                for c in comments:
                    author = ""
                    user = c.get("user") or {}
                    if isinstance(user, dict):
                        author = str(user.get("login") or "")
                    chunks.append(f"@{author}: {str(c.get('body') or '')[:500]}")
                if chunks:
                    comments_text = "\n\n--- comments ---\n" + "\n\n".join(chunks)
            except Exception:  # noqa: BLE001
                comments_text = ""
        pr = item.get("pull_request") if isinstance(item.get("pull_request"), dict) else None
        pr_url = str((pr or {}).get("html_url") or (pr or {}).get("url") or "")
        if (pr or {}).get("merged_at"):
            pr_state = "merged"
        else:
            pr_state = str(item.get("state") or "")
        user = item.get("user") or {}
        login = (
            str(user.get("login") or "")
            if isinstance(user, dict)
            else str(item.get("login") or "")
        )
        return {
            "_kind": "issue",
            "id": item.get("id") or f"{repo}#{number}",
            "number": number,
            "title": item.get("title") or "",
            "body": body + comments_text,
            "html_url": item.get("html_url") or "",
            "updated_at": item.get("updated_at"),
            "state": item.get("state") or "",
            "login": login,
            "author_id": login.lower(),
            "repo": repo,
            "pr_url": pr_url,
            "pr_state": pr_state,
        }

    def fetch_repo_feedback(self, owner_repo: str) -> RepoFeedbackResult:
        """有界抓取产品仓库的 issue / PR / discussion 作为用户反馈候选。只读。

        端点异常记入 ``errors``，不吞成「空成功」；调用方用 ``artifacts`` 与
        ``stderr_summary`` 区分部分失败。
        """
        now = datetime.now(UTC)
        out: list[RawArtifact] = []
        errors: list[str] = []
        for source_type, fetch, limit in (
            ("issue", self.gh.list_repo_issues, _MAX_REPO_ISSUES),
            ("pull_request", self.gh.list_repo_pull_requests, _MAX_REPO_PRS),
        ):
            try:
                items = fetch(owner_repo, limit=limit)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{source_type}: {exc}"[:120])
                items = []
            for item in items:
                art = self._feedback_item_artifact(item, now, source_type=source_type)
                if art is not None:
                    out.append(art)
        try:
            discussions = self.gh.list_repo_discussions(owner_repo, limit=_MAX_REPO_DISCUSSIONS)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"discussion: {exc}"[:120])
            discussions = []
        for item in discussions:
            art = self._discussion_artifact(item, now)
            if art is not None:
                out.append(art)
        return RepoFeedbackResult(artifacts=out, errors=errors)

    def _feedback_item_artifact(
        self, row: dict[str, Any], now: datetime, *, source_type: str
    ) -> RawArtifact | None:
        number = row.get("number")
        title = str(row.get("title") or "")
        url = str(row.get("html_url") or "")
        user = row.get("user") or {}
        login = str(user.get("login") or "") if isinstance(user, dict) else ""
        body = str(row.get("body") or "")
        text = f"{title}\n\n{body}".strip()
        sid = str(row.get("id") or f"{source_type}-{number}")
        fp = content_fingerprint(text, url=url)
        published = self._parse_ts(row.get("created_at") or row.get("updated_at"))
        return RawArtifact(
            artifact_id=artifact_id("github", source_type, sid),
            source=Source.GITHUB,
            source_type=source_type,
            source_id=sid,
            canonical_url=url,
            author_identity=AuthorIdentity(
                platform="github", external_id=login.lower(), handle=login
            ),
            title=title or None,
            text=text,
            published_at=published,
            metrics={},
            retrieved_at=now,
            capture_method="gh",
            content_fingerprint=fp,
        )

    def _discussion_artifact(self, row: dict[str, Any], now: datetime) -> RawArtifact | None:
        title = str(row.get("title") or "")
        url = str(row.get("url") or "")
        author = row.get("author") or {}
        login = str(author.get("login") or "") if isinstance(author, dict) else ""
        body = str(row.get("body") or "")
        text = f"{title}\n\n{body}".strip()
        sid = str(row.get("id") or url or "")
        fp = content_fingerprint(text, url=url)
        published = self._parse_ts(row.get("createdAt"))
        return RawArtifact(
            artifact_id=artifact_id("github", "discussion", sid),
            source=Source.GITHUB,
            source_type="discussion",
            source_id=sid,
            canonical_url=url,
            author_identity=AuthorIdentity(
                platform="github", external_id=login.lower(), handle=login
            ),
            title=title or None,
            text=text,
            published_at=published,
            metrics={},
            retrieved_at=now,
            capture_method="gh",
            content_fingerprint=fp,
        )

    @staticmethod
    def _parse_ts(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    def _row_to_artifact(self, row: dict[str, Any], now: datetime) -> RawArtifact | None:
        kind = str(row.get("_kind") or "user")
        if kind == "user":
            return self._user_artifact(row, now)
        if kind == "repo":
            return self._repo_artifact(row, now)
        if kind == "commit":
            return self._commit_artifact(row, now)
        if kind == "issue":
            return self._issue_artifact(row, now, source_type="issue")
        if kind == "pull_request":
            return self._issue_artifact(row, now, source_type="pull_request")
        if kind == "release":
            return self._release_artifact(row, now)
        return self._user_artifact(row, now)

    def _user_artifact(self, row: dict[str, Any], now: datetime) -> RawArtifact | None:
        login = str(row.get("login") or row.get("username") or "").strip()
        if not login:
            return None
        bio = str(row.get("bio") or "")
        name = str(row.get("name") or login)
        url = str(row.get("html_url") or f"https://github.com/{login}")
        text = f"{name}\n{bio}".strip()
        fp = content_fingerprint(text, url=url)
        return RawArtifact(
            artifact_id=artifact_id("github", "user", login.lower()),
            source=Source.GITHUB,
            source_type="user",
            source_id=login.lower(),
            canonical_url=url,
            author_identity=AuthorIdentity(
                platform="github",
                external_id=login.lower(),
                handle=login,
            ),
            title=name,
            text=text,
            published_at=None,
            metrics={},
            retrieved_at=now,
            capture_method="gh",
            content_fingerprint=fp,
        )

    def _repo_artifact(self, row: dict[str, Any], now: datetime) -> RawArtifact | None:
        full = str(row.get("full_name") or "").strip()
        if not full:
            return None
        login = str(row.get("owner_login") or full.split("/")[0])
        desc = str(row.get("description") or "")
        url = str(row.get("html_url") or f"https://github.com/{full}")
        text = f"{full}\n{desc}".strip()
        fp = content_fingerprint(text, url=url)
        published = None
        if row.get("pushed_at"):
            try:
                published = datetime.fromisoformat(str(row["pushed_at"]).replace("Z", "+00:00"))
            except ValueError:
                published = None
        return RawArtifact(
            artifact_id=artifact_id("github", "repo", full.lower()),
            source=Source.GITHUB,
            source_type="repo",
            source_id=full.lower(),
            canonical_url=url,
            author_identity=AuthorIdentity(
                platform="github",
                external_id=login.lower(),
                handle=login,
            ),
            title=full,
            text=text,
            published_at=published,
            metrics={},
            retrieved_at=now,
            capture_method="gh",
            content_fingerprint=fp,
        )

    def _commit_artifact(self, row: dict[str, Any], now: datetime) -> RawArtifact | None:
        sha = str(row.get("sha") or "").strip()
        repo = str(row.get("repo") or "").strip()
        if not sha or not repo:
            return None
        login = str(row.get("login") or repo.split("/")[0])
        msg = str(row.get("message") or "")
        url = str(row.get("html_url") or f"https://github.com/{repo}/commit/{sha}")
        sid = f"{repo.lower()}@{sha[:12]}"
        fp = content_fingerprint(msg, url=url)
        published = None
        if row.get("author_date"):
            try:
                published = datetime.fromisoformat(str(row["author_date"]).replace("Z", "+00:00"))
            except ValueError:
                published = None
        return RawArtifact(
            artifact_id=artifact_id("github", "commit", sid),
            source=Source.GITHUB,
            source_type="commit",
            source_id=sid,
            canonical_url=url,
            author_identity=AuthorIdentity(
                platform="github",
                external_id=login.lower(),
                handle=login,
            ),
            title=msg.split("\n", 1)[0][:120] or sha[:8],
            text=msg,
            published_at=published,
            metrics={},
            retrieved_at=now,
            capture_method="gh",
            content_fingerprint=fp,
        )

    def _issue_artifact(
        self, row: dict[str, Any], now: datetime, *, source_type: str
    ) -> RawArtifact | None:
        number = row.get("number")
        title = str(row.get("title") or "")
        url = str(row.get("html_url") or "")
        if not url and number is None:
            return None
        login = str(row.get("login") or "")
        body = str(row.get("body") or "")
        text = f"{title}\n\n{body}".strip()
        sid = str(row.get("id") or f"{source_type}-{number}")
        fp = content_fingerprint(text, url=url)
        published = None
        if row.get("updated_at"):
            try:
                published = datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00"))
            except ValueError:
                published = None
        metrics: dict[str, Any] = {}
        if row.get("state"):
            metrics["state"] = str(row.get("state"))
        if row.get("repo"):
            metrics["repo"] = str(row.get("repo"))
        if row.get("pr_url"):
            metrics["pr_url"] = str(row.get("pr_url"))
        if row.get("pr_state"):
            metrics["pr_state"] = str(row.get("pr_state"))
        if row.get("updated_at"):
            metrics["updated_at"] = str(row.get("updated_at"))
        return RawArtifact(
            artifact_id=artifact_id("github", source_type, sid),
            source=Source.GITHUB,
            source_type=source_type,
            source_id=sid,
            canonical_url=url,
            author_identity=AuthorIdentity(
                platform="github",
                external_id=login.lower(),
                handle=login,
            ),
            title=title or None,
            text=text,
            published_at=published,
            metrics=metrics,
            retrieved_at=now,
            capture_method="gh",
            content_fingerprint=fp,
        )

    def _release_artifact(self, row: dict[str, Any], now: datetime) -> RawArtifact | None:
        tag = str(row.get("tag_name") or row.get("id") or "").strip()
        repo = str(row.get("repo") or "").strip()
        if not tag:
            return None
        login = str(row.get("login") or (repo.split("/")[0] if repo else ""))
        name = str(row.get("name") or tag)
        body = str(row.get("body") or "")
        url = str(row.get("html_url") or "")
        text = f"{name}\n\n{body}".strip()
        sid = f"{repo.lower()}:{tag}" if repo else tag
        fp = content_fingerprint(text, url=url)
        published = None
        if row.get("published_at"):
            try:
                published = datetime.fromisoformat(
                    str(row["published_at"]).replace("Z", "+00:00")
                )
            except ValueError:
                published = None
        return RawArtifact(
            artifact_id=artifact_id("github", "release", sid),
            source=Source.GITHUB,
            source_type="release",
            source_id=sid,
            canonical_url=url,
            author_identity=AuthorIdentity(
                platform="github",
                external_id=login.lower(),
                handle=login,
            ),
            title=name,
            text=text,
            published_at=published,
            metrics={},
            retrieved_at=now,
            capture_method="gh",
            content_fingerprint=fp,
        )
