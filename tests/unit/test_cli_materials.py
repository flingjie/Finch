"""Unit tests for the `finch materials` CLI commands."""

from __future__ import annotations

import json

from _notion_fake import FakeNotionClient
from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.settings import Paths, Settings


def _settings(tmp_path) -> Settings:
    return Settings(paths=Paths(var_dir=tmp_path))


def _patch(monkeypatch, tmp_path, fake):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(cli, "_require_notion", lambda settings: (fake, "pg-1"))


def test_doctor_ok(monkeypatch, tmp_path):
    fake = FakeNotionClient()
    fake.set_page("pg-1", "月度页")
    _patch(monkeypatch, tmp_path, fake)
    r = CliRunner().invoke(app, ["materials", "doctor", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["ok"] is True
    assert payload["page_title"] == "月度页"


def test_capture_and_drain(monkeypatch, tmp_path):
    fake = FakeNotionClient()
    fake.set_page("pg-1")
    _patch(monkeypatch, tmp_path, fake)
    r = CliRunner().invoke(
        app, ["materials", "capture", "--title", "标题", "--body-text", "正文", "--drain", "--json"]
    )
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["status"] == "succeeded"
    assert payload["remote_result"]["block_id"]
