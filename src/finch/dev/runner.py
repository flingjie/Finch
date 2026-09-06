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
    """跑 spec.test_targets（子进程 pytest），返回退出码。

    超时不算测试失败，返回 124（GNU timeout 约定）而非裸 traceback。
    """
    argv = ["uv", "run", "pytest", *spec.test_targets, "-q"]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        print(f"timeout after {timeout}s: {' '.join(argv)}", file=sys.stderr)
        return 124
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    return proc.returncode


def run_node(name: str, fixture_path: Path, settings: Settings) -> NodeResult:
    """读 fixture（GraphContext envelope）、按节点 ``reads`` 校验、装配无状态节点并运行。

    领域错误统一抛 KeyError/ValueError（由 CLI 转成一行错误 + 退出码 1）；文件缺失
    等 OSError 也归一为 ValueError，保证调用方只需捕获这两个异常。
    """
    spec = REGISTRY.get(name)
    if spec is None:
        raise KeyError(f"unknown feature: {name}")
    if spec.kind != "graph_node":
        raise ValueError(f"feature {name!r} is a {spec.kind}; use `finch dev test {name}`")
    if spec.build is None:
        raise ValueError(f"feature {name!r} has no build (not a runnable graph_node)")
    try:
        raw = fixture_path.read_text()
    except OSError as exc:
        raise ValueError(f"cannot read fixture {fixture_path}: {exc}") from exc
    try:
        ctx = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"fixture {fixture_path} is not valid JSON: {exc}") from exc
    if not isinstance(ctx, dict):
        raise ValueError(
            f"fixture must decode to a JSON object, got {type(ctx).__name__}"
        )
    node = spec.build(DevContext(settings=settings))
    missing = [k for k in node.reads if k not in ctx]
    if missing:
        raise ValueError(f"fixture missing required inputs: {missing}")
    ctx.setdefault("run_id", "dev-run-node")
    return node.run(ctx)
