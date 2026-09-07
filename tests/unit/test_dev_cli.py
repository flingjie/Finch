from typer.testing import CliRunner

from finch.cli import app


def test_list_features():
    r = CliRunner().invoke(app, ["dev", "list-features"])
    assert r.exit_code == 0
    assert "select_groups" in r.output
