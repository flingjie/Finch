"""CLI tests for finch practice（start 的落库与校验；LLM 命令由 service 测试覆盖）。"""

from datetime import UTC, datetime

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.expression_methods.models import ExpressionMethod
from finch.expression_methods.repository import ExpressionMethodRepository
from finch.practice.models import PracticeFeedback, PracticeLesson
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
        if output_model is PracticeFeedback:
            return PracticeFeedback(action="revise", diagnosis="d", evidence_quote="e", task="q")
        return PracticeLesson(lesson="l")


class _SeqRunner:
    """返回一个预设的 PracticeFeedback 序列（跨命令共享类级队列）。"""

    feedbacks: list = []

    def run(self, prompt, output_model, **kw):
        if output_model is PracticeFeedback:
            return self.feedbacks.pop(0)
        return PracticeLesson(lesson="l")


def _patch_seq_runner(monkeypatch, feedbacks):
    _SeqRunner.feedbacks = list(feedbacks)
    monkeypatch.setattr(cli, "create_runner", lambda *a, **k: _SeqRunner())
    monkeypatch.setattr(cli, "CodexRunner", _SeqRunner)


def _start_session(app, args):
    r = CliRunner().invoke(app, ["practice", "start", *args])
    assert r.exit_code == 0, r.output
    return r.output.strip().splitlines()[0].removeprefix("id: ")


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
    session_id = _start_session(app, ["--method", "emethod_1", "--attempt", "hello"])
    r = CliRunner().invoke(app, ["practice", "finish", session_id, "--final", "终稿"])
    assert r.exit_code == 1
    assert "verdict" in r.output.casefold()


def test_practice_finish_records_method_log(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    _patch(monkeypatch, settings)
    monkeypatch.setattr(cli, "create_runner", lambda *a, **k: _FakeRunner())
    monkeypatch.setattr(cli, "CodexRunner", _FakeRunner)
    _seed_method(tmp_path)
    session_id = _start_session(app, ["--method", "emethod_1", "--attempt", "hello"])
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
    session_id = _start_session(app, ["--method", "emethod_1", "--attempt", "hello"])
    r = CliRunner().invoke(app, ["practice", "diagnose", session_id, "--context", "额外语境"])
    assert r.exit_code == 0, r.output
    assert "## Expression method drill" in seen[0]
    assert "id: emethod_1" in seen[0]
    assert seen[0].index("Expression method drill") < seen[0].index("额外语境")


def test_practice_full_path_predict_transfer_finish(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    _patch(monkeypatch, settings)
    _patch_seq_runner(
        monkeypatch,
        [
            PracticeFeedback(action="predict", diagnosis="d1", task="读者会怎么读？"),
            PracticeFeedback(action="transfer", diagnosis="d2", task="改受众再写一遍"),
        ],
    )
    session_id = _start_session(app, ["--attempt", "首稿"])
    ws = Workspace(tmp_path)
    repo = PracticeSessionRepository(ws)

    d1 = CliRunner().invoke(app, ["practice", "diagnose", session_id])
    assert d1.exit_code == 0, d1.output
    assert "task: 读者会怎么读？" in d1.output
    turn1 = repo.get(session_id).turns[-1].id

    r1 = CliRunner().invoke(
        app,
        [
            "practice",
            "respond",
            session_id,
            "--turn",
            turn1,
            "--kind",
            "prediction",
            "--text",
            "读者会以为禁止",
        ],
    )
    assert r1.exit_code == 0, r1.output

    d2 = CliRunner().invoke(app, ["practice", "diagnose", session_id])
    assert d2.exit_code == 0, d2.output
    assert "task: 改受众再写一遍" in d2.output
    turn2 = repo.get(session_id).turns[-1].id

    r2 = CliRunner().invoke(
        app,
        [
            "practice",
            "respond",
            session_id,
            "--turn",
            turn2,
            "--kind",
            "transfer",
            "--text",
            "迁移表达",
        ],
    )
    assert r2.exit_code == 0, r2.output

    f = CliRunner().invoke(app, ["practice", "finish", session_id, "--final", "终稿"])
    assert f.exit_code == 0, f.output

    session = repo.get(session_id)
    assert session.status == "finished"
    assert session.revisions == []  # predict/transfer 不污染 revisions
    assert session.final_expression == "终稿"
    assert len(session.turns) == 2


def test_practice_respond_skip(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    _patch(monkeypatch, settings)
    _patch_seq_runner(
        monkeypatch, [PracticeFeedback(action="predict", diagnosis="d", task="预测")]
    )
    session_id = _start_session(app, ["--attempt", "首稿"])
    repo = PracticeSessionRepository(Workspace(tmp_path))
    d = CliRunner().invoke(app, ["practice", "diagnose", session_id])
    assert d.exit_code == 0, d.output
    turn = repo.get(session_id).turns[-1].id
    r = CliRunner().invoke(app, ["practice", "respond", session_id, "--turn", turn, "--skip"])
    assert r.exit_code == 0, r.output
    session = repo.get(session_id)
    assert session.turns[-1].response_kind == "skipped"
    assert session.turns[-1].response == ""


def test_practice_respond_invalid_kind(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    _patch(monkeypatch, settings)
    _patch_seq_runner(
        monkeypatch, [PracticeFeedback(action="revise", diagnosis="d", task="q")]
    )
    session_id = _start_session(app, ["--attempt", "首稿"])
    repo = PracticeSessionRepository(Workspace(tmp_path))
    d = CliRunner().invoke(app, ["practice", "diagnose", session_id])
    assert d.exit_code == 0, d.output
    turn = repo.get(session_id).turns[-1].id
    r = CliRunner().invoke(
        app, ["practice", "respond", session_id, "--turn", turn, "--kind", "nope", "--text", "x"]
    )
    assert r.exit_code == 1
    assert "invalid kind" in r.output


def test_practice_show_legacy_session_no_turns(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    _patch(monkeypatch, settings)
    session_id = _start_session(app, ["--attempt", "首稿"])
    r = CliRunner().invoke(app, ["practice", "show", session_id])
    assert r.exit_code == 0, r.output
    assert "diagnosis:" in r.output
