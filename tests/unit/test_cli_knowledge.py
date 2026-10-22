"""CLI：finch knowledge / finch practice discover 命令的落库与守卫。"""

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.settings import Paths, Settings


def _patch(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(paths=Paths(var_dir=tmp_path)))


def test_knowledge_add_and_list(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r = CliRunner().invoke(
        app, ["knowledge", "add", "--claim", "先写结论", "--basis", "user_reported"]
    )
    assert r.exit_code == 0, r.output
    assert "knowledge_" in r.output
    r = CliRunner().invoke(app, ["knowledge", "list"])
    assert "先写结论" in r.output


def test_knowledge_reuse(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r = CliRunner().invoke(
        app, ["knowledge", "add", "--claim", "先写结论", "--basis", "user_reported"]
    )
    cid = r.output.strip().split()[2]
    r = CliRunner().invoke(app, ["knowledge", "reuse", cid, "--problem", "problem_x"])
    assert r.exit_code == 0, r.output
    assert "1 total" in r.output


def test_knowledge_add_invalid_basis(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r = CliRunner().invoke(app, ["knowledge", "add", "--claim", "C", "--basis", "bogus"])
    assert r.exit_code == 1
    assert "invalid basis" in r.output


def test_practice_discover_empty(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r = CliRunner().invoke(
        app, ["problems", "add", "--title", "汇报讲不清", "--desired-outcome", "一次讲清"]
    )
    pid = r.output.strip().split()[2]
    r = CliRunner().invoke(app, ["practice", "discover", "--problem", pid])
    assert r.exit_code == 0, r.output
    assert "no practice cards found" in r.output


def test_attempts_verify_result_status(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r = CliRunner().invoke(
        app, ["attempts", "add", "--problem", "P", "--attempt", "T", "--observation", "O"]
    )
    aid = r.output.strip().split()[2]
    r = CliRunner().invoke(
        app, ["attempts", "verify", aid, "--result", "有效", "--result-status", "mixed"]
    )
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(
        app,
        ["attempts", "verify", "attempt_x", "--result", "r", "--result-status", "bogus"],
    )
    assert r.exit_code == 1
    assert "invalid result-status" in r.output
