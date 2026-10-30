# Finch 文档统一 + Source/发现地基 设计

日期：2026-10-30
状态：待实施
目标：把 Finch 的产品方向从「产品单一核心 + AGENTS.md 旧定位残留」统一为「产品与问题并列打通」，并修复 Source 同步掩盖部分失败、产品发现流程不可观测两个可靠性缺陷，为下一块（Problem↔Product↔Feedback 关联）打地基。

> 本文已核对 `main` HEAD（85ec1a1）的 `src/finch/sources/orchestrator.py`、`src/finch/sources/connectors/github.py`、`src/finch/sources/projector.py`、`src/finch/products/discovery.py`、`src/finch/products/cards.py`、`src/finch/cli.py` 与四份规范文档（README / CLAUDE.md / docs/product-contract.md / AGENTS.md）。范围限定三块：文档统一、Source 聚合状态、发现执行报告。不建关联模型，不动 Problem 指纹/证据/合并，不动产品评分与 founder 身份。

## 1. 背景与问题

**方向分歧（已核实）**：README、CLAUDE.md、`docs/product-contract.md` 三处一致写「AI 产品发现 + 创业者连接，产品是主要研究对象」；`AGENTS.md` 仍是旧版，写「跨领域学习、连接与表达伙伴」且明确「不建独立问题库」「问题/workaround 记 ConversationThread」——而代码已有独立的 `src/finch/problem_discovery/` 与 problem inbox 功能。编码代理读不同规范会产出互相矛盾的实现。

已定的方向：**问题与产品并列打通**——不降级任何一方，二者通过证据或人工确认关联；关联机制本身留到下一块。本块先把方向落成文字。

**Source 掩盖部分失败（已核实到行）**：

1. `classify_result` 只有 `success/empty/failed` 三态，最终状态由 `last_kind`（最后一个请求）决定；`orchestrator.py` 的请求循环每次覆盖 `last_kind`，早先的 TIMEOUT 被后续 SUCCESS 掩盖（例：Query1 TIMEOUT → Query2 SUCCESS → 整体记 success）。
2. TIMEOUT 不在 break 集合里（`:316` 只有 AUTH_REQUIRED/BRIDGE_DOWN/CONFIG_ERROR 会中断），超时结果落到 `outcome="empty"`，报告显示「空」而非「失败」（`kind` 字段保留了 TIMEOUT，但渲染只看 `outcome`）。
3. GitHub `fetch_creator` 只要有任一行数据就返回 `kind=SUCCESS`（`github.py:224`），子请求失败只进 `errors→stderr_summary`，orchestrator 的 GitHub 路径从不读它；`_project` 捕获异常直接返回空 `ProjectResult`（`orchestrator.py:400`），投影失败完全无信号。

**发现流程不可观测（已核实到行）**：`run_product_discovery` 的 `result.detail` 恒为空（CLI `:1001` 恒假）；`cards.fill()` 返回值被丢弃（`discovery.py:170`）；空结果只显示「今日无产品候选（空轮保持空）」。而 `sources sync` 命令已有 per-query 渲染（`cli.py:1758`），`products daily` 没接上。

## 2. 已确认的决策

- **方向 = 问题与产品并列打通**；本块只落文档方向 + 地基，不写关联模型（字段/证据规则留到下一块）。
- **深度 = 结构化地基**（非最小补丁、非全量重构 Source 五层）。
- **北极星不变**（每周新增/加深的创业者关系）；「问题从发现推进到更深理解/验证」列为关系之外的观察项之一，不合成总分。
- **Source 聚合是确定性纯函数**，沿用「确定性 Python 领域服务」不变式；单源/单 query 失败不阻塞其他源；raw 落盘先于投影，投影失败不丢采集结果。
- **`not_executed` 继续由 CLI 对缺失（禁用）源合成**，不进 `aggregate_state`。

## 3. 改动

### 3.1 文档统一（方向落文字）

- `docs/product-contract.md`（最权威）：一句话定位补「问题是与产品并列的一等研究对象，二者通过证据或人工确认关联」；核心闭环补并列视角（真实问题可从产品/反馈/社区发现，一个问题对应多个候选方案，一个产品可解决多个问题）；对象所有权表新增 `Problem` 行（证据 / 受影响用户 / 现有 workaround / 候选方案，候选方案引用 Product）；北极星不变，观察项补「问题推进」。
- `README.md`：定位句 + 闭环图补「问题 ↔ 产品」并列视角；命令段不动。
- `CLAUDE.md`：Project 段补「问题是并列一等对象」；architecture 段显式列出 `problem_discovery/`、`relationships/`，厘清 active/frozen 边界（当前 frozen 注释未涵盖这两块）。
- `AGENTS.md`（全量重写）：删旧定位（跨领域学习/连接/表达伙伴、不建独立问题库、问题记 ConversationThread）；改为与契约一致的定义 + 三层（产品发现 / 问题发现 / 用户连接，连接是前两者的后续行动而非独立采集目标）；命令段替换为当前 active CLI，旧使命命令标 frozen/hidden；保留仍有效的工程约定（Evidence First、只读权限、子进程数组 + 超时 + Pydantic）。
- **边界**：文档只确立方向与对象身份，不写关联字段/证据规则。

### 3.2 Source 聚合状态 + 三个 bug

