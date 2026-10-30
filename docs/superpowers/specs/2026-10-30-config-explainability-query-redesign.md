# Finch 让配置可解释 + 完整词重构 设计

日期：2026-10-30
状态：待实施
目标：让 `finch sources sync` 如实报告每个源实际用了什么模式、跑了哪些查询、结果与失败状态，并把查询词拆短、按任务/困难组织、收敛到两个主题。

> 本文已核对 `main` HEAD 的 `src/finch/sources/` 与 `finch.example.yaml` / `finch.yaml`。命令、字段与配置为拟议接口，实施时以最新代码为准。

## 1. 背景与问题

三套发现任务（仓库 / 产品 / 问题）各维护一套搜索词，容易漂移；`sources` 与 `products.sources` 是两套独立 `SourcesSettings`。更关键的是「写了但没运行」：`SourceDiscoverySettings.mode` 是单选（`query | hot | url | timeline | disabled`），Twitter 在 `finch.example.yaml` 里 `mode: timeline`，其配置的 `queries` 被有意忽略（不回退搜索），但当前输出看不出这一点——`finch sources sync` 只打印 `source/status/raw/norm/new/projected/detail`，不显示实际 mode、实际查询、时间范围，也没有「未执行 / 返回空 / 失败」的清晰区分。

本切片解决两件事：

1. **报告机制**（代码）：让采集结果可解释。
2. **查询词重构**（纯 yaml 内容，不动 schema）：拆短长查询、按任务/困难组织、收敛到两主题。

## 2. 已确认的决策

- 范围 = 「报告机制 + 完整词重构」这一个切片；后续切片（产品→反馈、主动问题搜索、持续跟踪复盘）不在本切片。
- **不动采集模式**：`mode` 保持单选，一个源仍只跑一种模式；本切片只报告「实际用了哪种、哪些配置的查询因此没跑」，不支持同时跑 timeline + query。
- **不加结构化标签**：「完整词重构」只改查询词内容；「产品/问题/实践」路由标签与意图轴不落字段，留到「统一主题目录」的后续切片。意图轴只作写词指南。
- **报告粒度 = 方案 B**：按源四态 + 每查询一行，落结构化 run 记录，加 `--json`；不做 `finch sources runs` 历史读取命令（属于 step 4 复盘）。
- **时间范围**：如实报告「运行时间戳 + 每查询 latest N（`fetch_limit`），当前无回溯窗口」，不新增过滤。

## 3. 报告机制（代码）

全部落在 `src/finch/sources/`，不新增模块、不新增数据库。

### 3.1 数据模型与数据流

- `DiscoveryContext`（`src/finch/sources/connectors/__init__.py`）新增 `configured_queries`、`configured_urls`：存配置里的**完整**查询/URL（无论 mode），专用于「配置了但没跑」的报告。由 `build_context_by_source`（`src/finch/sources/query_plan.py`）填充。
  - `mode: timeline` → `queries=[]`，`configured_queries = plan.queries`（被跳过的）。
  - `mode: query` → `queries = configured_queries`（实际跑的）。
  - `mode: url` → `urls = configured_urls`。
  - `mode: hot`（仅 V2EX）→ 无查询，`configured_queries=[]`。
- 新增 `QueryOutcome`（`src/finch/sources/orchestrator.py`，dataclass）：`query`（查询词；timeline 记 `"timeline"`）、`outcome`（`ran | empty | failed | skipped`）、`raw_count`、`normalized_count`、`kind`、`detail`。
- `SyncResult`（`orchestrator.py`）新增：`mode`、`ran_queries`、`skipped_queries`、`per_query: list[QueryOutcome]`、`started_at`。
- `sync_source` 逐 request 记一条 `QueryOutcome`：有结果 → `ran`，成功但 0 行 → `empty`，错误 kind → `failed`；`mode: timeline` 且 `configured_queries` 非空时，把这些记为 `skipped`。
- `_run_payload`（`orchestrator.py`）落：`mode / configured_queries / ran_queries / skipped_queries / per_query / started_at`。

### 3.2 四态分类（确定性）

`classify(result)` 按源得出：

| 态 | 判定 |
| --- | --- |
| `not_executed` | 禁用 / 未进入 context（`enabled=false` 或 `mode=disabled`） |
| `failed` | 错误 kind（`ResultKind` 错误族）或非 READY 且带错误 detail（含 timeline-unavailable） |
| `empty` | 成功但 `raw_count == 0` |
| `success` | 成功且有结果 |

沿用现有 `ResultKind` 错误族与 `error_type`，不另造错误枚举。

### 3.3 输出形状

- `finch sources sync` 加 `--json`。
- 人类输出（按源四态 + 逐查询，含运行时间戳与每查询 latest N）：

```text
twitter: success [timeline] started=2026-10-30T10:00Z raw=200 norm=180 new=5 projected=4
  - timeline: ran (200 raw, latest 200)
  - skipped 3 configured queries (mode=timeline): "agent reliability…", …
reddit: empty [query] raw=0
  - "agent reliability production failure": empty (0 raw, latest 100)
github: not_executed [disabled]
```

- `--json` 直接 dump 结构化 `SyncResult`（含 `mode / ran_queries / skipped_queries / per_query / kind / error_type / started_at`）。
- 本切片只改 `sources sync` 的可见输出；`connect daily` / `products daily` 内部也走 `sync_all`，其一行的采集摘要列为后续，不在本切片展开。

