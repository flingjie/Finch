# Finch Dev Harness 设计文档

> 状态：已获用户批准（2026-09-06）。
> 目标：给每个功能点提供统一、可复现的测试与运行入口（`FeatureSpec` 注册表 + `finch dev` CLI），
> 但**不需要**让每个功能点成为独立 Skill。Skill 按用户意图拆分，功能点通过 `finch dev`、fixture、
> pytest 暴露。

## 0. 结论与原则

- 每个功能点应可测试、可复现运行，但不必每个功能点都可由独立 Skill 触发。
- **Skill 只调用 Finch CLI，不复制业务逻辑**（沿用现有 `skills/finch/SKILL.md` 的原则）。
- 新增 `finch dev` 作为开发 Harness 的统一入口；`FeatureSpec` 注册表是单点真源，
  CLI、`run-node`、测试发现都复用它。

## 1. 范围

**本次（MVP）**：
- `FeatureSpec` 注册表 + `src/finch/dev/` 包（`registry.py` / `runner.py` / `cli.py`）。
- `finch dev list-features`、`finch dev test <feature>`、`finch dev run-node <node> --input <fixture.json>`。
- 仅支持**无状态/确定性**节点（见 §4）。

**范围外（后续 spec，明确不做）**：
- 有状态节点 + live 模式 + `--no-persist`（`extract_events` / `define_jobs` / `position_gate` 需要
  `store` + 真实 LLM runner，DI 装配问题留到后续）。
- `benchmark` / `compare` / snapshot（等 fixture 格式稳定后再加）。
- `$finch-dev` Skill（等 `finch dev` CLI 稳定后再映射自然语言入口）。
- 不改动任何现有节点/工厂签名。

## 2. 背景与现状（已核实）

- 10 个 node factory 签名异构、DI 重：`make_extract_node(extractor, groups_by_repo, …)`、
  `make_define_jobs_node(plan_runner, expand_runner, …)`，而 `make_recall_node(gates)` 是纯函数、
  `make_collect_node(collect_fn)` 收闭包。
- 无任何 node fixture / snapshot；现有测试（`tests/graph/test_*.py`）手写 fake 直接构造节点。
- 节点分两类：**无状态**（`recall`、`brief`、`position_gate(jobs_repo=None)` 等确定性节点）与
  **有状态/依赖 LLM**（`extract`、`define_jobs`、`match`、`draft`、`critique`）。
- `GraphContext` envelope：节点 `run(ctx)` 接收 `{key: {"items": [...]}}`，`parse_items` 读
  `ctx[key]["items"]`。这是 fixture 的自然格式。

## 3. 架构决策

采用「**`src/finch/dev/` 包 + `FeatureSpec` 注册表**」方案：

- 一次注册，CLI、`run-node`、`test`、未来的 `benchmark` 都复用同一份元数据。
- `kind` 区分两类功能点：`graph_node`（可 `run-node`）与 `function`（仅 `test` / 未来 benchmark）。
- **DI 的 MVP 答案**：只运行无状态节点，`build` 是「`DevContext` → 已装配 Node」的闭包；
  `DevContext` 现在只带 `settings`，后续加 `store` / runners / `gh` / `opencli` 时，`build` 签名不变。
  这样避免了「裸工厂函数」无法零参实例化的问题（`FeatureSpec.factory` 的坑）。

### 3.1 文件布局

```
src/finch/dev/
  __init__.py
  registry.py   # FeatureSpec + DevContext + REGISTRY（纯数据，无 IO）
  runner.py     # run_node / run_test / list_features（编排逻辑）
  cli.py        # typer dev_app（list-features / test / run-node）
```

`cli.py` 挂载：`app.add_typer(dev_app, name="dev")`。

## 4. 数据模型

```python
# registry.py
from dataclasses import dataclass, field
from typing import Literal, Callable
from finch.graph.nodes import Node
from finch.settings import Settings

@dataclass(frozen=True)
class DevContext:
    settings: Settings  # MVP 只有 settings；后续加 store/runners

@dataclass(frozen=True)
class FeatureSpec:
    name: str
    kind: Literal["graph_node", "function"]
    description: str
    test_targets: tuple[str, ...] = ()
    # graph_node 专用（run-node 用它装配节点）：
    build: Callable[[DevContext], Node] | None = None
    required_inputs: tuple[str, ...] = ()  # fixture 必须提供的 GraphContext 键

REGISTRY: dict[str, FeatureSpec] = {s.name: s for s in _SPECS}
# _SPECS 是模块级 FeatureSpec 列表，按 §7 的表逐条构造（name 唯一，重复即启动报错）。
```

