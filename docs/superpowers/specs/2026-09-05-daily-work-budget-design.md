# Finch `daily` 有界工作量设计文档

> 状态：已获用户批准（2026-09-05）。
> 目标：把 `finch daily` 从「按 lookback 窗口处理全部 commit」改为「增量摄取全部 commit，
> 但只深度处理每轮预算内最值得输出的一小部分」。超出的 commit 进入 backlog，绝不丢弃。
> 验收：无论一天产生 20 / 100 / 500 个 commit，`daily` 的深度处理量始终不超过
> 12 个 group、36 张规划卡；性能从「由 commit 数决定」变为「由明确预算决定」。

## 0. 阶段划分

本设计覆盖完整 P0–P2 路线图，分三个可独立落地（各自对应一份实现计划）的阶段：

| 阶段 | 内容 | 依赖 | 预期效果 |
| --- | --- | --- | --- |
| **A（P0）** | 每仓库 SHA 增量 ledger + 两段预算 + `select_planning_evidence` | 无 | `daily` 深度处理量有硬上限 |
| **B（P1）** | sealed group / 稳定缓存 + 跨仓库并行 + 全局 semaphore | 阶段 A 的 ledger | 新 commit 不再使旧组失效；多仓库墙钟降低 |
| **C（P2）** | 超大 group 分层提取 + 双轨并行 | 阶段 B | 处理极端 commit burst；降低固定耗时 |

阶段 A 是基础；阶段 B 的 sealed-group 依赖阶段 A 的 per-SHA 状态先行落地。阶段 C 明确
标记为「投机 / 未来工作」，仅在触发条件命中时才有价值。

## 1. 背景与现状（已核实）

- `run_daily`（`cli.py:320`）现为**两套互不相干**的读取机制：
  1. **提取路径**：`since = now - lookback_hours`（24h），逐仓库 `load_commit_details(repo,
     since=...)`。**每轮重读整个窗口**，非增量。
  2. **同步路径**：`make_sync_node` → `CommitReader.sync()` 读一个**独立、共享**的游标文件
     `var/cache/github_sync_state.json`（单一 `last_synced_at`，无 per-repo 键），再调一次
     `gh.list_commits(since=cursor)`，然后在 **extract 节点之前**写 `last_synced_at=now`。
- 该游标是**只写、与真正提取解耦**的：`run_daily` 从不回读它。修复不是「拆分游标」，而是
  用 per-SHA ledger 替换「lookback 窗口 + 共享游标」模型。
- `Extractor.extract`（`evidence/extractor.py`）用 `group_commits`（90 分钟作者窗口 + 共享
  有效词/文件路径）分组，缓存键 = 整组内容指纹。给组 `[1,2]` 追加 commit 3 会改变指纹、
  使缓存整体失效。
- `plan_content_topics`（`content/jobs.py`）把**全部** EvidenceCard 塞进一次模型调用。
- `build_cards` 每个 event 固定产出 problem / decision / result 三张卡。
- `make_extract_node`（`graph/pipeline.py`）逐仓库串行提取；`pack_batches` 不拆单个超大 group。
- 存储为 SQLModel `payload_json` 模式，`Store.init()` 经 `create_all` 注册模型。
- 无 `commit_ingestion` 表。

## 2. 架构决策

采用「**pre-graph 摄取服务 + 纯预算选择器**」（用户已选 A）：

- `run_daily` 在构建节点**之前**运行 `ingestion` 阶段：读新 commit、落 ledger、确定性分组、
  打分排序、套预算，只把预算内的 group 作为 `commits_by_repo` 喂给 graph。
- graph 保持纯：extract 节点对预算内 group 提取，成功时把该组 SHA 标记 `extracted`。
- 优点：契合现有「CLI 先加载、再构建 graph」的形状；`select_groups` 是纯函数、可单测；
  replay/resume 语义不变（graph 输入在 run 开始时固定）。
- 缺点：ledger 状态推进拆在两处（CLI 推 `pending→grouped`/`skipped`，extract 节点推
  `extracted`/`failed`），需定义崩溃后的一致性规则（见 §5）。

## 3. 数据模型（阶段 A 引入，阶段 B 复用）

新增两张表，沿用 SQLModel `payload_json` 模式：

```python
class CommitIngestionRecord(SQLModel, table=True):
    repository: str = Field(primary_key=True)   # 复合主键 (repository, sha)
    sha: str = Field(primary_key=True)
    status: str            # pending | grouped | extracted | skipped | failed
    group_id: str | None = None   # 分组后写入；阶段 B 成为稳定缓存键
    discovered_at: datetime       # 首次摄取（墙钟）→ age_bonus 基准
    authored_at: datetime         # commit author_date → 分组窗口
    retry_count: int = 0          # failed 重试计数
    payload_json: str             # pending 时为 CommitSummary；fetch 后为 CommitDetail
    updated_at: datetime

class RepoCursorRecord(SQLModel, table=True):
    repository: str = Field(primary_key=True)
    last_synced_sha: str | None = None   # SHA 水位线，非日期
    last_synced_at: datetime             # 仅可观测性
```

