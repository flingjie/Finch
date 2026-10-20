"""CLI tests for the unified connections write path (record / follow-up / review / today)."""

import json

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.conversations.models import FollowUpTrigger, ReviewDisposition
from finch.conversations.service import thread_id_for
from finch.peers.service import PeerService
from finch.settings import Paths, Settings
from finch.storage.repositories import (
    ConversationThreadRepository,
    InteractionRecordRepository,
    PeerRepository,
)
from finch.storage.workspace import Workspace


def _settings(tmp_path) -> Settings:
    return Settings(paths=Paths(var_dir=tmp_path))


def _seed_peer(ws: Workspace) -> str:
    peer = PeerService().from_author(platform="x", author_id="alice", username="alice")
    PeerRepository(ws).upsert(peer)
    return peer.id


def test_connections_record_follow_up_review_today_flow(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    peer_id = _seed_peer(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    # 1. record (outbound) twice with stable request id → second reused.
    args = ["connections", "record", "--person", peer_id, "--body", "hi", "--request-id", "r1"]
    r1 = CliRunner().invoke(app, args)
    assert r1.exit_code == 0, r1.output
    assert "(reused)" not in r1.output
    r2 = CliRunner().invoke(app, args)
    assert r2.exit_code == 0, r2.output
    assert "(reused)" in r2.output
    assert len(InteractionRecordRepository(ws).list_all()) == 1

    # 2. follow-up on the thread without an opportunity (inbound).
    tid = thread_id_for(peer_id, "general")
    fu = CliRunner().invoke(
        app, ["connections", "follow-up", "--thread", tid, "--reply-body", "got it", "--json"]
    )
    assert fu.exit_code == 0, fu.output
    assert len(InteractionRecordRepository(ws).list_all()) == 2

    # 3. review disposition.
    rev = CliRunner().invoke(
        app, ["connections", "review", tid, "--disposition", "waiting"]
    )
    assert rev.exit_code == 0, rev.output
    thread = ConversationThreadRepository(ws).get(tid)
    assert thread is not None
    assert thread.review_disposition is ReviewDisposition.WAITING
    assert FollowUpTrigger.NEW_REPLY in thread.pending_triggers

    # 4. today projection lists the thread (NEW_REPLY is a contact reason).
    today = CliRunner().invoke(app, ["connections", "today", "--json"])
    assert today.exit_code == 0, today.output
    actions = json.loads(today.output)
    assert [a["primary_thread_id"] for a in actions] == [tid]


def test_connections_record_occurred_at_parses_naive_as_utc(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    peer_id = _seed_peer(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(
        app,
        [
            "connections", "record", "--person", peer_id,
            "--body", "hi", "--occurred-at", "2026-09-01T12:00:00",
        ],
    )
    assert r.exit_code == 0, r.output
    records = InteractionRecordRepository(ws).list_all()
    assert len(records) == 1
    assert records[0].occurred_at.isoformat() == "2026-09-01T12:00:00+00:00"


def test_connections_audit_reports_empty_when_clean(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    _seed_peer(ws)
    monkeypatch.setattr(cli, "load_settings", lambda: settings)

    r = CliRunner().invoke(app, ["connections", "audit", "--json"])
    assert r.exit_code == 0, r.output
    report = json.loads(r.output)
    assert report == {
        "duplicate_messages": [],
        "unknown_direction": [],
        "orphan_interactions": [],
        "unconfirmed_identity": [],
        "future_occurred_at": [],
    }
