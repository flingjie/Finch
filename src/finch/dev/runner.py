"""dev harness 编排：list_features / run_test。"""

import subprocess
import sys

from .registry import REGISTRY, FeatureSpec


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
