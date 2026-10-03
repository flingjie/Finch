"""CLI tests for finch practice（start 的互斥校验与落库；LLM 命令由 service 测试覆盖）。"""

from datetime import UTC, datetime

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.expression_methods.models import ExpressionMethod
from finch.expression_methods.repository import ExpressionMethodRepository
from finch.practice.models import PracticeDiagnosis, PracticeLesson
from finch.practice.service import PracticeService
from finch.settings import Paths, Settings
from finch.storage.repositories import PracticeSessionRepository
from finch.storage.workspace import Workspace


def _settings(tmp_path):
    return Settings(paths=Paths(var_dir=tmp_path))


def _patch(monkeypatch, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)


def test_practice_start_without_idea_is_unlinked(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _patch(monkeypatch, settings)
    r = CliRunner().invoke(app, ["practice", "start", "--attempt", "hello"])
    assert r.exit_code == 0, r.output
    session_id = r.output.strip().splitlines()[0].removeprefix("id: ")
    session = PracticeSessionRepository(ws).get(session_id)
    assert session is not None
    assert session.idea_id is None
    assert session.initial_attempt == "hello"


def test_practice_start_persists(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _patch(monkeypatch, settings)
    r = CliRunner().invoke(app, ["practice", "start", "--idea", "idea_1", "--attempt", "hello"])
    assert r.exit_code == 0, r.output
    session_id = r.output.strip().splitlines()[0].removeprefix("id: ")
    session = PracticeSessionRepository(ws).get(session_id)
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
    ws = Workspace(settings.paths.var_dir)
    _patch(monkeypatch, settings)
    svc = PracticeService(PracticeSessionRepository(ws), _FakeRunner())
    s = svc.start(idea_id="idea_1", initial_attempt="初稿")
    s = svc.finish(s.id, "最终版")
    assert s.status == "finished"
    r = CliRunner().invoke(app, ["practice", "save", s.id, "--revision", "修订"])
    assert r.exit_code == 1
    assert "illegal transition" in r.output
    assert "Traceback" not in r.output


def _seed_method(tmp_path, method_id: str = "emethod_1") -> None:
    now = datetime.now(UTC)
    ExpressionMethodRepository(Workspace(tmp_path)).upsert(
        ExpressionMethod(
            id=method_id,
            title="失败开场",
            why_effective="先见损失",
            when_to_use="复盘",
            mini_exercise="写开头",
            created_at=now,
            updated_at=now,
        )
    )


def test_practice_start_with_method(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    _patch(monkeypatch, settings)
    _seed_method(tmp_path)
    r = CliRunner().invoke(
        app,
        ["practice", "start", "--method", "emethod_1", "--attempt", "hello", "--json"],
    )
    assert r.exit_code == 0, r.output
    assert '"method_id": "emethod_1"' in r.output


def test_practice_start_missing_method(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    r = CliRunner().invoke(
        app, ["practice", "start", "--method", "nope", "--attempt", "hello"]
    )
    assert r.exit_code == 1
    assert "method not found" in r.output


def test_practice_finish_requires_verdict_for_method(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    _patch(monkeypatch, settings)
    monkeypatch.setattr(cli, "create_runner", lambda *a, **k: _FakeRunner())
    monkeypatch.setattr(cli, "CodexRunner", _FakeRunner)
    _seed_method(tmp_path)
    start = CliRunner().invoke(
        app, ["practice", "start", "--method", "emethod_1", "--attempt", "hello"]
    )
    session_id = start.output.strip().splitlines()[0].removeprefix("id: ")
    r = CliRunner().invoke(app, ["practice", "finish", session_id, "--final", "终稿"])
    assert r.exit_code == 1
    assert "verdict" in r.output.casefold()


def test_practice_finish_records_method_log(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    _patch(monkeypatch, settings)
    monkeypatch.setattr(cli, "create_runner", lambda *a, **k: _FakeRunner())
    monkeypatch.setattr(cli, "CodexRunner", _FakeRunner)
    _seed_method(tmp_path)
    start = CliRunner().invoke(
        app, ["practice", "start", "--method", "emethod_1", "--attempt", "hello"]
    )
    session_id = start.output.strip().splitlines()[0].removeprefix("id: ")
    r = CliRunner().invoke(
        app,
        [
            "practice",
            "finish",
            session_id,
            "--final",
            "终稿",
            "--verdict",
            "worth_reuse",
            "--note",
            "再用",
        ],
    )
    assert r.exit_code == 0, r.output
    method = ExpressionMethodRepository(Workspace(tmp_path)).get("emethod_1")
    assert method is not None
    assert method.practice_logs[-1].verdict == "worth_reuse"
    assert method.practice_logs[-1].session_id == session_id


def test_practice_diagnose_prepends_method_card(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    _patch(monkeypatch, settings)
    seen: list[str] = []

    class _Capture(_FakeRunner):
        def run(self, prompt, output_model, **kw):
            seen.append(prompt)
            return super().run(prompt, output_model, **kw)

    monkeypatch.setattr(cli, "create_runner", lambda *a, **k: _Capture())
    monkeypatch.setattr(cli, "CodexRunner", _Capture)
    _seed_method(tmp_path)
    start = CliRunner().invoke(
        app, ["practice", "start", "--method", "emethod_1", "--attempt", "hello"]
    )
    session_id = start.output.strip().splitlines()[0].removeprefix("id: ")
    r = CliRunner().invoke(app, ["practice", "diagnose", session_id, "--context", "额外语境"])
    assert r.exit_code == 0, r.output
    assert "## Expression method drill" in seen[0]
    assert "id: emethod_1" in seen[0]
    assert seen[0].index("Expression method drill") < seen[0].index("额外语境")
