"""CLI tests for finch practice（start 的互斥校验与落库；LLM 命令由 service 测试覆盖）。"""

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.practice.models import PracticeDiagnosis, PracticeLesson
from finch.practice.service import PracticeService
from finch.settings import Paths, Settings
from finch.storage.database import Store
from finch.storage.repositories import PracticeSessionRepository


def _settings(tmp_path):
    return Settings(paths=Paths(db_path=tmp_path / "finch.db"))


def _patch(monkeypatch, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)


def test_practice_start_without_idea_is_unlinked(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _patch(monkeypatch, settings)
    r = CliRunner().invoke(app, ["practice", "start", "--attempt", "hello"])
    assert r.exit_code == 0, r.output
    session_id = r.output.strip().splitlines()[0].removeprefix("id: ")
    session = PracticeSessionRepository(store).get(session_id)
    assert session is not None
    assert session.idea_id is None
    assert session.initial_attempt == "hello"


def test_practice_start_persists(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _patch(monkeypatch, settings)
    r = CliRunner().invoke(app, ["practice", "start", "--idea", "idea_1", "--attempt", "hello"])
    assert r.exit_code == 0, r.output
    session_id = r.output.strip().splitlines()[0].removeprefix("id: ")
    session = PracticeSessionRepository(store).get(session_id)
    assert session is not None
    assert session.idea_id == "idea_1"
    assert session.initial_attempt == "hello"


class _FakeRunner:
    def run(self, prompt, output_model, **kw):
        if output_model is PracticeDiagnosis:
            return PracticeDiagnosis(diagnosis="d", question="q")
        return PracticeLesson(lesson="l")


def test_practice_save_on_finished_session_clean_exit(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    store = Store(settings.paths.db_path)
    store.init()
    _patch(monkeypatch, settings)
    svc = PracticeService(PracticeSessionRepository(store), _FakeRunner())
    s = svc.start(idea_id="idea_1", initial_attempt="初稿")
    s = svc.finish(s.id, "最终版")
    assert s.status == "finished"
    r = CliRunner().invoke(app, ["practice", "save", s.id, "--revision", "修订"])
    assert r.exit_code == 1
    assert "illegal transition" in r.output
    assert "Traceback" not in r.output
