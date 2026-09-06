# Finch Dev Harness — MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `finch dev` harness — a `FeatureSpec` registry (single source of truth) plus `list-features` / `test` / `run-node` commands, so every feature point gets a uniform, reproducible test-and-run entry without becoming a separate Skill.

**Architecture:** A `src/finch/dev/` package (`registry.py` pure data, `runner.py` orchestration, `cli.py` typer app). `FeatureSpec` marks each feature as `graph_node` (runnable via `run-node`, built by a `DevContext → Node` closure) or `function` (test-only). MVP runs only stateless/deterministic nodes.

**Tech Stack:** Python 3.12, Pydantic 2, typer, `uv`.

## Global Constraints

- Python 3.12+; lint `uv run ruff check .` (E,F,I,B,UP; line-length 100), types `uv run mypy src`, tests `uv run pytest`.
- Subprocess discipline: args as arrays, per-call timeouts (`run_test` uses `subprocess.run(["uv", "run", "pytest", ...])`).
- Deterministic-totals invariant unchanged; `run-node` has NO side effects (no ledger/cards/jobs writes, no LLM).
- Do not modify any existing `make_*` factory signature or graph node.
- Bilingual docstrings; match surrounding files.

---

### Task 1: `registry.py` (FeatureSpec single source of truth)

**Files:**
- Create: `src/finch/dev/__init__.py`
- Create: `src/finch/dev/registry.py`
- Test: `tests/unit/test_dev_registry.py`

**Interfaces:**
- Produces:
  - `DevContext(settings: Settings)` (frozen dataclass).
  - `FeatureSpec(name: str, kind: Literal["graph_node","function"], description: str, test_targets: tuple[str,...] = (), build: Callable[[DevContext], Node] | None = None, required_inputs: tuple[str,...] = ())` (frozen dataclass).
  - `REGISTRY: dict[str, FeatureSpec]` built from a module-level `_SPECS` list (duplicate `name` → ValueError at import).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_dev_registry.py`:

```python
from finch.dev.registry import REGISTRY, DevContext
from finch.settings import Settings


def test_registry_has_unique_names_and_valid_kinds():
    assert len(REGISTRY) >= 7
    for name, spec in REGISTRY.items():
        assert spec.name == name
        assert spec.kind in ("graph_node", "function")


def test_graph_node_build_returns_node_with_matching_reads():
    ctx = DevContext(settings=Settings())
    for name in ("recall", "brief"):
        spec = REGISTRY[name]
        assert spec.kind == "graph_node"
        node = spec.build(ctx)
        assert set(node.reads) == set(spec.required_inputs)


def test_function_has_no_build():
    for name in ("select_groups", "rank_pending", "select_primary_job",
                 "select_planning_evidence", "scan_cards"):
        spec = REGISTRY[name]
        assert spec.kind == "function"
        assert spec.build is None
        assert spec.test_targets
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dev_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finch.dev'`

- [ ] **Step 3: Write minimal implementation**

Create `src/finch/dev/__init__.py` (empty).

Create `src/finch/dev/registry.py`:

```python
"""dev harness 注册表：FeatureSpec 单点真源（纯数据，无 IO）。"""

from dataclasses import dataclass
from typing import Callable, Literal

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_dev_registry.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/finch/dev/__init__.py src/finch/dev/registry.py tests/unit/test_dev_registry.py
git commit -m "feat(dev): add FeatureSpec registry (single source of truth)"
```

---

### Task 2: `runner.py` (list_features / run_test / run_node)

**Files:**
- Create: `src/finch/dev/runner.py`
- Test: `tests/unit/test_dev_runner.py`

**Interfaces:**
- Produces:
  - `list_features() -> list[FeatureSpec]`
  - `run_test(spec: FeatureSpec, timeout: float = 300.0) -> int` (subprocess pytest, returns exit code)
  - `run_node(name: str, fixture_path: Path, settings: Settings) -> NodeResult` (loads fixture, validates `required_inputs`, builds node, injects `run_id`, returns `node.run(ctx)`)
- Consumes: `REGISTRY`, `DevContext` (Task 1).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_dev_runner.py`:

```python
import json

import pytest

from finch.dev.runner import run_node
from finch.settings import Settings


def _recall_fixture(tmp_path):
    p = tmp_path / "recall.json"
    p.write_text(json.dumps({
        "candidates": {"items": [
            {"id": "c1", "author_handle": "a", "text": "agent harness reliability", "url": "u"},
        ]},
        "evidence_cards": {"items": [
            {"id": "ev1", "event_id": "evt1", "claim": "agent harness reliability matters",
             "confidence": "SUPPORTED"},
        ]},
    }))
    return p


def test_run_node_recall_succeeds(tmp_path):
    result = run_node("recall", _recall_fixture(tmp_path), Settings())
    assert result.status == "succeeded"
    assert result.output["items"]  # ranked_candidates 非空


def test_run_node_missing_input_raises(tmp_path):
    p = tmp_path / "empty.json"
    p.write_text("{}")
    with pytest.raises(ValueError):
        run_node("recall", p, Settings())


def test_run_node_function_rejected(tmp_path):
    with pytest.raises(ValueError):
        run_node("select_groups", tmp_path / "x.json", Settings())


def test_run_node_unknown_feature(tmp_path):
    with pytest.raises(KeyError):
        run_node("nope", tmp_path / "x.json", Settings())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dev_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finch.dev.runner'`

- [ ] **Step 3: Write minimal implementation**

Create `src/finch/dev/runner.py`:

