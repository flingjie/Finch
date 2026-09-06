# 收件箱深度重构：跨过「不重写」线，收敛内部（Graph / 存储 / CLI / 模块）

> 状态：待用户审阅（方向已锁定：深度重构，四个内部杠杆全做）。
> 本设计**修订** [[2026-09-06-product-inbox-design]] 的 §0/§1 范围。product-inbox-design 把范围锁在
> 「加性、不重写」；本设计经用户确认**越过这条线**，把 Finch 的内部也一起收敛——不只是给产品加一层
> 收件箱投影，而是把 10 节点图、两套决策存储、四套人工命令、两个大文件一并简化与解耦。

## 0. 结论与原则

- **产品是收件箱，内部才是编译器——但编译器也要简洁。** product-inbox-design 的六样东西不变：
  来源、机会、草稿、你的决定、发布记录、结果。本设计进一步把「编译器」内部收敛到与产品循环同构：
  三个收集阶段、一个选择、一个写入。
- **Graph 只产出状态，不产出文本。** 今日简报、决策卡、人类文案全部移到产品层（inbox 模块）；
  Graph 节点变成纯计算，`brief` 节点删除。
- **对人的门只有一次：审稿（next/decide）。** Graph 永不因立场或检查器停在 `NEEDS_INPUT`；
  `position_incomplete`、安全检查、立场冲突都降级为决策卡上的 `must_ask`。`gate/` 交互层、
  `run resume`/`run resolve` 删除。
- **决策记录唯一权威。** `DecisionRecord` 是采用/跳过/修订的唯一权威记录；`ReviewDecision`、
  `PositionApproval`（复用门禁）、`AuthorPosition.confirmed`/`position_source` 三个「向后兼容投影」
  全部删除。不保留「同样立场不再问」的快捷。
- **证据优先仍是规则，不是对象链。** `Commit → EngineeringEvent → EvidenceCard → Draft` 不变量保留；
  `EngineeringEvent` 继续不进 CLI/决策卡。外部帖 ≠ 个人证据不变。
- **不自动发布不变。** `gh`/`opencli` 只读；发布由人站外完成，`author sync` 只读匹配。
- **分数由代码算不变。** `weighted_total`、主题排序、预算裁剪全部在 Python；模型不得输出 `total`。
- **加性取消，改为迁移。** 本设计删除表与命令；用 alembic 迁移（仓库已有 alembic），验收标准是
  replay 测试全绿 + 全量单测通过。

## 1. 范围

本文件是**深度重构总设计**。它修订 product-inbox-design 的「加性」约束，并把四个内部杠杆合并成
一份连贯的目标架构。落地按 §10 三阶段拆开，每阶段各自一份实现计划。

**本次决定（四个杠杆全做）**：

- A. **Graph 拓扑收敛**：10 节点 → 7 节点（`select` 合并 define_jobs+position_gate，`write` 合并
  draft+critique，`brief` 出图）。
- B. **存储投影收敛**：`DecisionRecord` 唯一权威；删除 `ReviewRecord`/`ReviewHistoryRecord`/
  `PositionApprovalRecord` 表与 `review/`、`gate/` 模块；`AuthorPosition.confirmed`/`position_source`
  删除。
- C. **审核面收敛为单一门**：`next`/`decide` 是唯一人工门；`review`/`engagement`/`jobs`/`gate` 命令
  全删；`feedback` 提升为 `learn`，`run weekly` 提升为 `weekly`。
- D. **模块解耦**：新增 `inbox/` 模块（投影 + 选择 + 决策 + 渲染）；拆分 `cli.py`（1485 行）与
  `content_nodes.py`（1036 行）。

**范围外（明确不做）**：

- 不把 `EngineeringEvent`/`EvidenceCard`/`ContentJob`/`InteractionCandidate` 合成一张持久化表
  （product-inbox-design §1 保留）。
- 不改 `GraphRuntime` 的顺序/幂等/重放语义；只改节点拓扑（节点少几个、节点内部合并）。
- 不重做发布匹配（见 [[2026-09-06-author-publication-autolink-design]]）。
- 不重做 idea 评估/写稿 prompt（见 [[2026-09-06-idea-lightweight-entry-design]]）；只规定 `draft` 别名
  与进入收件箱。
