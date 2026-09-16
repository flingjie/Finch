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


class GitHubConnector:
    """GitHub 观察面：用户 / repo 摘要。实际 IO 通过 ``fetch`` 使用 GhClient。"""

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

    def plan(self, context: DiscoveryContext) -> list[OpenCliRequest]:
        # Placeholder requests for orchestrator bookkeeping; fetch() does real work.
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

    def _row_to_artifact(self, row: dict[str, Any], now: datetime) -> RawArtifact | None:
        login = str(row.get("login") or row.get("username") or "").strip()
        if not login:
            return None
        bio = str(row.get("bio") or row.get("name") or "")
        url = str(row.get("html_url") or f"https://github.com/{login}")
        fp = content_fingerprint(bio, url=url)
        return RawArtifact(
            artifact_id=artifact_id("github", "user", login.lower()),
            source=Source.GITHUB,
            source_type="user",
            source_id=login.lower(),
            canonical_url=url,
            author_identity=AuthorIdentity(
                platform="github",
                external_id=str(row.get("id") or login),
                handle=login,
            ),
            title=str(row.get("name") or login),
            text=bio,
            published_at=None,
            metrics={},
            retrieved_at=now,
            capture_method="gh",
            content_fingerprint=fp,
        )
