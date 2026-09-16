"""跨平台 Source 层统一数据模型。"""

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Source(StrEnum):
    """采集源标识（connector 名；Twitter 存储身份仍用 platform=x）。"""

    TWITTER = "twitter"
    REDDIT = "reddit"
    GITHUB = "github"
    V2EX = "v2ex"
    WEIXIN = "weixin"
    XIAOHONGSHU = "xiaohongshu"


class ResultKind(StrEnum):
    """OpenCLI / 子进程退出码在 Finch 侧的分类。"""

    SUCCESS = "SUCCESS"
    EMPTY = "EMPTY"
    BRIDGE_DOWN = "BRIDGE_DOWN"
    TIMEOUT = "TIMEOUT"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    CONFIG_ERROR = "CONFIG_ERROR"
    CANCELLED = "CANCELLED"
    UNAVAILABLE = "UNAVAILABLE"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


class OpenCliExitCode(IntEnum):
    """OpenCLI 约定退出码（以本机 opencli 为准；未知码落入 ERROR）。"""

    SUCCESS = 0
    EMPTY = 66
    BRIDGE_DOWN = 69
    TIMEOUT = 75
    AUTH_REQUIRED = 77
    CONFIG_ERROR = 78
    CANCELLED = 130


EXIT_CODE_TO_KIND: dict[int, ResultKind] = {
    OpenCliExitCode.SUCCESS: ResultKind.SUCCESS,
    OpenCliExitCode.EMPTY: ResultKind.EMPTY,
    OpenCliExitCode.BRIDGE_DOWN: ResultKind.BRIDGE_DOWN,
    OpenCliExitCode.TIMEOUT: ResultKind.TIMEOUT,
    OpenCliExitCode.AUTH_REQUIRED: ResultKind.AUTH_REQUIRED,
    OpenCliExitCode.CONFIG_ERROR: ResultKind.CONFIG_ERROR,
    OpenCliExitCode.CANCELLED: ResultKind.CANCELLED,
}


class SourceStatus(StrEnum):
    """``finch sources doctor`` 单源状态。"""

    READY = "READY"
    DEGRADED = "DEGRADED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"


class SourceCommandBlocked(RuntimeError):
    """写命令或未知命令在进入 OpenCLI 前被 Finch 拒绝。"""


class AuthorIdentity(BaseModel):
    """原始工件上的作者身份（尚未合并到 Person）。"""

    platform: str
    external_id: str = ""
    handle: str = ""


class RawArtifact(BaseModel):
    """所有平台先映射为不可变原始对象。"""

    artifact_id: str
    source: Source
    source_type: str
    source_id: str
    canonical_url: str = ""
    author_identity: AuthorIdentity
    title: str | None = None
    text: str = ""
    published_at: datetime | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: datetime
    capture_method: Literal["adapter", "browser", "url_import", "gh"] = "adapter"
    raw_ref: str = ""
    schema_version: int = 1
    content_fingerprint: str = ""


class OpenCliRequest(BaseModel):
    """一次只读 OpenCLI / 外部 CLI 调用请求。"""

    model_config = {"frozen": True}

    surface: str
    command: str
    args: tuple[str, ...] = ()
    profile: str | None = None
    timeout_seconds: int = 60
    access: Literal["read"] = "read"
    use_browser_lock: bool = True


class OpenCliResult(BaseModel):
    """OpenCLI 调用结果（已分类、已解析行）。"""

    rows: list[dict[str, Any]] = Field(default_factory=list)
    exit_code: int | None = None
    kind: ResultKind
    duration_ms: int = 0
    stderr_summary: str | None = None
    capability_snapshot_id: str = ""
    stdout_raw: str = ""


class OpenCliCapabilities(BaseModel):
    """``opencli list -f json`` 能力快照。"""

    snapshot_id: str
    captured_at: datetime
    surfaces: dict[str, list[str]] = Field(default_factory=dict)
    raw: list[dict[str, Any]] = Field(default_factory=list)


class DoctorSourceReport(BaseModel):
    """单个采集源的自检报告。"""

    source: Source
    status: SourceStatus
    detail: str = ""
    commands_seen: list[str] = Field(default_factory=list)


class DoctorReport(BaseModel):
    """``finch sources doctor`` 汇总。"""

    opencli_ok: bool
    opencli_detail: str = ""
    capability_snapshot_id: str = ""
    profile: str | None = None
    sources: list[DoctorSourceReport] = Field(default_factory=list)


def map_exit_code(exit_code: int | None, *, stderr: str = "") -> ResultKind:
    """将子进程退出码（及缺失二进制）映射为 ResultKind。"""
    if exit_code is None:
        lowered = (stderr or "").lower()
        if "timeout" in lowered:
            return ResultKind.TIMEOUT
        if "not found" in lowered:
            return ResultKind.UNAVAILABLE
        return ResultKind.UNAVAILABLE
    kind = EXIT_CODE_TO_KIND.get(exit_code)
    if kind is not None:
        return kind
    # Heuristic fallback when OpenCLI returns generic non-zero without typed codes.
    lowered = (stderr or "").lower()
    if "not logged in" in lowered or "login" in lowered or "auth" in lowered:
        return ResultKind.AUTH_REQUIRED
    if "bridge" in lowered or "daemon" in lowered:
        return ResultKind.BRIDGE_DOWN
    if "timeout" in lowered:
        return ResultKind.TIMEOUT
    if exit_code == 0:
        return ResultKind.SUCCESS
    return ResultKind.ERROR


def command_prefix(surface: str, command: str) -> str:
    """``twitter search`` 形式的策略键。"""
    return f"{surface} {command}".strip()
