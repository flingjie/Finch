"""CLI tests for finch methods."""

from typer.testing import CliRunner

from finch import cli
from finch.article.models import (
    ArticleReport,
    AudienceChange,
    Effectiveness,
    ExpressionTask,
    TransferableMethod,
)
from finch.article.repository import ArticleReportRepository
from finch.cli import app
from finch.expression_methods.models import MergeSuggestion
from finch.settings import Paths, Settings
from finch.storage.workspace import Workspace


def _settings(tmp_path):
    return Settings(paths=Paths(var_dir=tmp_path))


def _patch(monkeypatch, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)


def _seed_report(tmp_path) -> None:
    ArticleReportRepository(Workspace(tmp_path)).upsert(
        ArticleReport(
            id="article_x",
            source_type="text",
            content_hash="h",
            expression_task=ExpressionTask(topic="t", primary_task="解释"),
            audience_change=AudienceChange(
                who="d", before="a", after="b", fit_check="ok"
            ),
            effectiveness=Effectiveness(
                clarity="c",
                concreteness="c",
                credibility="c",
                actionability="n/a",
            ),
            transferable_methods=[
                TransferableMethod(
                    method="m1",
                    why_effective_here="w",
                    when_to_use="u",
                    mini_exercise="e",
                ),
                TransferableMethod(
                    method="m2",
                    why_effective_here="w",
                    when_to_use="u",
                    mini_exercise="e",
                ),
            ],
        )
    )


class _FakeRunner:
    def run(self, prompt, output_model, **kw):
        if output_model is MergeSuggestion:
            return MergeSuggestion(candidates=[])
        raise AssertionError(output_model)


def test_methods_save_suggest_only_no_write(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    _seed_report(tmp_path)
    monkeypatch.setattr(cli, "create_runner", lambda *a, **k: _FakeRunner())
    monkeypatch.setattr(cli, "CodexRunner", _FakeRunner)
    r = CliRunner().invoke(
        app, ["methods", "save", "--report", "article_x", "--index", "1"]
    )
    assert r.exit_code == 2, r.output
    from finch.expression_methods.repository import ExpressionMethodRepository

    assert ExpressionMethodRepository(Workspace(tmp_path)).list_all() == []


def test_methods_save_as_new(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    _seed_report(tmp_path)
    monkeypatch.setattr(cli, "create_runner", lambda *a, **k: _FakeRunner())
    monkeypatch.setattr(cli, "CodexRunner", _FakeRunner)
    r = CliRunner().invoke(
        app,
        ["methods", "save", "--report", "article_x", "--index", "2", "--as-new"],
    )
    assert r.exit_code == 0, r.output
    from finch.expression_methods.repository import ExpressionMethodRepository

    methods = ExpressionMethodRepository(Workspace(tmp_path)).list_all()
    assert len(methods) == 1
    assert methods[0].title == "m2"


def test_methods_save_missing_report(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "create_runner", lambda *a, **k: _FakeRunner())
    monkeypatch.setattr(cli, "CodexRunner", _FakeRunner)
    r = CliRunner().invoke(
        app, ["methods", "save", "--report", "missing", "--index", "1", "--as-new"]
    )
    assert r.exit_code == 1
    assert "report not found" in r.output


def _saved_method(tmp_path):
    from finch.expression_methods.repository import ExpressionMethodRepository

    return ExpressionMethodRepository(Workspace(tmp_path)).list_all()[0]


def test_methods_log_reply_appends_reply_log(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    _seed_report(tmp_path)
    monkeypatch.setattr(cli, "create_runner", lambda *a, **k: _FakeRunner())
    monkeypatch.setattr(cli, "CodexRunner", _FakeRunner)
    CliRunner().invoke(
        app, ["methods", "save", "--report", "article_x", "--index", "1", "--as-new"]
    )
    method = _saved_method(tmp_path)
    r = CliRunner().invoke(
        app,
        [
            "methods", "log-reply", "--method", method.id,
            "--artifact", "art_opp_1_reply_draft",
            "--verdict", "useful", "--note", "改了半句",
            "--conditions", "对方是独立开发者",
            "--question", "你最初为什么删掉自主决策步骤",
            "--response", "他说删掉后返工少了",
            "--follow-up", "把这一点记进对话笔记",
        ],
    )
    assert r.exit_code == 0, r.output
    from finch.expression_methods.repository import ExpressionMethodRepository

    updated = ExpressionMethodRepository(Workspace(tmp_path)).get(method.id)
    log = updated.practice_logs[-1]
    assert log.form == "reply"
    assert log.draft_ref == "art_opp_1_reply_draft"
    assert log.verdict == "useful"
    assert log.note == "改了半句"
    assert log.conditions == "对方是独立开发者"
    assert log.question_asked == "你最初为什么删掉自主决策步骤"
    assert log.response == "他说删掉后返工少了"
    assert log.follow_up_action == "把这一点记进对话笔记"


def test_methods_log_reply_invalid_verdict(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "create_runner", lambda *a, **k: _FakeRunner())
    monkeypatch.setattr(cli, "CodexRunner", _FakeRunner)
    r = CliRunner().invoke(
        app, ["methods", "log-reply", "--method", "m", "--artifact", "a", "--verdict", "bad"]
    )
    assert r.exit_code == 1
    assert "invalid verdict" in r.output


def test_methods_log_reply_missing_method(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "create_runner", lambda *a, **k: _FakeRunner())
    monkeypatch.setattr(cli, "CodexRunner", _FakeRunner)
    r = CliRunner().invoke(
        app,
        ["methods", "log-reply", "--method", "nope", "--artifact", "a", "--verdict", "useful"],
    )
    assert r.exit_code == 1
    assert "method not found" in r.output
