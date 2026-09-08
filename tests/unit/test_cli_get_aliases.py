"""Agent 读取别名：peers get / conversations get 默认 JSON。"""

import typer

from finch import cli
from finch.cli import conversations_get, peers_get
from finch.settings import Paths, Settings


def test_peers_get_missing_exits_1(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(paths=Paths(var_dir=tmp_path)))
    try:
        peers_get(peer_id="nope")
    except typer.Exit as exc:
        assert exc.exit_code == 1
    out = capsys.readouterr().out
    assert "peer not found" in out


def test_conversations_get_missing_exits_1(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(paths=Paths(var_dir=tmp_path)))
    try:
        conversations_get(conversation_id="nope")
    except typer.Exit as exc:
        assert exc.exit_code == 1
    out = capsys.readouterr().out
    assert "conversation not found" in out
