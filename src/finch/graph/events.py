"""领域事件与节点结果契约（spec 6.3）。"""

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DomainEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    type: str
    occurred_at: datetime = Field(default_factory=_utcnow)
    payload: dict = Field(default_factory=dict)


class NodeResult(BaseModel):
    status: Literal["succeeded", "failed", "needs_input", "partial"]
    output: dict = Field(default_factory=dict)
    events: list[DomainEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    retryable: bool = False
    error_code: str | None = None