- 不改互动五维打分公式；`weighted_total` 是唯一 `total` 计算点。

## 2. 背景与现状（已核实）

- 每日原创图固定 10 节点：`preflight → extract → collect → recall → match → define_jobs →
  position_gate → draft → critique → brief`（`src/finch/graph/daily.py`）。
- `define_jobs` 两阶段：`plan_content_topics` 一次聚类 → 对**每个**主题 `expand_content_job` 并行展开 →
  校验去重 → upsert 全部合法 job（`content_nodes.py` 的 `make_define_jobs_node`）。
- `position_gate`：`select_primary_job` 选出唯一 primary；立场不完整（decision/tradeoff 空）→
  `needs_input` **整图停**；复用门禁（`PositionApproval` fingerprint）；confirmed 放行；否则 `INFERRED`
  放行（`make_position_gate_node`）。
- `critique`：`default_checker_suite` 8 个检查器（Evidence/Decision/Specificity/Portability/Voice/
  Structure/Actionability/Safety），多数注入 runner（LLM）；每篇最多 `max_rewrite_rounds+1` 轮。
  确定性部分已有 `validate_draft`；LLM 部分默认全开。
- `decide`（`DecisionService.accept`）一次写 5 处：`ContentJob.author_position`（投影 1）、
  `PositionApproval`（投影 2）、`ReviewDecision`（投影 3）、`DecisionRecord`（权威）、
  `PublicationIntent`（autolink）。`next` 只处理原创草稿，不含互动。
- 互动：`finch engagement list/show/approve/reject/edit/metrics` 是独立审核面；
  `InteractionCandidate.status ∈ {PROPOSED,APPROVED,REJECTED,EXECUTED}`。
- `finch idea` 已落地（`run_id="idea"`，assess → write → 7 检查器 critic → `ContentJob`+`Draft`）。
- `review/models.py` 混放了 `DecisionRecord`/`DecisionAction`（新）与 `ReviewDecision`/`ReviewAction`
  （旧）；`voice approve-example` 依赖 `review confirm-position` 的 `voice_match`；`run weekly` 读
  `ReviewRepository`。

## 3. 产品循环

不变（与 product-inbox-design §3 一致）：

```text
收集来源 → 选出机会 → 写成草稿 → 你来决定 → 发布并学习
```

| 步骤 | 用户看见 | 内部（用户不必看见） |
| --- | --- | --- |
| 收集来源 | `finch daily` 或 `finch draft "…"` | GitHub 摄取、X/Reddit 搜索、想法评估、账号历史同步 |
| 选出机会 | 无（或一句「今天 N 条」） | 预算、规划裁剪、主题排序、互动五维分、`select` 先选后写 |
| 写成草稿 | 无 | Event 提取、EvidenceCard、expand job、write、L0 确定性、按需 L1 |
| 你来决定 | `finch next` 一张卡 → `decide` | DecisionRecord、互动 APPROVED/REJECTED |
| 发布并学习 | 站外发出；偶尔补一句「认不认」 | `author sync` 匹配 URL、`learn` 记结果、`weekly` 指标 |

两条收集适配器（原创/互动）继续独立跑、故障隔离；汇合点是收件箱，不是 Graph 合并。

## 4. Graph 收敛（10 → 7 节点）

```
preflight → extract → collect → recall → match → select → write
```

五个「收集」节点**不变**：它们是不同来源、不同故障模式（`preflight` 检查 gh/opencli 可用；`extract`
GitHub Commit→Event→Card；`collect` X 搜索；`recall` 旧证据；`match` 证据↔讨论），硬并只会增耦合。
它们继续各自独立 checkpoint，replay 粒度不受损。

### 4.1 `select`（= 合并 define_jobs + position_gate）

