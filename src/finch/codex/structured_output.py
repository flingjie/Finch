"""把 Pydantic 模型转成 Codex --output-schema 的 JSON Schema，并校验解析结果。"""

from pydantic import BaseModel, ValidationError


class StructuredOutputError(RuntimeError):
    pass


def model_to_json_schema(model: type[BaseModel]) -> dict:
    return model.model_json_schema()


def parse_checked(data: dict, model: type[BaseModel]) -> BaseModel:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise StructuredOutputError(
            f"invalid structured output for {model.__name__}: {exc}"
        ) from exc
