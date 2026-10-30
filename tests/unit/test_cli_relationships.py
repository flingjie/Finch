"""CLI smoke：finch relationships show / weekly / import（无真实网络 / LLM）。"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.relationships.ids import person_id_for
from finch.relationships.models import Briefing, PersonProfile
from finch.relationships.repository import BriefingRepository, PersonRepository
from finch.settings import Paths, Settings
from finch.storage.workspace import Workspace


def _settings(tmp_path) -> Settings:
    return Settings(paths=Paths(var_dir=tmp_path))


def _seed_person(ws: Workspace) -> str:
    pid = person_id_for("github.com/alice")
    PersonRepository(ws).upsert(PersonProfile(person_id=pid, display_name="Alice"))
    return pid


def test_relationships_show_json(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    pid = _seed_person(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["relationships", "show", pid, "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert payload["person"]["display_name"] == "Alice"


def test_relationships_show_missing_exits_1(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["relationships", "show", "person_missing", "--json"])
    assert r.exit_code == 1


def test_relationships_import_url(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(app, ["relationships", "import", "--url", "https://x.com/a/1"])
    assert r.exit_code == 0, r.output
    assert "note_" in r.output


def test_relationships_weekly_replays_latest(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_person(ws)
    BriefingRepository(ws).upsert(Briefing(briefing_id="brief_seeded", fingerprint="fp"))
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["relationships", "weekly", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["briefing_id"] == "brief_seeded"