- **读**：`match_results`、`evidence_cards`、`candidates`。
- **写**：`ready_jobs`（单一份，不再有 `content_jobs`/`ready_jobs` 两份）。
- **succeeds_to**：`JOBS_SELECTED`（新状态，替代 `JOBS_DEFINED`/`POSITIONS_READY`）。
- 流程：
  1. `plan_content_topics` 一次调用（沿用现有 flash 拆分），产出主题列表。
  2. **确定性排序**主题：沿用 `select_primary_job` 的实质键（confirmed 优先、有讨论上下文、VERIFIED/
     SUPPORTED 占比、decision+tradeoff 齐全、why_now、稳定 id）。不把 LLM `total` 当排序。
  3. 预过滤（沿用现有逻辑：candidate 不在 match、card_ids 空、card_ids 越界、按 topic.id 去重）。
  4. **只展开 Top K**（`expand_content_job`）。K = `quality_gates.max_daily_original_posts`（默认 1）。
     其余主题不展开、不写完整 job；未展开主题标题进 `output["unexpanded_topics"]`（供调试，不进收件箱）。
  5. 展开失败取排序下一个主题，最多递补一次（与现 primary 递补一致）。
  6. 校验去重（现有 `validate_source_cards` + job.id 去重）。
- **不阻塞**：`decision`/`tradeoff` 为空时**不** `needs_input`；job 照常进入 `ready_jobs`。
  是否 `must_ask` 由 inbox 投影在下游计算（见 §7.2），不写在 job 上。
- **删除复用门禁**：不再调用 `position_fingerprint` / `PositionApprovalRepository` / `find_active`；
  不写 `REUSED`。`position_incomplete` 的 `input_request`/`questions` 输出一并删除。
- `draft` 节点的 `max_daily_original_posts`/`max_daily_replies` cap 变成冗余（ready_jobs 已被 K 限制），
  随 `write` 合并时删除。

### 4.2 `write`（= 合并 draft + critique）

- **读**：`ready_jobs`、`evidence_cards`、`candidates`、`match_results`。
- **写**：`drafts`（保留通过的草稿）。
- **succeeds_to**：`DRAFTED`（替代 `DRAFTED`/`CRITIQUED` 两个边界）。
- 流程：对每个 ready job 写稿（`write_original`/`write_reply`），然后：
  - **L0 确定性（每篇必跑，无 LLM）**：`validate_draft`（无证据/越界主张 → hard_fail，idea 草稿跳过）、
    密钥扫描（`scan_secrets`）、字数上限（沿用质量门，超限 → rewrite instruction，不调模型）。
  - **L1 LLM（默认不跑）**，写节点内触发任一即跑**一轮**（不是 8 路再乘 `max_rewrite_rounds` 的全量）：
    - L0 未通过；或
    - `quality_gates.llm_critique_mode = "always"`（调试，默认 `"on_fail_or_gate"`）。
  - 采用前的 L1 补跑在 `decide` 服务里做（见 §7.2），不在写节点内；「`must_ask` 含 `safety_risk`/
    `position_conflict`」是 decide 时的触发，不是写时触发（写时尚未有 must_ask）。
  - L1 仍用现有 8 检查器里依赖 runner 的部分；`aggregate_checks` 规则不变。`max_rewrite_rounds` 只在
    L1 触发时生效，默认改为 1（配置项，既有测试同步）。
  - 检查器 `requires_human_input` 且无草稿可审 → 不再 `needs_input`，改为把 `safety_risk`/
    `position_conflict` 记进草稿的 critic 报告，由 inbox 投影读到卡上。
- 保留 `reports` 输出（round/version/checks/outcome），供 `persist_critique_reports` 持久化
  DraftVersion/CriticReport——checkpoint 仍在，用户看不见。

### 4.3 `brief` 移出 Graph

- 删除 `make_brief_node` 与 300+ 行 `_render_daily_brief`/`_*_section` 渲染函数。
- 「今天 N 条待决定」由 inbox 投影在 CLI 层渲染（`next` 已算出待决策集）。
- `DailyBrief` 模型不再作为 Graph 输出；需要人类摘要时由 inbox `render.py` 从投影集派生。
- 副作用：`GraphState.NEEDS_INPUT` 在 daily 路径**不再出现**——图总是跑到 `COMPLETED`/`WAITING_FOR_REVIEW`
  （有草稿则 `review_required`）。`_finish_daily`/`_read_input_request`/`_mark_stopped`/`_resume_and_echo`
  等交互收尾逻辑随 `gate/` 一起删除。

