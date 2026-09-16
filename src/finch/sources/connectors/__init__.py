"""Source Connector 协议与发现上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from finch.sources.models import (
    OpenCliCapabilities,
    OpenCliRequest,
    OpenCliResult,
    RawArtifact,
    Source,
    SourceStatus,
)


@dataclass
class DiscoveryContext:
    """一轮采集的查询上下文。"""

    queries: list[str] = field(default_factory=list)
    limit: int = 20
    urls: list[str] = field(default_factory=list)
    cursor: str | None = None
    extra: dict = field(default_factory=dict)


class SourceConnector(Protocol):
    """每个平台：探测能力 → 生成查询计划 → 标准化为 RawArtifact。"""

    source: Source

    def probe(self, capabilities: OpenCliCapabilities) -> SourceStatus: ...

    def plan(
        self,
        context: DiscoveryContext,
        capabilities: OpenCliCapabilities | None = None,
    ) -> list[OpenCliRequest]: ...

    def normalize(self, result: OpenCliResult) -> list[RawArtifact]: ...
