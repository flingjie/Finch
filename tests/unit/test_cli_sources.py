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


def test_sources_sync_json_partial_state(tmp_path, monkeypatch):
    settings = Settings(paths=Paths(var_dir=tmp_path))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    def fake_sync_all(self, **kwargs):
        return [
            SyncResult(
                source=Source.REDDIT,
                status=SourceStatus.READY,
                raw_count=5,
                mode="query",
                ran_queries=["a", "b"],
                per_query=[
                    QueryOutcome(query="a", outcome="failed", kind=ResultKind.TIMEOUT),
                    QueryOutcome(query="b", outcome="ran", raw_count=5, kind=ResultKind.SUCCESS),
                ],
                started_at="2026-10-30T10:00:00+00:00",
            )
        ]

    monkeypatch.setattr(DiscoveryOrchestrator, "sync_all", fake_sync_all)
    r = CliRunner().invoke(app, ["sources", "sync", "--all", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    reddit = next(row for row in payload if row["source"] == "reddit")
    assert reddit["state"] == "partial_success"
    assert reddit["per_query"][0]["outcome"] == "failed"
    assert reddit["per_query"][0]["partial"] is False


def test_products_daily_refresh_prints_report(tmp_path, monkeypatch):
    from finch.products.discovery import DiscoveryRunReport, ProductDiscoveryResult

    settings = Settings(paths=Paths(var_dir=tmp_path))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "create_runner", lambda _llm: None)

    def fake_run_product_discovery(settings, **kwargs):
        return ProductDiscoveryResult(
            run_id="r1",
            products=[],
            report=DiscoveryRunReport(
                per_source_state={"twitter": "failed"},
                raw_count=0, normalized_count=0, filtered_count=0, product_count=0,
                card_fill_success=0, card_fill_failed=0, elapsed_seconds=0.1,
            ),
        )

    monkeypatch.setattr(cli, "run_product_discovery", fake_run_product_discovery)
    r = CliRunner().invoke(app, ["products", "daily", "--refresh"])
    assert r.exit_code == 0, r.output
    assert "raw=0" in r.output
    assert "twitter:failed" in r.output