## 5. 存储收敛：`DecisionRecord` 唯一权威

### 5.1 删除

- **表（alembic 迁移）**：`ReviewRecord`、`ReviewHistoryRecord`、`PositionApprovalRecord`。
- **模块**：`review/`（`ReviewDecision`/`ReviewAction`/`DecisionService`/`confirm-position`/`service.py`/
  `feedback.py`/`weekly.py`）、`gate/`（`interactive.py`/`models.py`/`render.py`/`resolve.py`）。
- **函数/字段**：`position_fingerprint`、`PositionApprovalRepository`、`AuthorPosition.confirmed`、
  `AuthorPosition.position_source`、`PositionSource` 里的 `REUSED`/`INFERRED`（`HUMAN_CONFIRMED` 若已无
  区分需求也一并收窄）。

### 5.2 保留与迁移

- **`DecisionRecord` + `DecisionAction`**（现在误放 `review/models.py`）→ 迁入 `inbox/models.py`。
- **`DecisionRecordRepository`** 保留，是唯一决策读写。
- **`PublicationIntent`/`PublicationLink`** 保留（autolink）。
- **`Feedback`/`FeedbackRepository`** 保留 → 迁入 `learn` 模块（承载 `finch learn`）。
- **`weekly` 分析** → 改读 `DecisionRecordRepository` + `FeedbackRepository` + `PublicationLink`；
  迁出 `review/`。
- **`voice approve-example`** → 门改读 `DecisionRecord.action == ACCEPT`；`voice_match` 确认仪式删除
  （`ReviewDecision.voice_match`/`position_correct`/`job_clear` 随之消失）。

### 5.3 `decide` 语义（统一双轨）

| 动作 | original（ContentJob+Draft） | engagement（InteractionCandidate） |
| --- | --- | --- |
| accept | 写 `DecisionRecord(ACCEPT)`（含 `approved_content_hash`）+ `PublicationIntent`。不再写 `ReviewDecision`/`PositionApproval`。立场字段空则只留空，不用占位句填 | `status=APPROVED`；仍不执行发布（`guard` 不变） |
| revise | 按 instruction 重写 + 条件 L1，不写 ACCEPT；`revised_body`/`diff` 落 `DecisionRecord` | 写 `revised_draft`（无则写 `draft`），status 仍 proposed |
| skip | `DO_NOT_WRITE` + `reject_reason`，写 `DecisionRecord(SKIP)` | `REJECTED` + `reject_reason` |

`decide` 用 `id` 在两张表解析：先查 `ContentJob`，再查 `InteractionCandidate`；两边都没有 →
`typer.Exit(1)`。不要求 `--track`。冲突 id（不应出现）以 ContentJob 为准并告警。

## 6. CLI 收敛：只留产品面

```bash
finch daily [--json]
finch draft "一个想法或片段" [--json]     # = finch idea 别名
finch next [--json]
finch decide <id> --action accept|revise|skip [--instruction ...] [--json]
finch author sync
finch learn <draft_id> [--url --metrics --outcome --learning]
finch weekly
```

保留的基础设施与只读适配器：`init`、`diagnose`、`github *`、`twitter *`、`voice *`、`dev *`。

**删除**：`review *`、`engagement *`、`jobs *`、`gate *`、`run resume`、`run resolve`。

- `finch idea` 保留为 `draft` 别名（行为与 idea 设计一致）。
- `run_id` 仍在 `--json` 返回给编排层；人类默认输出不打印。
- 理想人类输出只描述收件箱，不描述节点（沿用 product-inbox-design §8 的示例）。
- `$finch` skill 循环：`daily --json` → 直到 `next` 为 `none` 为止 `next` → 渲染卡（含互动）→ `decide`。
  不再把用户带到 `engagement`/`review` 子命令。

## 7. 模块布局（去耦合）

