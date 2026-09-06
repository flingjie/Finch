"""dev harness 编排：list_features / run_test / run_node。"""

import json
import subprocess
import sys
from pathlib import Path

from finch.graph.events import NodeResult
from finch.settings import Settings

from .registry import REGISTRY, DevContext, FeatureSpec


def list_features() -> list[FeatureSpec]:
    return list(REGISTRY.values())


def run_test(spec: FeatureSpec, timeout: float = 300.0) -> int:
    """跑 spec.test_targets（子进程 pytest），返回退出码。"""
    argv = ["uv", "run", "pytest", *spec.test_targets, "-q"]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    return proc.returncode


def run_node(name: str, fixture_path: Path, settings: Settings) -> NodeResult:
    """读 fixture（GraphContext envelope）、校验 required_inputs、装配无状态节点并运行。"""
    spec = REGISTRY.get(name)
    if spec is None:
        raise KeyError(f"unknown feature: {name}")
    if spec.kind != "graph_node":
        raise ValueError(f"feature {name!r} is a {spec.kind}; use `finch dev test {name}`")
    ctx = json.loads(fixture_path.read_text())
    missing = [k for k in spec.required_inputs if k not in ctx]
    if missing:
        raise ValueError(f"fixture missing required inputs: {missing}")
    ctx.setdefault("run_id", "dev-run-node")
    assert spec.build is not None
    node = spec.build(DevContext(settings=settings))
    return node.run(ctx)
