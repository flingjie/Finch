"""Unit tests for finch run CLI commands."""
from typer.testing import CliRunner

from finch.cli import app


def test_run_daily_help():
    r = CliRunner().invoke(app, ["run", "daily", "--help"])
    assert r.exit_code == 0
