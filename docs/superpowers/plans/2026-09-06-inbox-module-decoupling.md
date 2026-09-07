# 收件箱深度重构 Phase 3（模块解耦收尾）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收尾模块解耦：拆 `graph/content_nodes.py`（499 行）为 `select_nodes.py` + `write_nodes.py`，把 `default_checker_suite`/`_run_checks` 移到 `content/critic.py`；`inbox` 增加 `list_items` 全量投影并接 `render_daily_summary`；`cli.py`（767 行）进一步瘦身；收敛 `ContentJobStatus.NEEDS_INPUT` 与重复的 `_STRONG_CONFIDENCE`。

**Architecture:** 图节点按职责分文件（select / write 各自独立），checker 套件归 content 层。产品层 `inbox` 是唯一跨轨投影处；`list_items` 与 `next_item` 共用同一投影组装，`daily` 的人类输出改为「今天 N 条待决定」。

**Tech Stack:** Python 3.12、Pydantic 2、SQLModel、typer、pytest。Phase 1/2 已完成（7 节点 Graph、gate 删除、review 删除、立场机制删除）。

## Global Constraints

- Python 3.12+；Pydantic 2；SQLModel `payload_json` + `session.merge`。
- Ruff `E,F,I,B,UP`，行宽 100，py312；mypy `src`；测试 `uv run pytest tests/... -v`。
- **不变量**：证据链不废除；不自动发布；分数由代码算；双轨每轮都跑、故障隔离；GraphRuntime 顺序/幂等/重放不改。
- 中文/英文 docstring 均可；与相邻模块一致。
- **验收**：`uv run pytest -q` 全绿 + `tests/graph/test_replay.py` 全绿 + `uv run ruff check src/finch` + `uv run mypy src/finch` 干净。

