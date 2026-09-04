"""Codex CLI 非交互调用封装（spec 3.1）。"""

import json
import tempfile
from pathlib import Path

from pydantic import BaseModel

from ..github.gh_client import _run
from .structured_output import (
    StructuredOutputError,
    load_json,
    model_to_json_schema,
    parse_checked,
)


class CodexRunner:
    def run(self,
            prompt: str,
            output_model: type[BaseModel],
            *,
            timeout: float = 600.0,
            max_attempts: int = 3) -> BaseModel:
        """非交互调用 `codex exec`，把 prompt 经 stdin 传入，输出经 JSON Schema 校验。

        Raises RuntimeError on subprocess failure or missing output;
        StructuredOutputError on validation failure.
        """
        last_error: Exception | None = None
        for _ in range(max_attempts):
            try:
                return self._run_once(prompt, output_model, timeout=timeout)
            except (json.JSONDecodeError, StructuredOutputError) as exc:
                last_error = exc
        raise StructuredOutputError(
            f"codex exec failed to produce valid structured output after "
            f"{max_attempts} attempts: {last_error}"
        ) from last_error

    def _run_once(self, prompt: str, output_model: type[BaseModel], *, timeout: float) -> BaseModel:
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
                "-",
            ]
            r = _run(argv, timeout=timeout, stdin=prompt)
            if not r["ok"]:
                raise RuntimeError(
                    f"codex exec failed: {r['stderr'].strip() or r['stdout'].strip()}"
                )
            if not out_path.exists():
                raise RuntimeError("codex exec produced no output file")
            data = load_json(out_path.read_text())
        return parse_checked(data, output_model)
