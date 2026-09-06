"""dev harness 注册表：FeatureSpec 单点真源（纯数据，无 IO）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from finch.settings import Settings

if TYPE_CHECKING:
    from finch.graph.nodes import Node


@dataclass(frozen=True)
class DevContext:
    """装配节点的依赖上下文；MVP 只有 settings，后续加 store / runners。"""

    settings: Settings


@dataclass(frozen=True)
class FeatureSpec:
    """单个功能点的元数据：CLI、run-node、test 都复用它。

    不存 required_inputs——run-node 直接读装配后节点的 ``reads``，避免与节点契约漂移。
    ``build`` 内部延迟导入节点工厂，使 `finch dev list-features` 不必拖入 content/match 管线。
    """

    name: str
    kind: Literal["graph_node", "function"]
    description: str
    test_targets: tuple[str, ...] = ()
    build: Callable[[DevContext], Node] | None = None


def _build_recall(ctx: DevContext) -> Node:
    """延迟装配 recall 节点（避免 import registry 即拖入 evidence/match 管线）。"""
    from finch.graph.match_nodes import make_recall_node

    return make_recall_node(ctx.settings.quality_gates)


def _build_brief(ctx: DevContext) -> Node:
    """延迟装配 brief 节点（避免 import registry 即拖入 content 管线）。"""
    from finch.graph.content_nodes import make_brief_node

    return make_brief_node(ctx.settings.quality_gates)


_SPECS: list[FeatureSpec] = [
    FeatureSpec(
        name="recall",
        kind="graph_node",
        description="确定性召回：candidates × cards → ranked_candidates（Jaccard）",
        test_targets=("tests/graph/test_match_nodes.py",),
        build=_build_recall,
    ),
    FeatureSpec(
        name="brief",
        kind="graph_node",
        description="每日简报渲染（确定性，jobs_repo=None）",
        test_targets=("tests/graph/test_content_nodes.py",),
        build=_build_brief,
    ),
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
        name="select_primary_job",
        kind="function",
        description="选出唯一 primary ContentJob（确定性）",
        test_targets=("tests/unit/test_jobs.py",),
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
