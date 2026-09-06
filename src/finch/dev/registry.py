"""dev harness 注册表：FeatureSpec 单点真源（纯数据，无 IO）。"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from finch.graph.content_nodes import make_brief_node
from finch.graph.match_nodes import make_recall_node
from finch.graph.nodes import Node
from finch.settings import Settings


@dataclass(frozen=True)
class DevContext:
    """装配节点的依赖上下文；MVP 只有 settings，后续加 store / runners。"""

    settings: Settings


@dataclass(frozen=True)
class FeatureSpec:
    """单个功能点的元数据：CLI、run-node、test 都复用它。"""

    name: str
    kind: Literal["graph_node", "function"]
    description: str
    test_targets: tuple[str, ...] = ()
    build: Callable[[DevContext], Node] | None = None
    required_inputs: tuple[str, ...] = ()


_SPECS: list[FeatureSpec] = [
    FeatureSpec(
        name="recall",
        kind="graph_node",
        description="确定性召回：candidates × cards → ranked_candidates（Jaccard）",
        test_targets=("tests/graph/test_match_nodes.py",),
        build=lambda ctx: make_recall_node(ctx.settings.quality_gates),
        required_inputs=("candidates", "evidence_cards"),
    ),
    FeatureSpec(
        name="brief",
        kind="graph_node",
        description="每日简报渲染（确定性，jobs_repo=None）",
        test_targets=("tests/graph/test_content_nodes.py",),
        build=lambda ctx: make_brief_node(ctx.settings.quality_gates),
        required_inputs=(
            "drafts", "content_jobs", "evidence_cards",
            "ready_jobs", "candidates", "match_results",
        ),
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