- 新增纯函数 `aggregate_state(result: SyncResult) -> "success" | "empty" | "partial_success" | "failed"`（放 `sources/orchestrator.py`，替换 `classify_result` 的渲染用途）：
  - `has_failure = 任一 per_query.outcome == "failed"`；`has_partial = 任一 per_query.partial`。
  - `has_failure or has_partial` 时：`raw_count > 0` → `partial_success`，否则 `failed`。
  - 否则：`raw_count > 0` → `success`，否则 `empty`。
  - 无 `per_query` 时兜底：`kind ∈ _ERROR_KINDS` → `failed`，否则 `raw_count > 0` → `success` / `empty`。
- `QueryOutcome` 新增 `partial: bool = False`。
- **bug1（TIMEOUT 记成 empty）**：请求循环里，`result.kind ∈ _ERROR_KINDS` 但非 break 集合（TIMEOUT/ERROR/CANCELLED/UNAVAILABLE/BLOCKED）→ 记 `QueryOutcome(outcome="failed", kind=..., detail=stderr)` 并 `continue`（不写 raw、不 normalize）；break 集合（AUTH_REQUIRED/BRIDGE_DOWN/CONFIG_ERROR）行为不变（记 failed + break）。
- **bug2（GitHub 部分失败隐身）**：GitHub 路径把 `result.stderr_summary` 写入 `QueryOutcome.detail`；当 `rows` 非空且 `stderr_summary` 非空时置 `partial=True`。
- **bug3（投影失败无信号）**：`ProjectResult` 新增 `failed: bool = False`、`error: str = ""`；`_project` 捕获异常时返回 `ProjectResult(failed=True, error=str(exc)[:300])`；`sync_source` 把 `proj.failed/error` 写进 `SyncResult`（新增 `projection_failed: bool`、`projection_error: str`）与 run payload。投影只对 peers 路径有意义（`products daily` 走 `project_peers=False`，不触发）。
- `sync_result_to_dict` 补 `partial`（per-query）、`projection_failed`、`projection_error`。
- **`sources sync` CLI**：state 列改用 `aggregate_state`（含 `partial_success`）；per-query 行对 `partial=True` 显示 `(partial)`；逐源行对 `r.projection_failed` 追加 `projection failed: {projection_error}` 提示。
- **不变量**：单源失败不阻塞其他源（`sync_all` 的 try/except 保留）；raw 落盘先于投影（已确认）。

### 3.3 DiscoveryRunReport + CLI 渲染

- 新增 dataclass `DiscoveryRunReport`（`products/discovery.py`）：
  - `per_source_state: dict[str, str]`（source → `aggregate_state`）
  - `raw_count / normalized_count / filtered_count / product_count: int`
  - `card_fill_success / card_fill_failed: int`
  - `elapsed_seconds: float`

  注：不含 `query_outcomes`——per-query 详情已随 `result.sync_results` 返回；`products daily --json` 只带聚合状态与计数，逐 query 详情走 `sources sync --json`。
- `ProductDiscoveryResult` 新增 `report: DiscoveryRunReport | None`。
- `ProductCardService.fill()` 改为返回 `(saved: int, failed: int)`（批级 except 时 `failed += len(batch)`）；`run_product_discovery` 用它填 `card_fill_success/failed`。
- `run_product_discovery`：起点记 `time.perf_counter()`，结束算 elapsed；组装 report；`result.detail` 置为失败汇总字符串（哪个源 failed/empty/partial）。
- **持久化**：`ProductSnapshot` 增 `source_states: dict[str, str]`、`card_fill_success: int`、`card_fill_failed: int`、`elapsed_seconds: float`（复用现有落盘，不新建 store）。
- **CLI `products daily`**：
  - 非空：先打印一行紧凑报告（源状态 / raw→norm→filtered→product 计数 / 卡片成功失败 / 耗时）。
  - 空：打印原因（逐源 state + 失败 detail），不再只显示「空轮保持空」。
  - `--json`：report 进 JSON。

## 4. 非目标

- 不建 Problem↔Product↔Feedback 关联模型（下一块）。
- 不动 Problem 指纹（内容哈希/URL/prompt 版本）、输入规模控制、证据门禁、问题合并（下一块）。
- 不动产品评分、founder 身份归属、`product_key_for` 身份解析（后续）。
- 不做产品卡片缓存、配置指纹去重、capabilities 快照复用（后续）。
- 不把 Source 重构为 SourceRun/QueryRun/Raw/Normalize/Projection/Aggregated 五层模型（本块只加聚合函数 + 三个信号）。
- 不新增数据库，不改存储后端。

## 5. 测试 / 验证

- `aggregate_state` 全态单测：success / empty / partial_success（部分 failed 但 raw>0）/ failed（全 failed）/ 无 per_query 兜底。
- TIMEOUT 记 `outcome="failed"` 而非 empty；break 集合仍 break。
- GitHub partial：`fetch_creator` rows + stderr → `QueryOutcome.partial=True` + detail 含 stderr；`sync_result_to_dict` 序列化 partial。
- `_project` 抛异常 → `ProjectResult(failed=True)` → `SyncResult.projection_failed=True`。
- `run_product_discovery` 用假 orchestrator/runner：卡片失败计数、report 计数、elapsed 非负、detail 含失败源。
- 文档一致性：人工走查四份文档，确认无「不建独立问题库」残留、对象表含 Problem、北极星不变。
- 全量 `uv run pytest`、`uv run ruff check .`、`uv run mypy src` 通过。