```
src/finch/
  inbox/            # 新：产品层（唯一同时认识 original 与 engagement 的地方）
    models.py       #   InboxItem / InboxTrack / DecisionCard / DecisionRecord / DecisionAction
    service.py      #   build_inbox_item（投影）、next_item（确定性选择）、decide（双轨分发）、
                    #   run_l1_before_decide（采用前补跑 L1）
    render.py       #   人类输出（「今天 N 条」、决策卡）
  learn/            # 新：发布后学习（Feedback 迁入），承载 finch learn
  graph/
    select_nodes.py #   make_select_node（原 define_jobs + position_gate）
    write_nodes.py  #   make_write_node（原 draft + critique）+ L0/L1 分层
    content_nodes.py#   删除（残余的 checker suite / _run_checks 迁到 content/critic.py）
  cli.py            # 瘦身为薄命令层：load settings/store + 调服务 + 渲染
```

- **`inbox/service.py`** 是唯一「跨轨」决策处：`decide` 对 original 走 DecisionRecord、对 engagement 走
  `InteractionCandidate.status`。Graph 与 storage 都不需要知道对方。
- **`inbox/models.py` 的 `InboxItem`**（沿用 product-inbox-design §4）：

  ```python
  class InboxTrack(StrEnum):
      ORIGINAL = "original"      # ContentJob + Draft（含 idea_ 前缀）
      ENGAGEMENT = "engagement"  # InteractionCandidate

  class InboxItem(BaseModel):
      id: str                    # original=job_id；engagement=candidate.id
      track: InboxTrack
      content_type: Literal["original", "reply", "quote"]
      provenance: Literal["personal", "external", "idea"]
      source_refs: list[str]     # commit URL / 帖子 URL / idea 文本摘要
      why_now: str
      score: float               # 原创用 primary 排序键映射 0–1；互动用 ConversationScore.total
      draft_id: str | None
      draft: str
      position: dict | None      # claim/decision/tradeoff；互动可空
      must_ask: bool
      ask_reasons: list[str]
      risks: list[str]
  ```

- **`next_item` 选择规则（确定性）**：
  1. 未决策原创草稿（无 `DecisionRecord` accept/skip）。
  2. `status=proposed` 且带草稿的互动候选。
  3. 排序（跨轨分数不可比，禁止用 `score` 混排）：先 `must_ask=True`；再 `track=original`（含 idea）
     先于 `engagement`；同 track 内 `score` 降序、`id` 升序。
  4. 返回第一条；空则 `{"status":"none"}`。
- **`must_ask` 在投影里派生**（不写回 job）：来自 (a) job 立场是否完整（`position_incomplete`）、
  (b) 草稿 critic 报告的 `safety_risk`/`position_conflict`（`requires_human_input`）。
- **`decide` 采用前补跑 L1**：`decide accept`/`revise` 之前跑一轮 L1（复用 content/critic 的共享
  函数），结果进卡 `risks`；本轮已跑过且正文未变则跳过。这条与 product-inbox-design §7 一致。
- **依赖方向**：`cli.py → inbox/ → (content/critic、content/jobs、engagement/models、
  storage/repositories)`。`inbox/` 不依赖 `cli.py`、不依赖 `graph/`；`graph/` 不依赖 `inbox/`，无循环。

## 8. 错误处理

- `next` 无项目：`{"status":"none"}`，退出码 0。
- `decide` 找不到 id、skip 无 `--reason`、revise 无 `--instruction`：干净错误，退出码 1。
- 一条轨道失败：`run_dual_track` 仍 `partial_failure`；收件箱只展示成功轨道项目。
- L1 critic / revise 子进程失败：不覆盖已有草稿，返回结构化 error JSON；编排层可重试或跳过该条。
- `position_incomplete` 不再造成 daily 停在 `NEEDS_INPUT`；daily 在有草稿时以 `review_required` 结束。

## 9. 测试

- `select`：N 个主题只 expand K 个；K 失败后递补一次；未展开主题不出现在 `ready_jobs`、出现在
  `unexpanded_topics`；空 decision/tradeoff **成功**放行（不再 `needs_input`）。
- `write`：L0 失败才进 L1；L0 全过且非 gate 时 runner 不被调用；`always` 模式回归旧行为；
  `requires_human_input` 记进报告而非停图。
- `InboxItem` 投影：一条原创、一条互动、一条 idea 草稿都能被 `next` 按 §7.2 规则选中；`must_ask`
  优先；无 must_ask 时 original 先于 engagement。
