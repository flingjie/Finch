"""把 Pydantic 模型转成 Codex --output-schema 的 JSON Schema，并校验解析结果。"""

import json

from pydantic import BaseModel, ValidationError


class StructuredOutputError(RuntimeError):
    pass


def model_to_json_schema(model: type[BaseModel]) -> dict:
    return model.model_json_schema()


def load_json(text: str) -> dict:
    """Parse a JSON document, tolerating markdown code fences from codex."""
    s = text.strip()
    if s.startswith("```"):
        s = s[3:].lstrip()
        # Drop an optional language identifier on the first line.
        if s and s[0] not in "{[":
            _, _, rest = s.partition("\n")
            s = rest or s
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].rstrip()
        s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # Fall back to the outermost JSON object if prose surrounds it.
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(s[start:end + 1])
        raise


def parse_checked(data: dict, model: type[BaseModel]) -> BaseModel:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise StructuredOutputError(
            f"invalid structured output for {model.__name__}: {exc}"
        ) from exc