---

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/finch/graph/select_nodes.py` | `make_select_node` + `_topic_sort_key` | 新建（从 content_nodes.py 迁出） |
| `src/finch/graph/write_nodes.py` | `make_write_node` + 写稿 helper（`WriteReplyFn`/`WriteOriginalFn`/`RewriteFn`/`_DraftPlan`） | 新建（从 content_nodes.py 迁出） |
| `src/finch/graph/content_nodes.py` | 删除（残余 `default_checker_suite`/`_run_checks` 迁到 content/critic.py） | 删除 |
| `src/finch/content/critic.py` | `critique` + `default_checker_suite` + `_run_checks` | 追加 checker 套件 |
| `src/finch/inbox/service.py` | 追加 `list_items`（全量投影组装，`next_item` 复用） | 修改 |
| `src/finch/inbox/render.py` | `render_daily_summary(items)`（「今天 N 条待决定」） | 修改 |
| `src/finch/cli.py` | `daily` 非 json 输出接 `list_items` + `render_daily_summary`；删残留死代码 | 修改 |
| `src/finch/content/jobs.py` | 收敛 `ContentJobStatus.NEEDS_INPUT`（见 Task 4） | 修改 |

---

### Task 1: 拆 content_nodes.py + 迁 checker 套件

**Files:**
- Create: `src/finch/graph/select_nodes.py`、`src/finch/graph/write_nodes.py`
- Modify: `src/finch/content/critic.py`（追加 `default_checker_suite`/`_run_checks`）
- Modify: `src/finch/graph/daily.py`、`src/finch/graph/content_nodes.py`（更新 import / 删除）
- Delete: `src/finch/graph/content_nodes.py`
- Test: `tests/graph/test_content_nodes.py`、`tests/graph/test_daily.py`

**Interfaces:**
- `select_nodes.make_select_node(...)` 与 `write_nodes.make_write_node(...)` 签名不变。
- `content/critic.py` 追加 `default_checker_suite(runner, voice_profile=None) -> list[Checker]`、`_run_checks(suite, check_ctx) -> list[CheckResult]`（从 content_nodes 迁出，签名不变）。
- 更新所有 `from .content_nodes import ...` / `from finch.graph.content_nodes import ...` 的引用（grep 全仓库：daily.py、idea/service.py、tests 等）。

- [ ] **Step 1: 迁 `default_checker_suite`/`_run_checks` 到 content/critic.py**（复制代码，更新 `idea/service.py` 等 import 指向）。
- [ ] **Step 2: 新建 select_nodes.py / write_nodes.py**，把 `make_select_node`/`_topic_sort_key` 与 `make_write_node`/写稿 helper 分别迁入；`content_nodes.py` 删除。
- [ ] **Step 3: 更新 import**（`grep -rn "content_nodes" src tests` 逐一改到新路径）。
- [ ] **Step 4: 跑测试 + 提交**：`uv run pytest -q`；`git commit -m "refactor(graph): split content_nodes into select/write + move checker suite"`。

---

### Task 2: inbox `list_items` + `render_daily_summary` 接入 daily

**Files:**
- Modify: `src/finch/inbox/service.py`（新增 `list_items(...)`；`next_item` 改为调用 `list_items` 取第一条）
- Modify: `src/finch/inbox/render.py`（`render_daily_summary(items)`）
- Modify: `src/finch/cli.py`（`daily` 非 json 输出：`list_items` → `render_daily_summary`）
- Test: `tests/unit/test_inbox_service.py`、`tests/unit/test_cli_run.py`

**Interfaces:**
- `list_items(*, jobs, drafts, decisions, interactions, cards) -> list[InboxItem]`（把 `next_item` 里的投影组装抽出来，返回全部未决策项，按 `select_next` 的排序排好）。
- `next_item(...) -> dict`（改为 `select_next(list_items(...))`，行为不变）。
- `render_daily_summary(items: list[InboxItem]) -> str`（「今天 N 条待决定」+ 逐条 `[类型] 标题`；空则「今天没有待决定的内容。」）。

- [ ] **Step 1: 抽 `list_items`**，`next_item` 复用；补 `test_list_items_*` 测试。
- [ ] **Step 2: `render_daily_summary` + 单测**。
- [ ] **Step 3: `daily` 接入**（非 json 分支：`_echo_dual_track_result` 之后或替代 state 打印，输出 `render_daily_summary(list_items(...))`；保留 `--json` 摘要）。
- [ ] **Step 4: 跑测试 + 提交**：`git commit -m "feat(inbox): list_items projection + daily summary"`。

---

### Task 3: cli.py 瘦身 + 死代码收敛

**Files:**
- Modify: `src/finch/cli.py`
- Modify: `src/finch/content/jobs.py`（`ContentJobStatus.NEEDS_INPUT` 收敛，见 Task 4）
- Modify: `src/finch/graph/*.py`（`_STRONG_CONFIDENCE` 去重）

**Interfaces:**
- `cli.py` 只保留命令定义 + `load_settings`/`Store`/服务调用；删除 Phase 2 残留的死 helper（grep 未使用）。
- `ContentJobStatus.NEEDS_INPUT`：若 `expand_content_job`/prompt 仍产出但图不再消费，则把 `select` 节点在展开后把 NEEDS_INPUT 的 job 视为 `PROPOSED`（或直接保留 status 但不影响 ready_jobs），并更新 prompt 文案；若复杂则保留并记录（不强行改）。

- [ ] **Step 1: 删 cli.py 死代码**（`grep` 确认未使用的 helper/import，ruff F401 驱动）。
- [ ] **Step 2: `_STRONG_CONFIDENCE` 去重**（`content/jobs.py` 与 `select_nodes.py`/`write_nodes.py` 各有一份时，统一到 `content/jobs.py` 导入）。
- [ ] **Step 3: `ContentJobStatus.NEEDS_INPUT` 收敛**（尽量让 prompt 不再产出，或 select 节点归一化为 READY/PROPOSED）。
- [ ] **Step 4: 跑测试 + 提交**：`git commit -m "refactor(inbox): phase 3 cli slim + dead-code convergence"`。

---

### Task 4: 全量回归 + 收尾

- [ ] **Step 1:** `uv run pytest -q`、`uv run ruff check src/finch`、`uv run mypy src/finch` 全绿（含 `tests/graph/test_replay.py`）。
- [ ] **Step 2: 提交**：`git commit -m "chore(inbox): phase 3 regression"`（若有残余改动）。

---

## Self-Review 记录

1. **Spec 覆盖**：spec §7 模块布局（拆 content_nodes、inbox 是唯一跨轨处）→ Task 1/2；§8 理想人类输出 → Task 2；死代码 → Task 3/4。
2. **占位符扫描**：无 TBD/TODO。
3. **类型一致性**：`list_items`/`next_item`/`render_daily_summary` 签名跨任务一致；`default_checker_suite`/`_run_checks` 迁址后签名不变。

**说明**：`GraphState.NEEDS_INPUT`/`AggregateOutcome.NEEDS_INPUT` 作为通用 runtime/critic 机制保留（daily 无节点产生）；`refactor/skill-architecture` 分支可删（已 ff 合并回 main）。
