"""GraphContext envelope: pass structured data between nodes (spec C3)."""

import json
from typing import TypeVar, Generic

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class MissingContextError(KeyError):
    """Raised when a node requires a context key that hasn't been written."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(",".join(missing))


class GraphContext:
    """Context envelope that passes data between nodes.

    Uses a dict of {writes_key: {"items": [...]}} to store outputs from nodes.
    """

    def __init__(self) -> None:
        self.outputs: dict[str, dict] = {}

    def hydrate(self, writes: str, output_json: str) -> None:
        """Rehydrate context from persisted node output.

        A no-op if writes is empty. Treats empty or "{}" output_json as {}.
        """
        if not writes or writes == "":
            return

        if not output_json or output_json == "{}":
            parsed = {}
        else:
            parsed = json.loads(output_json)

        self.outputs[writes] = parsed

    def project(self, reads: list[str]) -> dict[str, dict]:
        """Project context for node reads.

        Returns {key: items_dict} for each read key.
        Raises MissingContextError if any required key is missing.
        """
        if not reads:
            return {}

        missing = [k for k in reads if k not in self.outputs]
        if missing:
            raise MissingContextError(missing)

        return {k: self.outputs[k] for k in reads}

    def put(self, writes: str, output: dict) -> None:
        """Store output for a node's writes key.

        A no-op if writes is empty.
        """
        if not writes or writes == "":
            return

        self.outputs[writes] = output


def items_payload(models: list[BaseModel]) -> dict:
    """Convert a list of Pydantic models to the items payload format.

    Returns {"items": [model_dump(mode="json"), ...]}.
    """
    return {"items": [m.model_dump(mode="json") for m in models]}


def parse_items(payload: dict, model: type[T]) -> list[T]:
    """Parse items payload back to a list of Pydantic models."""
    items = payload.get("items", [])
    return [model.model_validate(m) for m in items]
