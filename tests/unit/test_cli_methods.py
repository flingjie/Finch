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