两个有意为之的选择：

- **ledger（per-SHA）是唯一权威**；`RepoCursorRecord` 只是非权威的优化与可观测性：仓库 HEAD
  SHA 等于 `last_synced_sha` 时跳过 `list_commits`（零新 commit 快速返回），避免每仓库一次
  GitHub API 调用。GitHub 的日期 `since` 按 author-date 过滤、会漏回填 commit，故游标存
  SHA 水位而非日期。
- **摄取时落完整 `CommitDetail`**，提取直接从 ledger 读、永不重取。这才是真正消除
  双重读取（`load_commit_details` + `sync()`）的手段：每 commit 的 GitHub API 开销恰好付一次。

### 状态机

```
pending ──fetch details──▶ grouped ──extract ok──▶ extracted
                            │
                            ├──noise(filter_noise)──▶ skipped    (永久)
                            └──extract error──▶ failed           (下一轮重试，有上限)
```

- **超预算不是 `skipped`**——留在 `grouped` 即为 backlog。
- `skipped` 仅保留给 `filter_noise`（确定性噪声过滤）。模糊的「与历史 EvidenceCard 重复」
  是**打分信号**（novelty），不是硬跳过。
- `failed` 下一轮重试，`retry_count >= max_extract_retries` 后降级 `skipped`。

## 4. 摄取阶段（pre-graph，确定性，无 LLM）

两段有界流程，取代 `make_sync_node` / `CommitReader.sync()` / 共享 `github_sync_state.json`
（三者删除）：

```
for repo in repos:
    if head_sha(repo) == cursor.last_synced_sha: continue      # 零新 commit，快速返回
    commits = gh.list_commits(repo)                 # 从 HEAD 走到 ledger 已知 SHA
    new = [c for c in commits if c.sha not in ledger]
    ledger.upsert(new → status=pending, payload=CommitSummary)   # 幂等，廉价
    repo_cursor.advance(repo, newest_new_sha)        # 非权威优化；仅在上一条 upsert 成功后

# 段 1：从 pending 池挑出要 fetch 详情的候选（含新 + backlog，统一排序）
candidates = select_detail_fetches(pending, max_detail_fetches)
for c in candidates:
    detail = fetch_details(c)                       # 本地 clone 优先，否则 gh
    if filter_noise(detail): ledger.mark(c, skipped)
    else: ledger.store_detail(c, detail)            # payload 升级为 CommitDetail

# 段 2：对已 fetch 的 commit 分组、打分、选出本轮的提取预算
groups = group_commits(fetched_pool)                # 阶段 A：现有滑动窗口；阶段 B：sealed
selected = select_groups(groups, budget)            # ≤ max_change_groups
```

**两段是必要的**：若对全部 500 个 commit 都 fetch 详情再套提取预算，虽然提取被限到 12 组，
但仍付出 500 次 GitHub API 调用。`max_detail_fetches`（40）把最贵的 API 开销也限住。段 1 的
预排序只用 summary 级信号（message 关键词、author_date 新旧、age_bonus、message 级 novelty），
因为文件级信号尚未 fetch；段 2 在 fetch 后使用完整文件级信号。

### `select_groups` —— 统一排序（一个排序，无 LLM）

对合并的 `grouped` 池（新 + backlog）打分：

```
score(group) = priority(group) + age_bonus(group)
```

`priority`（代码中确定性计算，不变量「LLM 不产出总分」保持）：
- `core_source`：改核心源码而非 doc/test/dep 的文件占比（filename 启发式）；
- `churn`：Σ(additions+deletions) 与文件数（对数尺度归一）；
- `keyword`：message 是否含 fix / refactor / performance / architecture / feat；
- `cross_module`：distinct 顶层目录数 / 文件数；
- `novelty`：1 − max(Jaccard(group 关键词/路径 token, 既有 EvidenceCard.topics))；无历史记 1.0。

`age_bonus = w_age · min(age_days / age_bonus_max_days, 1.0)`，`age = now − discovered_at`。
这是唯一防饿死机制。用户已确认「**统一排序、无硬性 backlog 保底槽**」：低优先级 backlog 项
最多等待 `age_bonus_max_days` 才被救回。若实践中发现饿死，可加 `min_backlog_slots`（一行改动）。

`select_groups` 按分数取 top `max_change_groups`，并受 `max_estimated_prompt_bytes` 二次约束
（以 `_render_commits` 字节长度估算，先到先止）。其余留 `grouped` 下一轮。

