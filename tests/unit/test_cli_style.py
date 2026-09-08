"""CLI tests for finch style analyze。"""

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
        from finch.style.models import StyleReport

        return StyleReport(
            id="style_x", source_type=source.source_type, content_hash=source.content_hash,
            sample_size=source.sample_size, overall_confidence="high",
        )


def test_style_analyze_requires_exactly_one_source(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    r = CliRunner().invoke(app, ["style", "analyze"])
    assert r.exit_code == 1
    assert "exactly one of" in r.output


def test_style_analyze_text_json(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    _patch(monkeypatch, settings)
    monkeypatch.setattr(cli, "WritingStyleService", lambda runner: _FakeService())
    r = CliRunner().invoke(app, ["style", "analyze", "--text", "hello", "--json"])
    assert r.exit_code == 0, r.output
    assert '"source_type": "text"' in r.output
