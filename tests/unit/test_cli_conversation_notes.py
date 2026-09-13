"""CLI tests for conversations ingest notes / commit (Value Discovery v2)."""

import json
from pathlib import Path

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.settings import Paths, Settings
from finch.storage.repositories import ConversationThreadRepository
from finch.storage.workspace import Workspace

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "conversations"
    / "assisted-usage-reply.json"
)


def _settings(tmp_path):
    return Settings(paths=Paths(var_dir=tmp_path))


def _patch(monkeypatch, settings):
    monkeypatch.setattr(cli, "load_settings", lambda: settings)


def test_ingest_with_notes_and_idempotent(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _patch(monkeypatch, settings)
    raw = json.loads(FIXTURE.read_text())
    notes_path = tmp_path / "notes.json"
    notes_path.write_text(json.dumps(raw["notes"]), encoding="utf-8")

    args = [
        "conversations",
        "ingest",
        "--peer",
        raw["peer_id"],
        "--url",
        raw["url"],
        "--message-id",
        raw["message_id"],
        "--body",
        raw["body"],
        "--direction",
        raw["direction"],
        "--topic",
        raw["topic"],
        "--attested",
        "--notes-file",
        str(notes_path),
        "--json",
    ]
    r1 = CliRunner().invoke(app, args)
    assert r1.exit_code == 0, r1.output
    payload = json.loads(r1.output)
    assert len(payload["observation_notes"]) == 2
    thread_id = payload["thread_id"]

    r2 = CliRunner().invoke(app, args)
    assert r2.exit_code == 0, r2.output
    payload2 = json.loads(r2.output)
    assert payload2["record"]["id"] == payload["record"]["id"]
    thread = ConversationThreadRepository(ws).get(thread_id)
    assert thread is not None
    active = [n for n in thread.observation_notes if not n.superseded]
    assert len(active) == 2


def test_ingest_rejects_polite_usage_note(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    _patch(monkeypatch, settings)
    r = CliRunner().invoke(
        app,
        [
            "conversations",
            "ingest",
            "--peer",
            "peer_1",
            "--url",
            "https://x.com/u/1",
            "--body",
            "很有趣，有空看看",
            "--direction",
            "inbound",
            "--note-kind",
            "usage_feedback",
            "--note",
            "很有趣，有空看看",
        ],
    )
    assert r.exit_code == 1
    assert "polite interest" in r.output


def test_commit_explicit_only(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    _patch(monkeypatch, settings)
    ingested = CliRunner().invoke(
        app,
        [
            "conversations",
            "ingest",
            "--peer",
            "peer_1",
            "--url",
            "https://x.com/u/2",
            "--body",
            "I will send an example Friday",
            "--direction",
            "outbound",
            "--json",
        ],
    )
    assert ingested.exit_code == 0, ingested.output
    data = json.loads(ingested.output)
    rec_id = data["record"]["id"]
    thread_id = data["thread_id"]
    r = CliRunner().invoke(
        app,
        [
            "conversations",
            "commit",
            thread_id,
            "--text",
            "Send example by Friday",
            "--source-ref",
            rec_id,
            "--json",
        ],
    )
    assert r.exit_code == 0, r.output
    thread = ConversationThreadRepository(ws).get(thread_id)
    assert len(thread.commitments) == 1
