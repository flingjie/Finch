"""Unit tests for finch twitter CLI commands."""
from typer.testing import CliRunner

from finch.cli import app
from finch.settings import Settings, TwitterSettings


def _fake_settings(queries=None):
    return Settings(
        twitter=TwitterSettings(
            daily_limit=100,
            per_query_limit=20,
            queries=queries or [
                {"id": "agent_evals", "text": "test", "filter": "top", "priority": 5}
            ],
        ),
    )


def test_twitter_search_smoke(monkeypatch):
    captured = {}

    class FakeClient:
        def search(self, query, *, product="top", limit=20):
            captured["query"] = query
            captured["product"] = product
            captured["limit"] = limit
            return []

    monkeypatch.setattr("finch.cli.OpenCliClient", lambda: FakeClient())
    monkeypatch.setattr("finch.cli.load_settings", lambda: _fake_settings())
    r = CliRunner().invoke(app, ["twitter", "search", "--query-set", "agent_evals"])
    assert r.exit_code == 0


def test_twitter_search_with_product(monkeypatch):
    captured = {}

    class FakeClient:
        def search(self, query, *, product="top", limit=20):
            captured["product"] = product
            return []

    monkeypatch.setattr("finch.cli.OpenCliClient", lambda: FakeClient())
    monkeypatch.setattr("finch.cli.load_settings", lambda: _fake_settings())
    r = CliRunner().invoke(
        app, ["twitter", "search", "--query-set", "agent_evals", "--product", "live"]
    )
    assert r.exit_code == 0
    assert captured.get("product") == "live"


def test_twitter_search_returns_preview(monkeypatch):
    from finch.twitter.models import Tweet

    class FakeClient:
        def search(self, query, *, product="top", limit=20):
            return [
                Tweet(id="1", author="alice", text="hello world", url="https://x.com/a/1"),
            ]

    monkeypatch.setattr("finch.cli.OpenCliClient", lambda: FakeClient())
    monkeypatch.setattr("finch.cli.load_settings", lambda: _fake_settings())
    r = CliRunner().invoke(app, ["twitter", "search", "--query-set", "agent_evals"])
    assert r.exit_code == 0
    assert "1" in r.output
    assert "alice" in r.output


def test_twitter_bookmarks_smoke(monkeypatch):
    class FakeClient:
        def bookmarks(self, *, limit=20):
            return []

    monkeypatch.setattr("finch.cli.OpenCliClient", lambda: FakeClient())
    monkeypatch.setattr("finch.cli.load_settings", lambda: _fake_settings())
    r = CliRunner().invoke(app, ["twitter", "import-bookmarks", "--limit", "10"])
    assert r.exit_code == 0
