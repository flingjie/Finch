from pathlib import Path

from typer.testing import CliRunner

from finch.cli import app
from finch.graph.events import NodeResult


def test_list_features():
    r = CliRunner().invoke(app, ["dev", "list-features"])
    assert r.exit_code == 0
    assert "recall" in r.output
    assert "select_groups" in r.output


def test_run_node_recall():
    fixture = Path("tests/fixtures/nodes/recall.json")
    r = CliRunner().invoke(app, ["dev", "run-node", "recall", "--input", str(fixture)])
    assert r.exit_code == 0, r.output
    assert "succeeded" in r.output


def test_run_node_failed_status_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        "finch.dev.cli.run_node",
        lambda node, path, settings: NodeResult(status="failed"),
    )
    r = CliRunner().invoke(app, ["dev", "run-node", "recall", "--input", "x.json"])
    assert r.exit_code == 1, r.output
    assert "failed" in r.output


def test_run_node_missing_file_clean_error():
    r = CliRunner().invoke(
        app, ["dev", "run-node", "recall", "--input", "/does/not/exist.json"]
    )
    assert r.exit_code == 1, r.output
    assert "Traceback" not in r.output
    assert "cannot read fixture" in r.output
