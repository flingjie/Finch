"""CLI tests for finch article analyze。"""

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.settings import Paths, Settings


def _settings(tmp_path):
    return Settings(paths=Paths(var_dir=tmp_path))


def _patch(monkeypatch, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)


class _FakeService:
    def analyze(self, source):
        from finch.article.models import (
            ArticleReport,
            AudienceChange,
            Effectiveness,
            ExpressionTask,
            TechniqueBreakdown,
            TransferableMethod,
        )

        return ArticleReport(
            id="article_x",
            source_type=source.source_type,
            source_ref=source.source_ref,
            content_hash=source.content_hash,
            expression_task=ExpressionTask(
                topic="t", primary_task="解释", inferred=True
            ),
            audience_change=AudienceChange(
                who="开发者", before="混淆", after="清楚", fit_check="ok"
            ),
            techniques=[
                TechniqueBreakdown(
                    excerpt="ex", method="m", reader_effect="r"
                )
            ],
            effectiveness=Effectiveness(
                clarity="c",
                concreteness="c",
                credibility="c",
                actionability="不适用：理解即可",
            ),
            transferable_methods=[
                TransferableMethod(
                    method="a",
                    why_effective_here="w",
                    when_to_use="u",
                    mini_exercise="e",
                ),
                TransferableMethod(
                    method="b",
                    why_effective_here="w",
                    when_to_use="u",
                    mini_exercise="e",
                ),
            ],
        )


def test_article_analyze_requires_exactly_one_source(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    r = CliRunner().invoke(app, ["article", "analyze"])
    assert r.exit_code == 1
    assert "exactly one of" in r.output


def test_article_analyze_text_json(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "ArticleAnalysisService", lambda runner: _FakeService())
    r = CliRunner().invoke(app, ["article", "analyze", "--text", "hello", "--json"])
    assert r.exit_code == 0, r.output
    assert '"source_type": "text"' in r.output
    assert '"inferred": true' in r.output


def test_article_analyze_text_human(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "ArticleAnalysisService", lambda runner: _FakeService())
    r = CliRunner().invoke(app, ["article", "analyze", "--text", "hello"])
    assert r.exit_code == 0, r.output
    assert "表达任务" in r.output
    assert "根据文章推断" in r.output
    assert "读者与预期变化" in r.output
    assert "表达特点" in r.output
    assert "目标达成情况" in r.output
    assert "可借鉴方法" in r.output
