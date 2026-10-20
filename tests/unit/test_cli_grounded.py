"""CLI：finch grounded 命令的落库与守卫。"""

import json

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.settings import GroundedSettings, Paths, Settings
from finch.storage.repositories import GroundedSessionRepository
from finch.storage.workspace import Workspace


def _patch(monkeypatch, tmp_path, *, enabled=True):
    settings = Settings(
        paths=Paths(var_dir=tmp_path),
        grounded=GroundedSettings(enabled=enabled),
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    return settings


def test_start_idempotent(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    args = ["grounded", "start", "--text", "同事说他最近天天加班"]
    r1 = CliRunner().invoke(app, args)
    assert r1.exit_code == 0, r1.output
    sid1 = r1.output.strip().split()[1]
    r2 = CliRunner().invoke(app, args)
    assert r2.exit_code == 0, r2.output
    sid2 = r2.output.strip().split()[1]
    assert sid1 == sid2
    assert len(GroundedSessionRepository(Workspace(tmp_path)).list_all()) == 1


def test_start_exactly_one_guard(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r = CliRunner().invoke(app, ["grounded", "start"])
    assert r.exit_code == 1
    assert "exactly one" in r.output


def test_start_json_shape(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r = CliRunner().invoke(app, ["grounded", "start", "--text", "情况", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["id"].startswith("gs_")
    assert data["status"] == "draft"
    assert data["situation"] == "情况"


def test_full_flow_to_reviewed(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r = CliRunner().invoke(app, ["grounded", "start", "--text", "情况"])
    sid = r.output.strip().split()[1]
    r = CliRunner().invoke(app, ["grounded", "reflect", sid, "--understanding", "原理解"])
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(
        app, ["grounded", "action", sid, "--action", "ask", "--action-draft", "草稿"]
    )
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(app, ["grounded", "record-action", sid])
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(
        app,
        ["grounded", "feedback", sid, "--text", "反馈", "--evidence", "user_reported",
         "--revised-understanding", "修正理解"],
    )
    assert r.exit_code == 0, r.output
    assert "reviewed" in r.output
    r = CliRunner().invoke(app, ["grounded", "show", sid, "--json"])
    data = json.loads(r.output)
    assert data["status"] == "reviewed"
    assert data["revision"]["original_understanding"] == "原理解"
    assert data["revision"]["revised_understanding"] == "修正理解"
    assert data["initial_understanding"] == "原理解"  # 未覆盖


def test_action_requires_reflect(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r = CliRunner().invoke(app, ["grounded", "start", "--text", "情况"])
    sid = r.output.strip().split()[1]
    r = CliRunner().invoke(
        app, ["grounded", "action", sid, "--action", "ask", "--action-draft", "草稿"]
    )
    assert r.exit_code == 1
    assert "reflect" in r.output


def test_feedback_keep(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r = CliRunner().invoke(app, ["grounded", "start", "--text", "情况"])
    sid = r.output.strip().split()[1]
    CliRunner().invoke(app, ["grounded", "reflect", sid, "--understanding", "原理解"])
    CliRunner().invoke(
        app, ["grounded", "action", sid, "--action", "observe", "--action-draft", "草稿"]
    )
    CliRunner().invoke(app, ["grounded", "record-action", sid])
    r = CliRunner().invoke(
        app,
        ["grounded", "feedback", sid, "--text", "反馈", "--keep", "--basis", "暂不改变", "--json"],
    )
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["revision"]["kept"] is True


def test_pause_resume(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r = CliRunner().invoke(app, ["grounded", "start", "--text", "情况"])
    sid = r.output.strip().split()[1]
    CliRunner().invoke(app, ["grounded", "reflect", sid, "--understanding", "理解"])
    r = CliRunner().invoke(app, ["grounded", "pause", sid])
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(app, ["grounded", "resume", sid])
    assert r.exit_code == 0, r.output
    assert "draft" in r.output


def test_kill_switch(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path, enabled=False)
    r = CliRunner().invoke(app, ["grounded", "start", "--text", "情况"])
    assert r.exit_code == 1
    assert "disabled" in r.output


def test_list_by_status(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r = CliRunner().invoke(app, ["grounded", "start", "--text", "情况甲"])
    sid_a = r.output.strip().split()[1]
    CliRunner().invoke(app, ["grounded", "start", "--text", "情况乙"])
    r = CliRunner().invoke(app, ["grounded", "list", "--status", "draft"])
    assert r.exit_code == 0, r.output
    assert sid_a in r.output
