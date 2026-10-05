"""CLI tests for finch summarize。"""

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.settings import Paths, Settings


def _settings(tmp_path):
    return Settings(paths=Paths(var_dir=tmp_path))


def _patch(monkeypatch, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)


class _FakeService:
    def summarize(self, source):
        from finch.content_summary.models import ContentSummary, EvidencePoint

        return ContentSummary(
            id="summary_x",
            source_type=source.source_type,
            source_ref=source.source_ref,
            content_hash=source.content_hash,
            main_point="测试通过不等于问题解决",
            key_points=["测试通过只是必要条件", "记忆不是聊天记录"],
            evidence=[EvidencePoint(source="作者", content="测试通过，但问题没解决")],
            conditions=["原文未说明适用范围"],
        )


def test_summarize_requires_exactly_one_source(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    r = CliRunner().invoke(app, ["summarize"])
    assert r.exit_code == 1
    assert "exactly one of" in r.output


def test_summarize_text_json(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "ContentSummaryService", lambda runner: _FakeService())
    r = CliRunner().invoke(app, ["summarize", "--text", "hello", "--json"])
    assert r.exit_code == 0, r.output
    assert '"source_type": "text"' in r.output
    assert '"main_point"' in r.output


def test_summarize_text_human(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "ContentSummaryService", lambda runner: _FakeService())
    r = CliRunner().invoke(app, ["summarize", "--text", "hello"])
    assert r.exit_code == 0, r.output
    assert "一句话主旨" in r.output
    assert "核心要点" in r.output
    assert "关键依据或例子" in r.output
    assert "条件与限制" in r.output
    assert "id: summary_x" in r.output
    assert "[作者]" in r.output


def test_summarize_persists_by_default(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "ContentSummaryService", lambda runner: _FakeService())
    r = CliRunner().invoke(app, ["summarize", "--text", "hello"])
    assert r.exit_code == 0, r.output
    from finch.content_summary.repository import ContentSummaryRepository
    from finch.storage.workspace import Workspace

    got = ContentSummaryRepository(Workspace(tmp_path)).get("summary_x")
    assert got is not None


def test_summarize_no_save(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "ContentSummaryService", lambda runner: _FakeService())
    r = CliRunner().invoke(app, ["summarize", "--text", "hello", "--no-save"])
    assert r.exit_code == 0, r.output
    from finch.content_summary.repository import ContentSummaryRepository
    from finch.storage.workspace import Workspace

    assert ContentSummaryRepository(Workspace(tmp_path)).get("summary_x") is None
