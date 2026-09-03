import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from finch.codex.runner import CodexRunner
from finch.codex.structured_output import (
    StructuredOutputError,
    load_json,
    model_to_json_schema,
    parse_checked,
)


class FakeOut(BaseModel):
    name: str
    score: int


def test_model_to_json_schema():
    s = model_to_json_schema(FakeOut)
    assert s["type"] == "object"
    assert "name" in s["properties"]


def test_parse_checked_valid():
    out = parse_checked({"name": "x", "score": 3}, FakeOut)
    assert isinstance(out, FakeOut)
    assert out.score == 3


def test_parse_checked_invalid_raises():
    with pytest.raises(StructuredOutputError):
        parse_checked({"name": "x"}, FakeOut)  # 缺 score


def test_load_json_strips_markdown_fence():
    raw = '```json\n{"name": "x", "score": 3}\n```'
    assert load_json(raw) == {"name": "x", "score": 3}


def test_load_json_extracts_object_from_prose():
    raw = 'here is the result:\n{"name": "x", "score": 3}\nthanks'
    assert load_json(raw) == {"name": "x", "score": 3}


def test_runner_invokes_codex_and_parses(tmp_path, monkeypatch):
    schema_written = {}
    stdin_seen = {}
    argv_seen = []

    def fake_run(argv, timeout, stdin=None):
        assert argv[0] == "codex"
        assert argv[-1] == "-"
        argv_seen.append(argv)
        stdin_seen["prompt"] = stdin
        i = argv.index("--output-schema")
        schema_written["path"] = argv[i + 1]
        out_i = argv.index("-o")
        out_path = argv[out_i + 1]
        Path(out_path).write_text(json.dumps({"name": "y", "score": 9}))
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr("finch.codex.runner._run", fake_run)
    r = CodexRunner().run("please extract", FakeOut, timeout=10.0)
    assert isinstance(r, FakeOut)
    assert r.name == "y"
    assert stdin_seen["prompt"] == "please extract"
    # Verify argv ends with "-" (prompt via stdin)
    assert argv_seen[0][-1] == "-"


def test_runner_parses_fenced_output(tmp_path, monkeypatch):
    def fake_run(argv, timeout, stdin=None):
        out_i = argv.index("-o")
        out_path = argv[out_i + 1]
        Path(out_path).write_text('```json\n{"name": "y", "score": 9}\n```')
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr("finch.codex.runner._run", fake_run)
    r = CodexRunner().run("please extract", FakeOut, timeout=10.0)
    assert isinstance(r, FakeOut)
    assert (r.name, r.score) == ("y", 9)


def test_runner_retries_on_invalid_output(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_run(argv, timeout, stdin=None):
        calls["n"] += 1
        out_i = argv.index("-o")
        out_path = argv[out_i + 1]
        if calls["n"] == 1:
            Path(out_path).write_text(json.dumps({"name": "y"}))  # 缺 score
        else:
            Path(out_path).write_text(json.dumps({"name": "y", "score": 9}))
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr("finch.codex.runner._run", fake_run)
    r = CodexRunner().run("please extract", FakeOut, timeout=10.0)
    assert (r.name, r.score) == ("y", 9)
    assert calls["n"] == 2
