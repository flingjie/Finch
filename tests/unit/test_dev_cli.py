from pathlib import Path

from typer.testing import CliRunner

from finch.cli import app


def test_list_features():
    r = CliRunner().invoke(app, ["dev", "list-features"])
    assert r.exit_code == 0
    assert "recall" in r.output
    assert "select_groups" in r.output


def test_run_node_recall(tmp_path, monkeypatch):
    from finch import cli as cli_mod

    monkeypatch.setattr(cli_mod, "load_settings", lambda: _settings(tmp_path))
    fixture = Path("tests/fixtures/nodes/recall.json")
    r = CliRunner().invoke(app, ["dev", "run-node", "recall", "--input", str(fixture)])
    assert r.exit_code == 0, r.output
    assert "succeeded" in r.output


def _settings(tmp_path):
    from finch.settings import Settings
    return Settings()  # run-node 不触碰 paths，默认 Settings 即可
