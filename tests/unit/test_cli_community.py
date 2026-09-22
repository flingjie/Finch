"""Unit tests for the finch community CLI (community-scout)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.settings import Paths, Settings


def _settings(tmp_path) -> Settings:
    return Settings(paths=Paths(var_dir=tmp_path))


def _card(tmp_path) -> str:
    card = tmp_path / "card.yaml"
    card.write_text(
        "\n".join(
            [
                "community: Temporal Community",
                "platforms:",
                "  - GitHub Discussions",
                "fit_score: 86",
                "why_fit:",
                "  - 与 durable execution 相关",
                "recent_evidence:",
                "  - topic: workflow failure recovery",
                "    relevance: 与 failure replay 相关",
                "    url: https://example.com/d/1",
                "people:",
                "  - name: Core Builder A",
                "    reason: 持续分享 durable execution 实践",
                "entry_point:",
                "  discussion: 一个活跃的具体讨论",
                "  suggested_angle: 分享失败回放设计",
                "first_contribution:",
                "  type: example",
                "  proposal: 提供一个 workflow failure replay 示例",
                "risks:",
                "  - 英文交流成本中等",
            ]
        ),
        encoding="utf-8",
    )
    return str(card)


def test_cli_save_inspect_feedback_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    card = _card(tmp_path)

    r = CliRunner().invoke(app, ["community", "save", "--file", card, "--json"])
    assert r.exit_code == 0, r.output
    saved = json.loads(r.output)
    assert saved["id"].startswith("comm_")
    assert saved["name"] == "Temporal Community"
    assert saved["fit_score"] == 86

    r = CliRunner().invoke(app, ["community", "inspect", saved["id"], "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["entry_point"]["discussion"] == "一个活跃的具体讨论"

    r = CliRunner().invoke(
        app, ["community", "feedback", saved["id"], "--result", "joined", "--json"]
    )
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["result"] == "joined"

    r = CliRunner().invoke(app, ["community", "list", "--json"])
    assert r.exit_code == 0, r.output
    rows = json.loads(r.output)
    assert len(rows) == 1
    assert rows[0]["profile"]["id"] == saved["id"]
    assert rows[0]["feedback"]["result"] == "joined"


def test_cli_feedback_rejects_invalid_result(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    r = CliRunner().invoke(app, ["community", "feedback", "comm_x", "--result", "bogus"])
    assert r.exit_code == 1
    assert "invalid --result" in r.output


def test_cli_save_rejects_missing_community_field(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    bad = tmp_path / "bad.yaml"
    bad.write_text("fit_score: 10\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["community", "save", "--file", str(bad)])
    assert r.exit_code == 1
    assert "community" in r.output


def test_cli_discover_snapshots_context(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    r = CliRunner().invoke(app, ["community", "discover", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert "-W" in payload["week"]
    assert "interests" in payload["context"]
    assert payload["report"] is None