## 5. extract 节点接线（阶段 A 收尾）

- 重构 `Extractor` 暴露 `extract_grouped(groups, repo) -> list[ExtractedGroup]`，其中
  `ExtractedGroup = (group_id, event, commit_shas)`。`extract(commits, repo)` 变薄封装：先分组
  再委托（保住 `github reflect` CLI 命令）。
- 摄取阶段分组一次、选出预算内 group，把**预分组 group** 传给 extract 节点 → 无双重分组，
  extract 节点拿到 group→commit_shas 映射。
- `make_extract_node` 增加 `IngestionRepository` 参数：成功时把**该组全部 SHA**（取自预分组
  输入，而非模型回显的 `event.commits`，防漏标）标记 `extracted`；`IncompleteBatchExtractionError`
  / 硬失败时标记 `failed`（`retry_count+1`）。

崩溃一致性规则：`pending→grouped` 的推进在 extract 节点之前、由 CLI 完成并已持久化；
`grouped→extracted` 在 extract 节点内、与 `cards_repo.upsert_cards` 同处提交。若 extract 节点
失败，commit 停留 `grouped`/`failed`，下一轮重试——**不会因游标先行推进而遗漏**（正是本次
修复的关键点：旧代码先存游标、后提取，提取失败即漏 commit）。

## 6. `select_planning_evidence`（阶段 A）

一个**纯函数**（同 `select_primary_job`），在 `DefineJobsNode.run` 内、`plan_content_topics`
之前调用，**不是**新 graph 节点：

```
select_planning_evidence(cards, match_results, settings) -> list[EvidenceCard]
```

1. 按 `event_id` 聚合（一个 event = 其 problem/decision/result 三卡）；
2. 确定性排序：`publishable` 优先、`confidence` 更高、被 candidate 匹配（出现在
   `match_results`）、对既有 `ContentJob` 的 novelty（`topics` 上 Jaccard）；
3. 取 top `max_planning_events`，每 event 留 2–3 卡，硬上限 `max_evidence_cards_for_planning`。

仅此裁剪集喂 `plan_content_topics`。**完整**提取集仍留在 graph context 供 `draft`/`critique`/
`brief` 使用，`job.source_card_ids` 仍对完整集解析（裁剪集 ⊆ 完整集，全部校验不变）。

关系说明：`max_change_groups=12` × 3 卡/组 = 36 卡，故 `select_planning_evidence` 默认是
**no-op**（规划上限 == 提取上限）。它是「两个独立旋钮，默认恰好相等」，在解耦时（如提取 20
组覆盖证据、规划仍限 36 卡）或作为兜底上界才生效。这是有意设计。

## 7. 阶段 B：sealed group / 稳定缓存

分组语义从「每次重建」改为「**append-only 单调**」：

- **摄取时**计算每 commit 的原子 `content_hash`（message + 文件路径 + patch 哈希），并把它
  赋给一个*开放* group（若与最新 commit 共享有效词/文件路径、且在 90 分钟作者窗口内）；
  否则新建 group。成员关系**只决定一次**，永不重判。
- **seal 规则**：`now − group.last_discovered_at > seal_window_minutes`（墙钟）即 sealed。
  sealed group 不再吸收 commit，其提取结果不可变，按**稳定 `group_id`**（而非内容指纹）缓存，
  永久复用。
- 新 commit **默认形成新 group**；仅*开放* group 可吸收。

**有意取舍**：日更节奏下，今天建的 group 明天已 sealed，所以几小时后才 push 的相关 commit
会成**新 group**（现有滑动窗口本可能合并）。结果：事件更小、更多；跨天连续改动可能表现为多
个事件而非一个。这是换取稳定、可缓存 group 身份的代价，即用户「新 commit 默认形成新 group」。

**缓存机制**：保留现有 `ExtractionCache` JSON 文件，但 daily 路径的键从 `group_fingerprint`
改为 `group_id`。`reflect` CLI（ad-hoc、无 ledger）继续用内容指纹。此阶段不引入 SQLite
`group_extraction` 表（YAGNI，后续加固再议）。

## 8. 阶段 B：跨仓库并行 + 全局 semaphore

- `make_extract_node` 现逐仓库串行。改为跨仓库 `ThreadPoolExecutor(max_workers=min(repo_count,
  3))`，用 `pool.map` 保证卡序与串行一致（保持确定性顺序不变量）。
- `Extractor` 持有全局 `threading.Semaphore(global_max_concurrency=4)`，在
  `_extract_group_batch` 内、`runner.run` 处 acquire，把「仓库并发 × batch 并发」的瞬时 LLM
  调用总数封顶为 4。

配置：`extraction.global_max_concurrency: 4`（新字段），与既有 `max_concurrent_batches: 2` 并列。

