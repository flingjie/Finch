"""CLI smoke for finch sources sync report."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.settings import Paths, Settings
from finch.sources.models import ResultKind, Source, SourceStatus
from finch.sources.orchestrator import (
    DiscoveryOrchestrator,
    QueryOutcome,
    SyncResult,
)


def test_sources_sync_single_disabled_source_not_executed(tmp_path, monkeypatch):
    settings = Settings(paths=Paths(var_dir=tmp_path))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["sources", "sync", "--source", "github"])
    assert r.exit_code == 0, r.output
    assert "github: not_executed [disabled]" in r.output


def test_sources_sync_json(tmp_path, monkeypatch):
    settings = Settings(paths=Paths(var_dir=tmp_path))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    def fake_sync_all(self, **kwargs):
        return [
            SyncResult(
                source=Source.REDDIT,
                status=SourceStatus.READY,
                raw_count=1,
                mode="query",
                ran_queries=["agent stuck"],
                per_query=[
                    QueryOutcome(
                        query="agent stuck", outcome="ran", raw_count=1, kind=ResultKind.SUCCESS
                    )
                ],
                started_at="2026-10-30T10:00:00+00:00",
            )
        ]

    monkeypatch.setattr(DiscoveryOrchestrator, "sync_all", fake_sync_all)
    r = CliRunner().invoke(app, ["sources", "sync", "--all", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    reddit = next(row for row in payload if row["source"] == "reddit")
    assert reddit["mode"] == "query"
    assert reddit["state"] == "success"
    assert reddit["per_query"][0]["outcome"] == "ran"
