"""双轨调度：原创轨道与互动轨道每轮必执行，故障隔离（同步顺序执行）。

对应执行计划 Phase 1 的 ``asyncio.gather(..., return_exceptions=True)`` 语义：仓库 runtime 为
同步顺序执行，两条轨道仍独立运行；任一轨道异常或返回失败状态时，另一条轨道的有效结果保留，
汇总结果显式标记部分失败，不把整轮误报为完全成功。
"""

from collections.abc import Callable
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel

from ..engagement.flow import EngagementRunResult
from ..storage.database import RunRecord
from .state import GraphState

_ORIGINAL_FAILED_STATES = {GraphState.FAILED.value, GraphState.BLOCKED.value}


class TrackError(BaseModel):
    """单条轨道抛出异常的捕获结果（不重新抛出）。"""

    type: str
    message: str


class DualTrackResult(BaseModel):
    """双轨汇总：每条轨道要么有结果、要么有捕获的异常。

    ``partial_failure`` 为派生属性：任一轨道异常、或返回失败状态（原创轨道 FAILED/BLOCKED，
    互动轨道 status="failed"）即为 True；``status`` 汇总两条轨道，失败时不报告完全成功。
    """

    run_id: str
    original: RunRecord | None = None
    original_error: TrackError | None = None
    engagement: EngagementRunResult | None = None
    engagement_error: TrackError | None = None

    @property
    def original_failed(self) -> bool:
        if self.original_error is not None:
            return True
        if self.original is not None and self.original.state in _ORIGINAL_FAILED_STATES:
            return True
        return False

    @property
    def engagement_failed(self) -> bool:
        if self.engagement_error is not None:
            return True
        if self.engagement is not None and self.engagement.status == "failed":
            return True
        return False

    @property
    def partial_failure(self) -> bool:
        return self.original_failed or self.engagement_failed

    @property
    def status(self) -> Literal["succeeded", "partial_failure", "failed"]:
        if not self.partial_failure:
            return "succeeded"
        if self.original_failed and self.engagement_failed:
            return "failed"
        return "partial_failure"


def _capture[TrackT](
    track: Callable[[str], TrackT], run_id: str
) -> tuple[TrackT | None, TrackError | None]:
    """执行单条轨道并把异常捕获为 ``TrackError``（return_exceptions=True 的同步等价）。"""
    try:
        return track(run_id), None
    except Exception as exc:  # noqa: BLE001 - 一条轨道失败不得抹掉另一条轨道的有效结果
        return None, TrackError(type=type(exc).__name__, message=str(exc))


def run_dual_track(
    *,
    run_id: str | None = None,
    original_track: Callable[[str], RunRecord],
    engagement_track: Callable[[str], EngagementRunResult],
) -> DualTrackResult:
    """顺序执行两条轨道，共享同一 ``run_id``，并汇总为 ``DualTrackResult``。

    两条轨道各自以 try/except 隔离：异常被捕获为该轨道的结果，不重新抛出；任一轨道失败
    都会让 ``DualTrackResult.partial_failure`` 为 True。
    """
    run_id = run_id or uuid4().hex
    original, original_error = _capture(original_track, run_id)
    engagement, engagement_error = _capture(engagement_track, run_id)
    return DualTrackResult(
        run_id=run_id,
        original=original,
        original_error=original_error,
        engagement=engagement,
        engagement_error=engagement_error,
    )
