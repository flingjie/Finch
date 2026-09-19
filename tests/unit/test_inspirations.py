"""Unit tests for the lightweight Inspiration note domain + CLI."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.inspirations.models import InspirationOrigin, inspiration_id_for
from finch.inspirations.service import InspirationService
from finch.settings import Paths, Settings
from finch.storage.workspace import Workspace


def _settings(tmp_path) -> Settings:
    return Settings(paths=Paths(var_dir=tmp_path))


def test_inspiration_id_is_content_addressed():
    a = inspiration_id_for("换个角度看问题")
    b = inspiration_id_for("换个角度看问题")
    c = inspiration_id_for("换个角度看问题 ")
    d = inspiration_id_for("不同的反思")
    assert a == b
    assert a == c  # 去首尾空白后相同
    assert a != d


def test_save_is_idempotent_and_preserves_origin(tmp_path):
    svc = InspirationService(Workspace(tmp_path))
    first = svc.save(text="换个角度看问题", origin=InspirationOrigin.OBSERVATION)
    second = svc.save(text="换个角度看问题", origin=InspirationOrigin.PRACTICE)
    assert first.id == second.id
    # 幂等：不覆盖第一次的用户意图。
    assert second.origin == InspirationOrigin.OBSERVATION


def test_note_appends_and_archive_filters(tmp_path):
    svc = InspirationService(Workspace(tmp_path))
    insp = svc.save(text="一条启发")
    assert insp.notes == []

    noted = svc.note(insp.id, text="补充一点")
    assert noted is not None
    assert [n.text for n in noted.notes] == ["补充一点"]

    archived = svc.archive(insp.id)
    assert archived is not None and archived.archived_at is not None

    assert svc.list() == []  # 默认不含已归档
    assert len(svc.list(include_archived=True)) == 1


def test_cli_save_list_show_note_archive(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(
        app,
        [
            "inspirations",
            "save",
            "--text",
            "换个角度看问题",
            "--source",
            "https://x.com/a/1",
            "--json",
        ],
    )
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    insp_id = payload["id"]
    assert insp_id.startswith("insp_")
    assert payload["source_refs"] == ["https://x.com/a/1"]

    r = CliRunner().invoke(app, ["inspirations", "list", "--json"])
    assert r.exit_code == 0, r.output
    assert [x["id"] for x in json.loads(r.output)] == [insp_id]

    r = CliRunner().invoke(app, ["inspirations", "note", insp_id, "--text", "补充"])
    assert r.exit_code == 0, r.output

    r = CliRunner().invoke(app, ["inspirations", "archive", insp_id])
    assert r.exit_code == 0, r.output

    r = CliRunner().invoke(app, ["inspirations", "list", "--json"])
    assert json.loads(r.output) == []


def test_cli_save_rejects_invalid_origin(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    r = CliRunner().invoke(
        app, ["inspirations", "save", "--text", "x", "--origin", "bogus"]
    )
    assert r.exit_code == 1
    assert "invalid --origin" in r.output
