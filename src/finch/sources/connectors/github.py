"""GitHub Source Connector — 执行仍走 GhClient，不经 opencli gh 写路径。"""

from __future__ import annotations

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

_MAX_REPOS = 5
_MAX_COMMITS_PER_REPO = 5
_MAX_ISSUES = 5
_MAX_PRS = 5
_MAX_RELEASES_PER_REPO = 2


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
            metrics={},
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
