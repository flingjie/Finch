"""跨平台 OpenCLI 访问层：gateway、能力探测、只读策略与 Source Connector。"""

from .models import (
    OpenCliCapabilities,
    OpenCliRequest,
    OpenCliResult,
    RawArtifact,
    ResultKind,
    Source,
    SourceCommandBlocked,
    SourceStatus,
)

__all__ = [
    "OpenCliCapabilities",
    "OpenCliRequest",
    "OpenCliResult",
    "RawArtifact",
    "ResultKind",
    "Source",
    "SourceCommandBlocked",
    "SourceStatus",
]