## 9. 阶段 C：超大 group 分层提取

触发条件：单 group 超过 `extraction.max_commits_per_group_prompt: 15` **或**
`extraction.max_group_prompt_bytes: 25000`（`pack_batches` 现不拆单个超大 group）。改为三阶段：

1. 把 group 拆成 ≤15-commit 的块（确定性、保序）；
2. 每块提取一个**局部** `EngineeringEvent`（复用现有 batch prompt，把每块当 group）；
3. 一次**新增** `merge_events` 调用（新 `prompts/merge-engineering-events.md` +
   `MergeEventsOutput` 模型）合并局部事件为最终 event。

触发配置放 `extraction:`（纯提取关注），不放 `daily_budget:`。仅在病态 commit burst 时触发，
是最后实现项。

## 10. 阶段 C：原创 + 互动双轨并行

`run_dual_track` 现按文档「同步顺序执行」。改为 `ThreadPoolExecutor(max_workers=2)` + `pool.map`
包裹两条轨道 lambda，保留既有 per-track `_capture` 异常隔离与相同的 `DualTrackResult` 组装。
必须用 `ThreadPoolExecutor`（不变量禁 `asyncio.gather`）。

已核实无共享表写冲突：原创轨道写 `RunRecord`/`NodeRecord`，互动轨道写
`EngagementRunStatsRecord` 等自身表；共享的仅 `run_id` 字符串值，落在不同表。SQLite WAL 下
多线程并发写安全。

## 11. 配置汇总

```yaml
daily_budget:
  max_detail_fetches: 40            # 每轮从 pending 池 fetch 详情的上限（含新 + backlog）
  max_change_groups: 12             # 每轮提取的 group 上限
  max_planning_events: 12           # plan_topics 的 event 上限
  max_evidence_cards_for_planning: 36
  max_estimated_prompt_bytes: 40000 # select_groups 的字节二次约束
  age_bonus_max_days: 7             # age_bonus 满值天数
  max_extract_retries: 3            # failed → skipped 前重试上限
  sort_weights:                     # priority + age，权重确定性
    core_source: 0.25
    churn: 0.20
    keyword: 0.15
    cross_module: 0.10
    novelty: 0.15
    age_bonus: 0.15

extraction:
  global_max_concurrency: 4         # 阶段 B 全局 LLM 提取并发
  seal_window_minutes: 90           # 阶段 B sealed 墙钟窗口
  max_commits_per_group_prompt: 15  # 阶段 C
  max_group_prompt_bytes: 25000     # 阶段 C
```

## 12. 不变量与错误处理

- **证据优先**：预算只跳过「本次提取」，绝不从 commit 直接写帖；未处理 commit 停留在
  ledger，之后仍走 Commit → Event → Card → Draft。
- **无自动发布**：`gh`/`opencli` 只读不变量不变。
- **确定性总分**：`priority`/`score` 在代码中计算，LLM 输出不含 `total`（含新的 `merge_events`
  也不允许 LLM 决定总分）。
- **子进程纪律**：摄取/提取的 `gh`/codex 调用沿用数组传参 + 超时 + Pydantic 校验。
- **故障隔离**：`failed` 不阻断其他 group；双轨并行后单轨异常仍被 `_capture` 捕获。

## 13. 测试

- `tests/unit/test_ingestion.py`：SHA 增量（重复 commit 幂等、游标仅成功后推进）、
  `filter_noise → skipped`、两段预算（500 commit 下 detail-fetch ≤ 40、group ≤ 12）。
- `tests/unit/test_select_groups.py`：统一排序确定性、age_bonus 防饿死、novelty 对历史卡去重、
  `max_estimated_prompt_bytes` 二次约束。
- `tests/unit/test_select_planning_evidence.py`：event 聚合、裁剪上限、裁剪集 ⊆ 完整集。
- `tests/unit/test_extractor_grouped.py`：`extract_grouped` 预分组路径、成功标 `extracted`（整组
  SHA）、失败标 `failed` + 重试计数。
- `tests/unit/test_sealed_groups.py`（阶段 B）：seal 后指纹稳定、新 commit 成新组。
- `tests/unit/test_merge_events.py`（阶段 C）：超大组拆块 + merge。
- 集成：`finch daily` 在 fixture 下产出 ≤12 group / ≤36 规划卡，剩余 commit 留在 ledger。

## 14. 范围外（明确不做）

- 互动轨道（`engagement/`）不受预算影响，仍独立运行；预算只作用于原创轨道。
- 不引入 SQLite `group_extraction` 表（阶段 B 仍用 JSON 缓存）。
- `merge_events`（阶段 C）与双轨并行（阶段 C）标记为投机，实现顺序最后。
- 不改动 `pack_batches` 的现有拆批逻辑（仅在其上叠加超大组分层）。
