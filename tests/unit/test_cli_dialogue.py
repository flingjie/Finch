"""Unit tests for the finch dialogue CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.settings import Paths, Settings


def _settings(tmp_path) -> Settings:
    return Settings(paths=Paths(var_dir=tmp_path))


def _note_json(tmp_path, note_id: str = "dlg_a", topic_key: str = "skill-to-code") -> Path:
    p = tmp_path / "note.json"
    p.write_text(
        json.dumps(
            {
                "id": note_id,
                "topic": "skill 代码化",
                "topic_key": topic_key,
                "checkpoints": [
                    {
                        "checkpoint_id": "cp_1",
                        "user_position": "先做 Skill 再代码化",
                        "position_status": "tentative",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return p


def test_cli_save_show_search_forget(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    note = _note_json(tmp_path)

    r = CliRunner().invoke(
        app,
        [
            "dialogue",
            "save",
            "--file",
            str(note),
            "--expected-revision",
            "0",
            "--json",
        ],
    )
    assert r.exit_code == 0, r.output
    saved = json.loads(r.output)
    assert saved["id"] == "dlg_a"
    assert saved["revision"] == 1

    r = CliRunner().invoke(app, ["dialogue", "show", "dlg_a", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["checkpoints"][0]["checkpoint_id"] == "cp_1"

    r = CliRunner().invoke(app, ["dialogue", "search", "skill", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)[0]["id"] == "dlg_a"

    r = CliRunner().invoke(app, ["dialogue", "forget", "dlg_a", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["ok"] is True


def test_cli_save_conflict_returns_error(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    note = _note_json(tmp_path)

    assert (
        CliRunner()
        .invoke(
            app,
            [
                "dialogue",
                "save",
                "--file",
                str(note),
                "--expected-revision",
                "0",
            ],
        )
        .exit_code
        == 0
    )
    r = CliRunner().invoke(
        app,
        [
            "dialogue",
            "save",
            "--file",
            str(note),
            "--expected-revision",
            "0",
            "--json",
        ],
    )
    assert r.exit_code == 1
    assert json.loads(r.output)["ok"] is False
