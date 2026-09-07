"""Unit tests for the unified inbox CLI surface."""

from typer.testing import CliRunner

from finch.cli import app


def test_product_commands_present():
    names = {c.name for c in app.registered_commands}
    for present in ("draft", "next", "decide", "learn", "weekly"):
        assert present in names, present


def test_old_commands_removed():
    names = {c.name for c in app.registered_commands}
    for gone in ("review", "engagement", "jobs", "run", "gate"):
        assert gone not in names, gone


def test_draft_command_help():
    r = CliRunner().invoke(app, ["draft", "--help"])
    assert r.exit_code == 0
