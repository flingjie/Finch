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
    with_clarity = False

    def analyze(self, source):
        from finch.article.models import (
            ArticleReport,
            AudienceChange,
            ClarityCostReduction,
            Effectiveness,
            ExpressionTask,
            StyleBlock,
            StyleEvidence,
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
            style=StyleBlock(
                opening=[
                    StyleEvidence(
                        dimension="opening",
                        observation="结论先行",
                        excerpts=["先说结果"],
                        confidence="high",
                    )
                ],
                transferable_techniques=["结论先行"],
            ),
            clarity_cost_reductions=(
                [
                    ClarityCostReduction(
                        excerpt="先备份再升级",
                        method="前置条件紧邻建议",
                        reader_effect="读者不必回查",
                        mini_exercise="把前提挪到动作前",
                        rule_id="CL03",
                    )
                ]
                if self.with_clarity
                else []
            ),
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
    assert "写作风格" in r.output
    assert "目标达成情况" in r.output
    assert "可借鉴方法" in r.output
    assert "id: article_x" in r.output
    assert "[1]" in r.output


def test_article_analyze_persists_by_default(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "ArticleAnalysisService", lambda runner: _FakeService())
    r = CliRunner().invoke(app, ["article", "analyze", "--text", "hello"])
    assert r.exit_code == 0, r.output
    assert "id: article_x" in r.output or "report id" in r.output.casefold() or "article_x" in r.output
    from finch.article.repository import ArticleReportRepository
    from finch.storage.workspace import Workspace

    got = ArticleReportRepository(Workspace(tmp_path)).get("article_x")
    assert got is not None


def test_article_analyze_no_save(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "ArticleAnalysisService", lambda runner: _FakeService())
    r = CliRunner().invoke(
        app, ["article", "analyze", "--text", "hello", "--no-save"]
    )
    assert r.exit_code == 0, r.output
    from finch.article.repository import ArticleReportRepository
    from finch.storage.workspace import Workspace

    assert ArticleReportRepository(Workspace(tmp_path)).get("article_x") is None


def test_article_show(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "ArticleAnalysisService", lambda runner: _FakeService())
    CliRunner().invoke(app, ["article", "analyze", "--text", "hello"])
    r = CliRunner().invoke(app, ["article", "show", "article_x"])
    assert r.exit_code == 0, r.output
    assert "可借鉴方法" in r.output


def test_article_show_missing(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    r = CliRunner().invoke(app, ["article", "show", "missing"])
    assert r.exit_code == 1


def test_article_analyze_human_omits_clarity_section_when_empty(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "ArticleAnalysisService", lambda runner: _FakeService())
    r = CliRunner().invoke(app, ["article", "analyze", "--text", "hello"])
    assert r.exit_code == 0, r.output
    assert "降低理解成本" not in r.output


def test_article_analyze_human_renders_clarity_section(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))

    class _Svc(_FakeService):
        with_clarity = True

    monkeypatch.setattr(cli, "ArticleAnalysisService", lambda runner: _Svc())
    r = CliRunner().invoke(app, ["article", "analyze", "--text", "hello"])
    assert r.exit_code == 0, r.output
    assert "降低理解成本" in r.output
    assert "[CL03]" in r.output
    assert "先备份再升级" in r.output


def test_article_analyze_renders_style_section(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "ArticleAnalysisService", lambda runner: _FakeService())
    r = CliRunner().invoke(app, ["article", "analyze", "--text", "hello"])
    assert r.exit_code == 0, r.output
    assert "## 写作风格" in r.output
    assert "结论先行" in r.output
    assert "先说结果" in r.output
    assert r.output.index("## 表达特点") < r.output.index("## 写作风格")
    assert r.output.index("## 写作风格") < r.output.index("## 目标达成情况")


def test_style_subcommand_removed(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    r = CliRunner().invoke(app, ["style", "analyze", "--text", "hello"])
    assert r.exit_code != 0
