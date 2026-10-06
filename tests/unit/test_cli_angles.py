"""CLI tests for finch angles。"""

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.settings import Paths, Settings


def _settings(tmp_path):
    return Settings(paths=Paths(var_dir=tmp_path))


def _patch(monkeypatch, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)


class _FakeService:
    def discover(self, source, context=None):
        from finch.angle_discovery.models import AngleBrief, AngleCard, SourceSummary

        return AngleBrief(
            id="angle_x",
            source_type=source.source_type,
            source_ref=source.source_ref,
            content_hash=source.content_hash,
            source_summary=SourceSummary(main_point="AI 让个人编码更快。"),
            angles=[
                AngleCard(
                    title="AI 写代码更快以后，交付为什么没有同步变快？",
                    main_angles=["系统瓶颈", "真实场景映射"],
                    target_reader="小团队",
                    thesis="规格澄清、审查和验收成为新的交付瓶颈。",
                    incremental_value="从个人效率扩展到团队交付。",
                    increment_basis="inference",
                )
            ],
            recommended_index=0,
            recommendation_reason="最有读者价值。",
            outline=["用场景提出矛盾", "解释机制"],
        )


def test_discover_requires_exactly_one_source(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    r = CliRunner().invoke(app, ["angles", "discover"])
    assert r.exit_code == 1
    assert "exactly one of" in r.output


def test_discover_text_json(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "AngleDiscoveryService", lambda runner: _FakeService())
    r = CliRunner().invoke(app, ["angles", "discover", "--text", "hello", "--json"])
    assert r.exit_code == 0, r.output
    assert '"source_type": "text"' in r.output
    assert '"thesis"' in r.output


def test_discover_text_human(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "AngleDiscoveryService", lambda runner: _FakeService())
    r = CliRunner().invoke(app, ["angles", "discover", "--text", "hello"])
    assert r.exit_code == 0, r.output
    assert "原文摘要" in r.output
    assert "选题 1" in r.output
    assert "推荐方向" in r.output
    assert "id: angle_x" in r.output


def test_discover_persists_by_default(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "AngleDiscoveryService", lambda runner: _FakeService())
    r = CliRunner().invoke(app, ["angles", "discover", "--text", "hello"])
    assert r.exit_code == 0, r.output
    from finch.angle_discovery.repository import AngleBriefRepository
    from finch.storage.workspace import Workspace

    assert AngleBriefRepository(Workspace(tmp_path)).get("angle_x") is not None


def test_discover_no_save(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "AngleDiscoveryService", lambda runner: _FakeService())
    r = CliRunner().invoke(app, ["angles", "discover", "--text", "hello", "--no-save"])
    assert r.exit_code == 0, r.output
    from finch.angle_discovery.repository import AngleBriefRepository
    from finch.storage.workspace import Workspace

    assert AngleBriefRepository(Workspace(tmp_path)).get("angle_x") is None


def test_show_and_list(monkeypatch, tmp_path):
    _patch(monkeypatch, _settings(tmp_path))
    monkeypatch.setattr(cli, "AngleDiscoveryService", lambda runner: _FakeService())
    r = CliRunner().invoke(app, ["angles", "discover", "--text", "hello"])
    assert r.exit_code == 0, r.output
    shown = CliRunner().invoke(app, ["angles", "show", "angle_x"])
    assert shown.exit_code == 0, shown.output
    assert "原文摘要" in shown.output
    listed = CliRunner().invoke(app, ["angles", "list"])
    assert listed.exit_code == 0, listed.output
    assert "angle_x" in listed.output
