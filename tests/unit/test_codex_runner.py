import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from finch.codex.runner import CodexRunner
from finch.codex.structured_output import StructuredOutputError, model_to_json_schema, parse_checked


class FakeOut(BaseModel):
    name: str
    score: int


def _run_success(argv, timeout):
    return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}


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


def test_runner_invokes_codex_and_parses(tmp_path, monkeypatch):
    schema_written_path = None

    def fake_run(argv, timeout):
        nonlocal schema_written_path
        assert argv[0] == "codex"
        i = argv.index("--output-schema")
        schema_written_path = argv[i + 1]
        out_i = argv.index("-o")
        out_path = argv[out_i + 1]
        # Verify schema was written before temp dir cleanup
        assert Path(schema_written_path).exists()
        Path(out_path).write_text(json.dumps({"name": "y", "score": 9}))
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr("finch.codex.runner._run", fake_run)
    r = CodexRunner().run("please extract", FakeOut, timeout=10.0)
    assert isinstance(r, FakeOut)
    assert r.name == "y"
