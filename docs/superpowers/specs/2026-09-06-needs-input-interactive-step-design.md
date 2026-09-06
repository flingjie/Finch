# 把 `NEEDS_INPUT` 变成 Daily 交互步骤（UX 门面）设计文档

> 状态：已获用户批准（2026-09-06）。
> 目标：把「等待用户决策」从**运行异常**的观感，改成 **Daily 流程中的一个交互步骤**。
> 用户不需要理解 Graph 为什么暂停，只需要理解 Finch 现在需要他决定什么，以及按 Enter 会发生什么。

## 0. 结论与原则

- **不取消 `NEEDS_INPUT`**：Graph 内部状态仍为 `NEEDS_INPUT`，`finch run resume` 仍是底层恢复机制。
  本设计只在其上叠加一层友好的交互门面（沿用 [[2026-09-06-author-position-confirmation-design]] 的原则）。
- **面向用户与内部状态分层**：内部状态（`NEEDS_INPUT`/`author_position_confirmation`/`job_tp4` 等）默认不显示，
  仅在 `--verbose` / `--json` 中暴露。
- **一次选择，自动 resume**：确认 / 编辑 / 跳过 / 退出 之后由 CLI 自动调用 replay 继续运行，
  不再要求用户手动编排第二条命令（沿用 `finch run resolve` 的既有约定）。
- **TTY 与 非 TTY 分道**：有 TTY 时弹出交互选择器并自动恢复；无 TTY（Codex / CI / 定时任务）时
  输出紧凑、可操作的结果，退出码 0，不阻塞。
- **安全不变量不变**：任何全新或实质变化的作者立场，进入 Draft 节点前必须得到明确确认；
  `position_fingerprint` 复用门禁保持确定性。
- **确定性**：交互选择器与状态文案都是纯函数 / 确定性映射，不引入 LLM 判定。

## 1. 范围

**本次（一次性实现）**：

1. 状态命名分层：`state_label` 纯函数映射内部状态 → 面向用户文案；`--verbose` 揭示内部字段。
2. 渲染纯函数：确认卡、Daily 决策前摘要、非 TTY 紧凑结果、决策后产出摘要。
3. 交互选择器：`select_action`（Enter/e/s/d/q）+ `edit_position_inline`（逐字段编辑）。
4. CLI 接线：`finch run resolve` 无参进入交互选择器；`run daily` 在 TTY 复用选择器并自动恢复；
   非 TTY 输出紧凑结果。
5. 逐字段编辑 + 「多待确认任务」落地为 Daily 摘要的两个区块（原创立场确认 + 互动草稿审核），
   选择器本身仍只处理 `position_gate` 的单一阻塞。

**范围外（明确不做）**：

- 不改 `GraphRuntime` / `replay` 的底层恢复语义；不改 `position_gate` 的 single-primary 选择逻辑。
- 不把「多个待确认任务」做成真正的多阻塞队列——当前 gate 单次 run 只产出一个 `InputRequest`；
  互动草稿走独立的 `finch engagement` / `finch jobs review` 审核，不属于 `NEEDS_INPUT` 阻塞。
- 对 `NEEDS_INPUT` 之外的阻塞（`BLOCKED`/`FAILED`）不做交互门面（仍走 `finch diagnose`）。

## 2. 背景与现状（已核实）

- `finch run daily` 当前只打印原始 state（如 `NEEDS_INPUT`）与 brief，无摘要、无选择器、无自动恢复。
- `finch run resolve` 已有一个隐藏的交互分支（无 flag 时 `typer.prompt` 出 1–5 菜单），但 flag 与菜单并存、
  `--edit` 打开 `$EDITOR`（非逐字段）、文案直译内部概念（`请选择`/`job_tp4` 等）。
- `position_gate` 单次 run 只产出一个 `InputRequest`（单个 primary ContentJob），阻塞点唯一。
- 「互动草稿」来自 `run_dual_track` 的 engagement 轨道，候选持久化到 `InteractionRepository`，
  与 `NEEDS_INPUT` 正交。
- 已批准的立场确认门面设计见 [[2026-09-06-author-position-confirmation-design]]；本设计是在其上的
  第二层（UX 文案与交互），复用 `gate/resolve.py` 的 `resolve_input` / `parse_position_yaml` / `position_yaml`。

## 3. 架构

新增/修改文件：

```
src/finch/gate/
  render.py       # 新增 state_label / render_confirm_card / render_daily_summary /
                  #   render_compact_resolve / render_resolved_summary（纯函数，无 IO）
  interactive.py  # 新增 select_action / edit_position_inline（依赖 typer.prompt，可注入输入流）
  resolve.py      # 复用（resolve_input / parse_position_yaml / position_yaml）
src/finch/cli.py  # run daily / run resolve 接线；--verbose / --interactive / --edit-editor
```

### 3.1 状态命名分层（§1.1）

`gate/render.py` 新增纯函数：

```python
def state_label(state: str) -> str:
    # COMPLETED→已完成; NEEDS_INPUT→等待你的确认; SKIPPED→已跳过;
    # FAILED/BLOCKED→运行失败; 其余返回原值。
```

- 不存在 `GraphState.STOPPED`：`--stop` 是**动作**（把所有 job 标记 `do_not_write`），run 最终
  落在 `COMPLETED`（空输出）。因此「已保存并退出」是 `[q]` 的**结果文案**，不进入 `state_label`；
  「已跳过」是 `--skip` 的结果文案。
- `run daily` / `run resolve` 增加 `--verbose`：设置时，在友好文案后追加原始 state 与
  `run_id` / `job_id` / `author_position_confirmation` 等内部字段。`--json` 仍输出原始 `InputRequest`。

### 3.2 渲染纯函数

