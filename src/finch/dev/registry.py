"""dev harness 注册表：FeatureSpec 单点真源（纯数据，无 IO）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from finch.settings import Settings


@dataclass(frozen=True)
class DevContext:
    """功能点的依赖上下文；MVP 只有 settings，后续按需加 store / runners。"""

    settings: Settings


@dataclass(frozen=True)
class FeatureSpec:
    """单个功能点的元数据：CLI、test 都复用它。"""

    name: str
    kind: Literal["function"]
    description: str
    test_targets: tuple[str, ...] = ()


_SPECS: list[FeatureSpec] = [
    FeatureSpec(
        name="select_groups",
        kind="function",
        description="每日预算：group 排序 + 预算选择（确定性）",
        test_targets=("tests/unit/test_budget.py",),
    ),
    FeatureSpec(
        name="rank_pending",
        kind="function",
        description="每日预算：pending 预排序（确定性）",
        test_targets=("tests/unit/test_budget.py",),
    ),
    FeatureSpec(
        name="select_planning_evidence",
        kind="function",
        description="plan_topics 前裁剪证据卡（确定性）",
        test_targets=("tests/unit/test_jobs.py",),
    ),
    FeatureSpec(
        name="scan_cards",
        kind="function",
        description="证据卡安全扫描（确定性）",
        test_targets=("tests/unit/test_safety.py",),
    ),
]


def _build_registry(specs: list[FeatureSpec]) -> dict[str, FeatureSpec]:
    seen: set[str] = set()
    registry: dict[str, FeatureSpec] = {}
    for spec in specs:
        if spec.name in seen:
            raise ValueError(f"duplicate feature name: {spec.name}")
        seen.add(spec.name)
        registry[spec.name] = spec
    return registry


REGISTRY: dict[str, FeatureSpec] = _build_registry(_SPECS)