- `decide` 解析：job_id 走 original 落地（`DecisionRecord` + `PublicationIntent`，**不写**
  `ReviewDecision`/`PositionApproval`）；候选 id 走 APPROVED/REJECTED；冲突 id 以 ContentJob 为准并告警。
- CLI：只存在产品命令；`review`/`engagement`/`jobs`/`gate`/`run resume`/`run resolve` 已删除；
  `draft` 与 `idea` 同行为；人类输出不含 `run_id`；`--json` 含 `track`。
- 回归：证据链单测、互动 `weighted_total`、不自动发布、`promote_to_personal` 门禁、dual_track 故障
  隔离、`test_daily.py` 的 7 节点顺序、replay 全绿。

## 10. 分阶段

不要一份计划覆盖全部。三份实现计划，每份可独立合并、replay 全绿：

| 阶段 | 内容 | 用户可感知结果 | 依赖 |
| --- | --- | --- | --- |
| **1** | 统一收件箱 + 存储收敛：加 `inbox/`，`next`/`decide` 收编原创+互动，删 `review`/`engagement`/`jobs`/`gate`/`run resume`/`run resolve`，删 `ReviewRecord`/`ReviewHistoryRecord`/`PositionApprovalRecord`，迁 `learn`/`weekly`/`voice` 读者 | 一个 `next`/`decide` 审完原创和回复；旧命令消失 | 无 |
| **2** | Graph 收敛：`select`/`write` 合并、条件 Critic、`brief` 出图、`NEEDS_INPUT` 从 daily 路径消失 | daily 更快、更少停住 | 1 |
| **3** | 模块解耦收尾：拆 `content_nodes.py`、瘦 `cli.py`、清死代码（`gate/`、`review/`、`position_fingerprint`） | 代码面干净 | 2 |

发布学习继续走 [[2026-09-06-author-publication-autolink-design]]，不阻塞 1–3。

## 11. 迁移

- **强制 alembic**：drop `reviewrecord`（`ReviewRecord`）、`reviewhistoryrecord`（`ReviewHistoryRecord`）、
  `positionapprovalrecord`（`PositionApprovalRecord`）。
- `ContentJob`/`DecisionRecord`/`InteractionCandidate` 等以 `payload_json` 存储，`AuthorPosition` 字段
  删除（`confirmed`/`position_source`）不需列迁移，只需改 Pydantic 模型 + 旧 JSON 反序列化兼容
  （`Field(default=...)` 兜底）。
- `QualityGates` 增加 `llm_critique_mode: Literal["on_fail_or_gate","always"] = "on_fail_or_gate"`，
  `max_rewrite_rounds` 默认 1；旧配置缺省兼容。
- 文档：落地阶段 1 时改 `CLAUDE.md` 互动段「审批队列」为「写入统一收件箱」；证据链句保留，加一句
  「对象链是实现，不是用户流程」；删除对 `review`/`engagement`/`jobs` 命令的描述。

## 12. 与既有设计的关系

- [[2026-09-06-product-inbox-design]]：本设计是其**深度版本**，修订 §0/§1 的「加性」约束并落实
  §9 里推迟的「减节点」。六样东西、证据优先、不自动发布、分数由代码算全部继承。
- [[2026-09-06-single-decision-point-design]]：`DecisionRecord`/`decide accept` 语义保留；修订为
  「不再写 `ReviewDecision`/`PositionApproval` 双重投影」「互动进入同一 `next`」「`position_incomplete`
  不停图」。
- [[2026-09-06-author-position-confirmation-design]] / [[2026-09-06-needs-input-interactive-step-design]]：
  `gate/` 交互层随本设计删除；产品路径不再经过它们。
- [[2026-09-05-daily-work-budget-design]]：预算仍在收集/规划之前；本设计在预算之后再加「只展开 Top K」。
- [[2026-09-06-idea-lightweight-entry-design]]：服务层不变；产品名增加 `draft`；产出进入收件箱。
- [[2026-09-06-author-publication-autolink-design]]：即第 5 步「发布并学习」的实现，本文件不重复。
- `CLAUDE.md` 双轨：收集与评分继续两套；产品面不再是两套审核；内部 Graph 由 10 节点收敛为 7。
