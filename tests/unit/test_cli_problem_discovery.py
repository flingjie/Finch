"""CLI smoke：finch problem-discovery list / show / add（无真实网络 / LLM）。"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.problem_discovery.models import Problem, ProblemScenario
from finch.problem_discovery.repository import ProblemRepository
from finch.settings import Paths, Settings
from finch.storage.workspace import Workspace


def _settings(tmp_path) -> Settings:
    return Settings(paths=Paths(var_dir=tmp_path))


def _seed_problem(ws: Workspace) -> str:
    repo = ProblemRepository(ws)
    repo.upsert(
        Problem(
            problem_id="prob_x",
            title="Agent 失败后难以复现",
            user_type="Agent 应用开发者",
            scenario=ProblemScenario(task="调试 Agent 失败"),
            next_action="interview",
        )
    )
    return "prob_x"


def test_problem_discovery_list_json(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_problem(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["problem-discovery", "list", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert [p["problem_id"] for p in payload] == ["prob_x"]


def test_problem_discovery_list_compact(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_problem(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["problem-discovery", "list"])
    assert r.exit_code == 0, r.output
    assert "Agent 失败后难以复现" in r.output
    assert "查看：uv run finch problem-discovery show prob_x" in r.output
    assert "场景：任务=调试 Agent 失败" not in r.output


def test_problem_discovery_list_full(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_problem(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["problem-discovery", "list", "--full"])
    assert r.exit_code == 0, r.output
    assert "场景：任务=调试 Agent 失败" in r.output


def test_problem_discovery_show_json(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_problem(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["problem-discovery", "show", "prob_x", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["title"] == "Agent 失败后难以复现"


def test_problem_discovery_show_missing_exits_1(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["problem-discovery", "show", "prob_missing", "--json"])
    assert r.exit_code == 1


def test_problem_discovery_add_url(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["problem-discovery", "add", "--url", "https://x.com/a/1"])
    assert r.exit_code == 0, r.output
    assert "disc_" in r.output