```python
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
    node = spec.build(DevContext(settings=settings))
    return node.run(ctx)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_dev_runner.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/finch/dev/runner.py tests/unit/test_dev_runner.py
git commit -m "feat(dev): add runner (list_features / run_test / run_node)"
```

---

### Task 3: `cli.py` + mount + sample fixture

**Files:**
- Create: `src/finch/dev/cli.py`
- Modify: `src/finch/cli.py`
- Create: `tests/fixtures/nodes/recall.json`
- Test: `tests/unit/test_dev_cli.py`

**Interfaces:**
- Produces: `dev_app` (typer) with `list-features`, `test`, `run-node`; mounted as `app.add_typer(dev_app, name="dev")`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_dev_cli.py`:

```python
from pathlib import Path

from typer.testing import CliRunner

from finch.cli import app


def test_list_features():
    r = CliRunner().invoke(app, ["dev", "list-features"])
    assert r.exit_code == 0
    assert "recall" in r.output
    assert "select_groups" in r.output


def test_run_node_recall(tmp_path, monkeypatch):
    from finch import cli as cli_mod

    monkeypatch.setattr(cli_mod, "load_settings", lambda: _settings(tmp_path))
    fixture = Path("tests/fixtures/nodes/recall.json")
    r = CliRunner().invoke(app, ["dev", "run-node", "recall", "--input", str(fixture)])
    assert r.exit_code == 0, r.output
    assert "succeeded" in r.output


def _settings(tmp_path):
    from finch.settings import Settings
    return Settings()  # run-node 不触碰 paths，默认 Settings 即可
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_dev_cli.py::test_list_features -v`
Expected: FAIL — `AttributeError`/usage error: `dev` command not found.

- [ ] **Step 3: Write minimal implementation**

**3a.** Create `src/finch/dev/cli.py`:

```python
"""finch dev CLI（typer）：列出/测试/运行功能点，无生产副作用。"""

import json
from pathlib import Path

import typer

from finch.settings import load_settings
from .registry import REGISTRY
from .runner import list_features, run_node, run_test

dev_app = typer.Typer(help="开发 harness：列出/测试/运行功能点（不触发生产副作用）。")


@dev_app.command("list-features")
def list_features_cmd() -> None:
    for spec in list_features():
        typer.echo(f"{spec.name}\t{spec.kind}\t{spec.description}")


@dev_app.command("test")
def test_cmd(
    feature: str = typer.Argument(..., help="FeatureSpec name (see list-features)"),
) -> None:
    spec = REGISTRY.get(feature)
    if spec is None:
        typer.echo(f"unknown feature: {feature}; run `finch dev list-features`")
        raise typer.Exit(code=1)
    raise typer.Exit(code=run_test(spec))


@dev_app.command("run-node")
def run_node_cmd(
    node: str = typer.Argument(..., help="graph_node name (see list-features)"),
    input_path: Path = typer.Option(..., "--input", help="GraphContext envelope JSON"),
) -> None:
    settings = load_settings()
    try:
        result = run_node(node, input_path, settings)
    except (KeyError, ValueError) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
```

**3b.** In `src/finch/cli.py`, add the import and mount (near the other `add_typer` calls):

```python
from .dev.cli import dev_app
```

and:

```python
app.add_typer(dev_app, name="dev")
```

**3c.** Create `tests/fixtures/nodes/recall.json`:

```json
{
  "candidates": {
    "items": [
      {
        "id": "c1",
        "source": "twitter",
        "author_handle": "alice",
        "text": "agent harness reliability",
        "url": "https://x.com/alice/status/1",
        "query_id": "q1"
      }
    ]
  },
  "evidence_cards": {
    "items": [
      {
        "id": "ev1",
        "event_id": "evt1",
        "claim": "agent harness reliability matters",
        "confidence": "SUPPORTED",
        "publishable": true,
        "topics": ["agent reliability"]
      }
    ]
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_dev_cli.py -v` and `uv run mypy src` and `uv run ruff check .`
Expected: PASS + mypy clean + ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/finch/dev/cli.py src/finch/cli.py tests/fixtures/nodes/recall.json tests/unit/test_dev_cli.py
git commit -m "feat(dev): add finch dev CLI (list-features / test / run-node)"
```

---

## Self-Review

**Spec coverage:**
- §3/§4 `FeatureSpec` registry + `DevContext` → Task 1.
- §5 `run-node` mechanism → Task 2.
- §6 CLI surface → Task 3.
- §7 MVP feature set (7 features) → Task 1 `_SPECS`.
- §9 tests → Tasks 1/2/3 test files.

**Placeholder scan:** none — each step has concrete code. (The `_settings` helper in Task 3's test has a note to simplify to `Settings()` — resolve during implementation: `run-node` does not touch paths, so `Settings()` suffices.)

**Type consistency:**
- `run_node(name, fixture_path, settings) -> NodeResult` matches its CLI caller (`result.model_dump(mode="json")`) and test (`result.status`, `result.output["items"]`).
- `FeatureSpec.build: Callable[[DevContext], Node]` matches `node = spec.build(DevContext(settings=settings))`.
- `REGISTRY.get(name)` returns `FeatureSpec | None`; both `runner.py` and `cli.py` handle `None`.

**Noted deviation:** spec §5 said `run_node` returns a dict; this plan returns `NodeResult` and lets the CLI format it (cleaner for tests). Behavior unchanged.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-06-finch-dev-harness.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks.

**2. Inline Execution** — execute in this session with checkpoints.

Which approach?
