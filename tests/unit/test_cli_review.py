"""Unit tests for finch review CLI commands."""
from typer.testing import CliRunner

from finch.cli import app


def test_review_subcommands_exist():
    r = CliRunner()
    for cmd in ["list", "show", "approve", "revise", "skip", "feedback"]:
        res = r.invoke(app, ["review", cmd, "--help"])
        assert res.exit_code == 0, cmd