- `build` 对无状态节点是一行闭包，例如 `lambda ctx: make_recall_node(ctx.settings.quality_gates)`。
- `required_inputs` 让 `run-node` 校验 fixture 完整性，缺失时给出明确报错。
- `kind="function"` 的 `build` / `required_inputs` 留空（不参与 `run-node`）。

## 5. `run-node` 机制

```
finch dev run-node recall --input tests/fixtures/nodes/recall.json
```

1. 查 `REGISTRY[name]`；`kind != "graph_node"` → 报错「use `test`, not `run-node`」。
2. 读 fixture（`GraphContext` envelope：`{"candidates": {"items": [...]}, "evidence_cards": {"items": [...]}}`）。
3. 校验 fixture 含全部 `required_inputs` 键。
4. `node = spec.build(DevContext(settings))`；补 `ctx["run_id"]` 占位（`setdefault("run_id", "dev-run-node")`，
   供 `brief` 等读 `ctx["run_id"]` 的节点用，与 `GraphRuntime` 一致）；`result = node.run(ctx)`。
5. 打印 `result.status` + `result.output`（JSON）。**无持久化、无副作用**（无状态节点保证）。

## 6. CLI 表面

```text
finch dev list-features                  # name / kind / description / test_targets
finch dev test <feature>                 # 子进程: uv run pytest <test_targets> -q
finch dev run-node <node> --input <json> # 见 §5
```

- `test` 用 `subprocess.run(["uv", "run", "pytest", *spec.test_targets, "-q"])`，数组传参 + 超时，
  透传 stdout/stderr，返回退出码。
- 错误：未知 feature → 列出全部合法 name；`run-node` 的 `function` → 引导去 `test`。

## 7. MVP 功能点集（证明机制可行）

| name | kind | test_targets | required_inputs |
|---|---|---|---|
| `recall` | graph_node | `tests/graph/test_match_nodes.py` | `candidates`, `evidence_cards` |
| `brief` | graph_node | `tests/graph/test_content_nodes.py` | `drafts`, `content_jobs`, `evidence_cards`, `ready_jobs`, `candidates`, `match_results` |
| `select_groups` | function | `tests/unit/test_budget.py` | — |
| `rank_pending` | function | `tests/unit/test_budget.py` | — |
| `select_primary_job` | function | `tests/unit/test_jobs.py` | — |
| `select_planning_evidence` | function | `tests/unit/test_jobs.py` | — |
| `scan_cards` | function | `tests/unit/test_safety.py` | — |

`brief` 的 fixture 较繁（6 键），但正好证明多输入节点也能跑；`recall` 是单键示例。

## 8. 不变量与错误处理

- **子进程纪律**：`test` 用数组传参 + 超时，不 shell 拼接。
- **无副作用**：`run-node` 只跑无状态节点，不写 ledger / cards / jobs，不触发 LLM。
- **确定性**：`run-node` 输出只取决于 fixture 输入（无状态节点保证）。
- **只读**：`finch dev` 不改变任何生产数据；`list-features` / `test` / `run-node` 均无持久化。

## 9. 测试

- `tests/unit/test_dev_registry.py`：每个 `FeatureSpec` 有唯一 name、合法 kind；graph_node 的
  `build(DevContext(Settings()))` 能返回 `Node` 且 `reads` 与 `required_inputs` 一致；function 的
  `build is None`。
- `tests/unit/test_dev_runner.py`：`run_node` 对 recall fixture 返回 `succeeded` 且 output 含
  `ranked_candidates` 的 items；缺 required_inputs 报错；`function` 走 `run-node` 报错。
- `tests/unit/test_dev_cli.py`：`finch dev list-features` 退出码 0 且列出 recall；`finch dev test
  recall` 跑通（退出码 0）。

## 10. 范围外（明确不做）

- 有状态节点 / live 模式 / `--no-persist`；`FeatureSpec.supports_live` 等字段。
- `benchmark` / `compare` / snapshot / eval fixture 格式。
- `$finch-dev` Skill。
- 节点工厂签名统一化重构（不改现有 `make_*` 签名）。
