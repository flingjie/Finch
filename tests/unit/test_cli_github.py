"""Unit tests for finch github CLI commands."""
from typer.testing import CliRunner

from finch.cli import app


def test_github_sync_smoke(monkeypatch):
    captured = {}

    class FakeGh:
        def list_commits(self, repo, since, per_page=100):
            captured["since"] = since
            return []

    monkeypatch.setattr("finch.cli.GhClient", lambda: FakeGh())
    monkeypatch.setattr("finch.cli.load_settings", lambda: None)
    r = CliRunner().invoke(app, ["github", "sync", "--since", "72h"])
    assert r.exit_code == 0
    assert captured["since"]
