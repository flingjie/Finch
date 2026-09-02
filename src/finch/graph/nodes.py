"""Graph 节点：声明契约 + 基类行为。"""

from typing import Literal

from pydantic import BaseModel

from .events import NodeResult


class Node(BaseModel):
    """spec 6.3：每个节点必须声明的契约字段。"""

    name: str
    timeout_seconds: float = 60.0
    max_retries: int = 3
    idempotency_key: str = "default"
    side_effect: Literal["none", "read", "write"] = "none"
    reads: list[str] = []
    writes: str = ""
    succeeds_to: str = ""

    def run(self, ctx: dict) -> NodeResult:
        return NodeResult(status="succeeded", output={})


class NoopNode(Node):
    def run(self, ctx: dict) -> NodeResult:
        return NodeResult(status="succeeded", output={})


class FailingNode(Node):
    retryable: bool = False
    error_code: str = "E_FAILED"

    def run(self, ctx: dict) -> NodeResult:
        return NodeResult(
            status="failed",
            output={},
            retryable=self.retryable,
            error_code=self.error_code,
        )
