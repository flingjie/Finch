"""CLI smoke for finch repos list/export (no live opencli)."""

import json
from datetime import UTC, datetime

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.repos.models import RepoRecord, RunStatus, TweetRecord
from finch.repos.repository import RepoDiscoveryRepository
from finch.repos.service import make_run_id
from finch.settings import Paths, Settings
from finch.storage.workspace import Workspace


def _settings(tmp_path) -> Settings:
    return Settings(paths=Paths(var_dir=tmp_path))


def test_repos_list_and_export(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    end = datetime(2026, 10, 2, tzinfo=UTC)
    start = datetime(2026, 10, 1, tzinfo=UTC)
    run_id = make_run_id(end)
    store = RepoDiscoveryRepository(ws)
    from finch.repos.models import DiscoveryRun, QueryProgress, QueryStatus

    store.save_run(
        DiscoveryRun(
            run_id=run_id,
            window_start=start,
            window_end=end,
            status=RunStatus.COMPLETED,
            queries=[QueryProgress(query_id="q0", text="x", status=QueryStatus.COMPLETED)],
            tweet_count=1,
            repo_count=1,
        )
    )
    store.save_tweet(
        run_id,
        TweetRecord(
            tweet_id="1",
            likes=3,
            repo_refs=["acme/widget"],
            published_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
            url="https://x.com/a/status/1",
            text="https://github.com/acme/widget",
        ),
    )
    store.save_repo(
        run_id,
        RepoRecord(
            key="acme/widget",
            owner="acme",
            name="widget",
            tweet_ids=["1"],
            source_share_urls=["https://x.com/a/status/1"],
            stars=10,
            description="",
        ),
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["repos", "list", "--run", run_id, "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["total"] == 1
    assert payload["entries"][0]["repo_key"] == "acme/widget"
    assert "第" not in r.output  # json path

    out = tmp_path / "out.csv"
    r2 = CliRunner().invoke(
        app,
        ["repos", "export", "--run", run_id, "--format", "csv", "--output", str(out)],
    )
    assert r2.exit_code == 0, r2.output
    text = out.read_text(encoding="utf-8")
    assert "acme/widget" in text
    assert "repo_key" in text
