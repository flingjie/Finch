"""CLI tests for finch practice（start 的互斥校验与落库；LLM 命令由 service 测试覆盖）。"""

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.settings import Paths, Settings
from finch.storage.database import Store
from finch.storage.repositories import PracticeSessionRepository


def _settings(tmp_path):
    return Settings(paths=Paths(db_path=tmp_path / "finch.db"))


def _patch(monkeypatch, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)


def test_practice_start_requires_exactly_one_source(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    r = CliRunner().invoke(app, ["practice", "start", "--attempt", "hello"])
    assert r.exit_code == 1
    assert "exactly one of" in r.output


def test_practice_start_persists(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _patch(monkeypatch, settings)
    r = CliRunner().invoke(app, ["practice", "start", "--idea", "idea_1", "--attempt", "hello"])
    assert r.exit_code == 0, r.output
    session = PracticeSessionRepository(store).get(r.output.strip())
    assert session is not None
    assert session.idea_id == "idea_1"
    assert session.initial_attempt == "hello"
