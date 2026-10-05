"""CLI：finch problems / finch attempts 命令的落库与守卫。"""

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.settings import Paths, Settings


def _patch(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(paths=Paths(var_dir=tmp_path)))


def test_problems_add_and_list(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r = CliRunner().invoke(app, ["problems", "add", "--title", "重试何时值得"])
    assert r.exit_code == 0, r.output
    assert "problem_" in r.output
    r = CliRunner().invoke(app, ["problems", "list"])
    assert "重试何时值得" in r.output


def test_problems_rejects_fourth(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    for i in range(3):
        CliRunner().invoke(app, ["problems", "add", "--title", f"问题 {i}"])
    r = CliRunner().invoke(app, ["problems", "add", "--title", "问题 3"])
    assert r.exit_code == 1
    assert "max 3" in r.output


def test_attempts_add_links_problem(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r = CliRunner().invoke(app, ["problems", "add", "--title", "重试"])
    pid = r.output.strip().split()[2]
    r = CliRunner().invoke(
        app,
        ["attempts", "add", "--problem-id", pid, "--problem", "重试",
         "--attempt", "加重试", "--observation", "多数卡输入"],
    )
    assert r.exit_code == 0, r.output
    assert "attempt_" in r.output


def test_attempts_verify(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r = CliRunner().invoke(
        app,
        ["attempts", "add", "--problem", "P", "--attempt", "T", "--observation", "O"],
    )
    aid = r.output.strip().split()[2]
    r = CliRunner().invoke(app, ["attempts", "verify", aid, "--result", "有效"])
    assert r.exit_code == 0, r.output


def test_problems_list_default_excludes_closed(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r1 = CliRunner().invoke(app, ["problems", "add", "--title", "问题 A"])
    pid_a = r1.output.strip().split()[2]
    CliRunner().invoke(app, ["problems", "add", "--title", "问题 B"])
    CliRunner().invoke(app, ["problems", "close", pid_a])
    r = CliRunner().invoke(app, ["problems", "list"])
    assert r.exit_code == 0, r.output
    assert "问题 B" in r.output
    assert "问题 A" not in r.output


def test_attempts_list_default_excludes_closed(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    r1 = CliRunner().invoke(
        app, ["attempts", "add", "--problem", "问题甲", "--attempt", "T", "--observation", "O"]
    )
    aid = r1.output.strip().split()[2]
    CliRunner().invoke(app, ["attempts", "close", aid])
    CliRunner().invoke(
        app, ["attempts", "add", "--problem", "问题乙", "--attempt", "T2", "--observation", "O2"]
    )
    r = CliRunner().invoke(app, ["attempts", "list"])
    assert r.exit_code == 0, r.output
    assert "问题乙" in r.output
    assert "问题甲" not in r.output
