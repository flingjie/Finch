"""Codex CLI 非交互调用封装（spec 3.1）。"""

import json
import tempfile
from pathlib import Path

from pydantic import BaseModel

from ..github.gh_client import _run
from .structured_output import model_to_json_schema, parse_checked


class CodexRunner:
    def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 180.0) -> BaseModel:
        with tempfile.TemporaryDirectory() as td:
            schema_path = Path(td) / "schema.json"
            out_path = Path(td) / "out.json"
            schema_path.write_text(json.dumps(model_to_json_schema(output_model)))
            argv = [
                "codex", "exec",
                "--output-schema", str(schema_path),
                "--json",
                "-o", str(out_path),
                "--ephemeral",
                "--skip-git-repo-check",
                prompt,
            ]
            r = _run(argv, timeout=timeout)
            if not r["ok"]:
                raise RuntimeError(f"codex exec failed: {r['stderr'].strip() or r['stdout'].strip()}")
            if not out_path.exists():
                raise RuntimeError("codex exec produced no output file")
            data = json.loads(out_path.read_text())
        return parse_checked(data, output_model)
