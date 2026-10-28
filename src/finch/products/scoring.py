"""产品确定性基础分（无 LLM；热度/明星数只作上下文，不参与总分）。

维度是产品信号，不是旧 ``score_person`` 的关系维度。总分只在代码里算。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from finch.sources.models import RawArtifact

WEIGHTS = {
    "launch_signal": 25,
    "founder_build": 20,
    "problem_clarity": 20,
    "evidence_depth": 20,
    "recency": 15,
}

_LAUNCH_MARKERS = (
    "launch",
    "launched",
    "shipped",
    "show hn",
    "product hunt",
    "发布",
    "上线",
    "推出",
)

_FOUNDER_BUILD_MARKERS = (
    "i built",
    "we built",
    "we launched",
    "i made",
    "我做了",
    "我开发了",
    "我们发布了",
    "我们做了",
)

_PROBLEM_MARKERS = (
    "pain",
    "struggle",
    "痛点",
    "难",
    "问题",
    "解决",
    "for those who",
)


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(m in lowered for m in markers)


@dataclass(frozen=True)
class ProductScoreBreakdown:
    launch_signal: float
    founder_build: float
    problem_clarity: float
    evidence_depth: float
    recency: float
    total: float


def score_product(
    evidence: list[RawArtifact], *, now: datetime | None = None
) -> ProductScoreBreakdown:
    """按产品信号确定性打分；`total` 落在 [0,1]。"""
    clock = now or datetime.now(UTC)
    texts = [(a.title or "") + "\n" + a.text for a in evidence]

    launch = 1.0 if any(_has_any(t, _LAUNCH_MARKERS) for t in texts) else 0.0
    founder_build = 1.0 if any(_has_any(t, _FOUNDER_BUILD_MARKERS) for t in texts) else 0.0
    problem_clarity = 1.0 if any(_has_any(t, _PROBLEM_MARKERS) for t in texts) else 0.0

    distinct = len({a.artifact_id for a in evidence})
    evidence_depth = _clamp01(distinct / 3.0)

    published = [
        a.published_at or a.retrieved_at
        for a in evidence
        if a.published_at or a.retrieved_at
    ]
    recency = 0.0
    if published:
        newest = max(published)
        if newest >= clock - timedelta(days=7):
            recency = 1.0
        elif newest >= clock - timedelta(days=30):
            recency = 0.5
        else:
            recency = 0.2

    total = (
        launch * WEIGHTS["launch_signal"]
        + founder_build * WEIGHTS["founder_build"]
        + problem_clarity * WEIGHTS["problem_clarity"]
        + evidence_depth * WEIGHTS["evidence_depth"]
        + recency * WEIGHTS["recency"]
    ) / 100.0

    return ProductScoreBreakdown(
        launch_signal=launch,
        founder_build=founder_build,
        problem_clarity=problem_clarity,
        evidence_depth=evidence_depth,
        recency=recency,
        total=_clamp01(total),
    )
