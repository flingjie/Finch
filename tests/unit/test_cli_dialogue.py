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


def test_cli_save_invalid_json_hides_input(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    secret = "secret-token-do-not-echo"
    note = tmp_path / "bad.json"
    note.write_text(
        json.dumps({"id": "dlg_bad", "leak": secret}, ensure_ascii=False),
        encoding="utf-8",
    )

    r = CliRunner().invoke(
        app,
        ["dialogue", "save", "--file", str(note), "--json"],
    )
    assert r.exit_code == 1
    payload = json.loads(r.output)
    assert payload == {"ok": False, "error": "invalid DialogueNote JSON"}
    assert secret not in r.output


def test_cli_show_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    r = CliRunner().invoke(app, ["dialogue", "show", "missing", "--json"])
    assert r.exit_code == 1
    assert json.loads(r.output)["ok"] is False


def test_cli_forget_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    r = CliRunner().invoke(app, ["dialogue", "forget", "missing", "--json"])
    assert r.exit_code == 1
    assert json.loads(r.output)["ok"] is False


def test_cli_search_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    r = CliRunner().invoke(app, ["dialogue", "search", "no-such-term", "--json"])
    assert r.exit_code == 0
    assert json.loads(r.output) == []
