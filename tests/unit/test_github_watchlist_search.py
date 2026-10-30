"""GitHub watchlist / cross-repo search / enrich tests."""

from __future__ import annotations

import json
from pathlib import Path

from finch.sources.connectors import DiscoveryContext
from finch.sources.connectors.github import GitHubConnector
from finch.sources.models import OpenCliResult, ResultKind
from finch.sources.models import Source as S
from finch.sources.opencli_gateway import OpenCliGateway
from finch.sources.orchestrator import DiscoveryOrchestrator, aggregate_state
from finch.storage.workspace import Workspace


class _FakeGh:
    def __init__(self) -> None:
        self.issue_calls: list[str] = []
        self.comment_calls: list[tuple[str, int]] = []

    def search_issues(self, query: str, *, limit: int = 30) -> list[dict]:
        self.issue_calls.append(query)
        return [
            {
                "id": 9,
                "number": 9,
                "title": "agent stuck",
                "body": "tools hang",
                "html_url": "https://github.com/a/b/issues/9",
                "updated_at": "2026-10-01T00:00:00Z",
                "state": "open",
                "user": {"login": "u1"},
                "repository_url": "https://api.github.com/repos/a/b",
            }
        ]

    def list_repo_issues(self, repo: str, *, state: str = "all", limit: int = 30) -> list[dict]:
        return [
            {
                "id": 1,
                "number": 1,
                "title": "broken import",
                "body": "fails",
                "html_url": f"https://github.com/{repo}/issues/1",
                "updated_at": "2026-10-20T00:00:00Z",
                "state": "open",
                "user": {"login": "u2"},
            }
        ]

    def list_issue_comments(self, repo: str, number: int, *, limit: int = 30) -> list[dict]:
        self.comment_calls.append((repo, number))
        return [{"user": {"login": "helper"}, "body": "try clearing cache"}]

    def list_repo_discussions(self, repo: str, *, limit: int = 10) -> list[dict]:
        return []


def test_fetch_issue_search_enriches_comments():
    conn = GitHubConnector(gh=_FakeGh())  # type: ignore[arg-type]
    result = conn.fetch_issue_search("agent stuck")
    assert result.kind == ResultKind.SUCCESS
    assert len(result.rows) == 1
    assert "try clearing cache" in result.rows[0]["body"]
    arts = conn.normalize(result)
    assert arts[0].metrics.get("state") == "open"
    assert "try clearing cache" in arts[0].text


def test_fetch_watchlist_and_orchestrator_partial(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.ensure()
    gh = _FakeGh()

    class _Conn(GitHubConnector):
        def __init__(self):
            super().__init__(gh=gh)  # type: ignore[arg-type]

        def probe(self, capabilities):
            from finch.sources.models import SourceStatus

            return SourceStatus.READY

        def fetch_creator(self, login):
            return OpenCliResult(
                rows=[],
                exit_code=1,
                kind=ResultKind.ERROR,
                stderr_summary="user boom",
            )

    def fake_run(argv, timeout):
        return {
            "ok": True,
            "exit_code": 0,
            "stdout": json.dumps([{"site": "github", "commands": ["user"]}]),
            "stderr": "",
        }

    orch = DiscoveryOrchestrator(
        ws,
        gateway=OpenCliGateway(run_fn=fake_run),
        connectors={S.GITHUB: _Conn()},
    )
    result = orch.sync_source(
        S.GITHUB,
        DiscoveryContext(
            queries=["baduser"],
            extra={"watchlist": ["a/b"], "issue_queries": ["agent stuck"]},
            limit=10,
        ),
        project_peers=False,
    )
    outcomes = {o.query: o.outcome for o in result.per_query}
    assert outcomes["baduser"] == "failed"
    assert outcomes["watch:a/b"] == "ran"
    assert outcomes["search:agent stuck"] == "ran"
    assert aggregate_state(result) == "partial_success"
    assert any("broken import" in a.text for a in result.artifacts)
    # Watermark persisted for watchlist incremental.
    raw = (tmp_path / "sources" / "cursors" / "github.yaml").read_text()
    assert "2026-10-20" in raw