- `render_confirm_card(request, cards)`：主题 / 建议立场（主张·方案·取舍）/ 什么情况会改变这个决定 /
  证据 N 张。替换现有 `render_input_request` 的直译文案（保留 `render_position_diff` / `render_evidence`）。
- `render_daily_summary(posts_found, engagement_draft_count, pending_original_count, ...)`：
  「✓ 今日分析已完成」+ 互动内容（扫描 N 条 / M 条评论草稿等待审核）+ 原创内容（K 个主题待确认）。
- `render_compact_resolve(request, engagement_draft_count)`：非 TTY 紧凑结果（分析已完成 + 1 项待确认 +
  推荐命令 `uv run finch run resolve --confirm` + 其他命令 + 互动草稿计数）。
- `render_resolved_summary(...)`：决策后产出（✓ 已确认你的立场 / ✓ 原创草稿已生成 / 今日产出：互动草稿 M 条、
  原创草稿 K 条，等待审核）。

### 3.3 交互选择器（`gate/interactive.py`）

- `select_action(request, cards) -> InputAction | None`：
  渲染确认卡 + 菜单 `[Enter] 确认立场并继续生成草稿 / [e] 编辑立场 / [s] 跳过这个主题 /
  [d] 查看完整依据 / [q] 保存进度并退出`。Enter→CONFIRM，`e`→EDIT，`s`→SKIP，`d`→打印证据后重新循环，
  `q`→返回 `None`（保存进度并退出）。
- `edit_position_inline(proposed) -> ProposedPosition`：逐字段（主张/方案/取舍/改变决定的条件），
  空 Enter 保留原值。若编辑后不完整（`complete()` 为 False），返回错误并提示补全。

### 3.4 CLI 接线（§1.4）

- `finch run resolve`：
  - `--edit` → 逐字段内联编辑（`edit_position_inline`）。
  - 新增 `--edit-editor` → 沿用现有 `_edit_position`（`$EDITOR`）路径，供偏好完整编辑器的用户。
  - `--file` / `--confirm` / `--skip` / `--stop` / `--json` 语义不变。
  - 无任何 flag → `select_action` + `resolve_input` + `_resume_and_echo`。
- `finch run daily`：执行轨道后：
  - TTY → `render_daily_summary` + `select_action` 循环；确认/编辑后自动 `_resume_and_echo`，
    **循环**直到终态（已完成/运行失败）或 `[q]`。
  - 非 TTY → `render_compact_resolve`，退出码 0，不弹选择器。
  - 增加两个互斥布尔 `--interactive` / `--non-interactive`（缺省两者均未设 = 自动 TTY 检测），
    使交互路径可测试、可强制；以及 `--verbose`。二者同时给出时报错。

### 3.5 自动恢复循环

`run daily`（与无参 `run resolve`）把 resolve+resume 包进循环：每次决策后检查返回的 `RunRecord.state`，
若仍为 `NEEDS_INPUT`（例如 skip → 递补 topic 也阻塞），重新进入选择器；终态或 `[q]` 时退出。
engagement 候选与运行统计照旧持久化，不变。

## 4. 数据流

1. `run daily` → 双轨/单轨运行 → 读取 `RunRecord.state`。
2. 非 `NEEDS_INPUT` → 打印 `state_label` + 摘要，结束。
3. `NEEDS_INPUT` + 无 TTY → `render_compact_resolve`，退出 0。
4. `NEEDS_INPUT` + TTY → `render_daily_summary` → `select_action`：
   - `None`（q）→ 打印「已保存并退出」，结束（run 留在 `NEEDS_INPUT`）。
   - CONFIRM/EDIT/SKIP/STOP → `resolve_input(...)` → `_resume_and_echo(...)` → 检查新 state → 循环或结束。
   - 决策后非阻塞 → `render_resolved_summary` + 终态文案。

## 5. 错误处理

- `edit_position_inline` 产出不完整立场 → 报错并提示补全，不落库。
- `resolve_input` 抛 `ValueError`（job 不存在 / 立场不完整 / 缺 skip reason）→ 现有 `typer.Exit(1)` 干净错误路径不变。
- 无 TTY 时不调用 `typer.prompt`（会抛 `Abort`/`EOFError`）；紧凑路径纯输出。
- `--interactive` 强制交互时，输入流由 `typer.prompt` 读取，测试可用 `CliRunner(input=...)` 注入。

## 6. 测试

- 更新：
  - `tests/unit/test_cli_run.py::test_run_resolve_interactive_*`：输入 `"1"`→Enter、`"5"`→`d`；
    `test_run_resolve_edit_applies` 改 monkeypatch `edit_position_inline`。
  - `tests/unit/test_gate_render.py`：确认卡 / 摘要 / 紧凑结果新文案。
  - `tests/unit/test_cli_run.py::test_run_daily_enabled_echoes_engagement_summary`：新摘要文案。
- 新增：
  - `state_label` 映射表（含未知名回退原值）。
  - `edit_position_inline` 空 Enter 保留原值 / 产出不完整时报错。
  - `run resolve --edit-editor` 走 `$EDITOR` 路径。
  - `run daily` 非 TTY 紧凑输出（默认 `CliRunner` 非 TTY）。
  - `run daily --interactive` 自动恢复循环（含 skip→递补再阻塞→再选择）。
  - `--verbose` 揭示内部 state / `run_id` / `job_id`。

## 7. 与既有设计的关系

本设计是 [[2026-09-06-author-position-confirmation-design]] 之上的 UX 门面：不改 gate 语义，
只替换面向用户的文案、交互与状态展示。`resolve_input` / `parse_position_yaml` / `position_yaml` /
`position_fingerprint` 复用，不重复实现。