## 4. 查询词重构（纯 yaml，不动 schema）

两主题写进 `exploration_topics`；`sources.*.queries` 的固定基线用同款短查询替换长抽象词。意图轴只作写词指南，不落字段。每平台保留各自语法，不追求跨平台复用同一串。

### 4.1 主题

- **Agent 实际落地**（可靠性 / 评测 / 人工介入 / 成本 / 调试）：英文 `agent stuck`、`tool calling failed`、`agent evals`、`human in the loop`、`agent reliability`；中文 `Agent 卡住`、`工具调用失败`、`人工接管`。
- **AI 个人赋能**（学习 / 技能练习 / 知识检索与应用）：英文 `AI notes remembering`、`learn by doing`、`knowledge retrieval`；中文 `笔记很多但记不住`、`学了不会用`。

### 4.2 查询种子（按搜索意图，作写词参考，非跨平台完整语法）

| 意图 | 英文种子 | 中文种子 |
| --- | --- | --- |
| 遇到困难 | `agent stuck`、`tool calling failed` | Agent 卡住、工具调用失败 |
| 寻找替代 | `alternative to [产品名]` | 替代某产品、有没有更好的 |
| 被迫绕路 | `[产品名] workaround`、`manual review` | 手动处理、反复核对 |
| 放弃使用 | `stopped using [产品名]` | 不用了、放弃使用 |
| 实际价值 | `[产品名] saved time` | 实际效果、用了一个月 |
| 学习迁移 | `AI notes remembering` | 笔记很多但记不住、学了不会用 |

### 4.3 yaml 改动

- `finch.example.yaml` **补上 `exploration_topics`**（现缺），收敛为上述两主题。
- 运行时 `finch.yaml` 现有 5 主题（`reliability_and_recovery` 等）收敛为两主题。
- 同步替换 `finch.example.yaml` 与 `finch.yaml` 里 `sources.*.queries` 的长查询（如 Reddit `agent reliability production failure`）为短查询；`products.sources.*.queries` 里的宽发布词（`"I built"`、`"新产品 发布"`）加任务约束（如产品涉及学习/知识管理/Agent 落地）。

## 5. 明确不做

- 模式多选（同时跑 timeline + query）。
- 结构化标签（产品/问题/实践）与意图轴落字段。
- lookback 时间过滤。
- `finch sources runs` 历史读取命令（step 4 复盘）。
- `connect daily` / `products daily` 的采集摘要一行。

## 6. 实施步骤

### P0：报告机制（代码）

改 `DiscoveryContext`、新增 `QueryOutcome`、扩 `SyncResult`、改 `sync_source` 逐 request 记账、扩 `_run_payload`、改 `sources_sync` 输出 + `--json`。补单测：四态分类、timeline 跳过查询的报告、`--json` 结构、run 记录 roundtrip（镜像 `tests/unit/test_source_*.py`）。

### P1：查询词重构（yaml）

重写 `finch.example.yaml` 与 `finch.yaml` 的 `exploration_topics` 与各 `queries`，收敛到两主题、拆短长查询、给 `products.sources` 宽查询加任务约束。手工用 `uv run finch sources sync --all --json` 抽样核对 query 与 mode 如实。

## 7. 验收标准

| 验收场景 | 预期结果 |
| --- | --- |
| Twitter `mode: timeline` 且配置了 queries | 报告 `timeline` 跑了，配置的 queries 记为 `skipped (mode=timeline)`，不误报为运行 |
| Reddit `mode: query` 某查询返回 0 行 | 该查询记 `empty (0 raw)`，与 `failed` 区分 |
| 某源禁用 | 记 `not_executed [disabled]`，不报错 |
| 某查询失败（auth/bridge/error） | 记 `failed` + `kind`，`error_type` 非空 |
| `sources sync --json` | 输出含 `mode / ran_queries / skipped_queries / per_query / started_at` |
| run 记录 | 落 mode/查询/每查询结果/时间戳，可复算报告 |
| 示例 yaml 的 `exploration_topics` | 收敛为「Agent 实际落地」「AI 个人赋能」两主题，查询为短查询 |

评估只关注「报告是否如实反映实际运行的 mode / query / 结果 / 失败」与「查询词是否拆短且主题收敛」，不评估召回率或相关性（属后续切片）。

---

## Appendix A — 现状代码映射（2026-10-30）

- `DiscoveryContext`：`src/finch/sources/connectors/__init__.py`（`queries / limit / urls / cursor / mode / config_error / extra`）。
- `SyncResult` / `sync_source` / `sync_all` / `_run_payload`：`src/finch/sources/orchestrator.py`。
- `build_context_by_source` / `select_exploration_topic` / `_payload`：`src/finch/sources/query_plan.py`。
- `SourceDiscoverySettings.mode`（`Literal["query","hot","url","timeline","disabled"]`）、`ExplorationTopic`（`id/label/queries_by_source/github_users`）、`SourcesSettings.exploration_topics`：`src/finch/settings.py`。
- `finch sources sync`：`src/finch/cli.py`（无 `--json`；输出 `source/status/raw/norm/new/projected/detail`）。
- 配置：`finch.example.yaml`（`sources` L67–127、`products.sources` L151–198；**无 `exploration_topics`**）；运行时 `finch.yaml`（`exploration_topics` L133–201，5 主题）。
- 测试范式：`tests/unit/test_source_query_plan.py`、`tests/unit/test_sources_*.py`。
