# 产品收件箱：从第一性原理压缩 Finch 设计文档

> 状态：待用户审阅（方向已锁定：压缩产品循环，保留证据卡）。
> 目标：把 Finch 从「操作内容生产 Graph」收成「一个收件箱」——收集来源、选出机会、写成草稿、你来决定、发布并学习。证据卡留下；事件提取和立场确认降为起草内部步骤；原创与互动共用审核列表，不合并存储。

## 0. 结论与原则

- **产品是收件箱，内部才是编译器。** 用户只面对「一条值得说的话」：来源、为什么现在写、草稿、采用 / 修改 / 跳过。Graph 节点名、`run_id`、`resume`、`confirm-position` 不是产品。
- **少不了的六样东西：** 来源、机会、草稿、你的决定、发布记录、结果。其余（Event、独立立场确认、十个节点、两套审核命令）都是工序，不是产品对象。
- **证据优先是规则，不是用户必须走完的对象链。** 规则仍是：不能从 commit 直接编造可发布主张；草稿必须能追溯到来源。实现上继续 `Commit → EngineeringEvent → EvidenceCard → Draft`，但 Event 不再进入 CLI / 决策卡。
- **外部帖 ≠ 个人证据。** 搜到的帖子可以成为收件箱里的一条（回复 / 引用），标记为 `provenance=external`，永远不能写成 `EvidenceCard`。只有经过既有 `promote_to_personal` 的对话证据才能升格。
- **对人的门只有一次：审稿。** 采用同时表示「这个立场是我的」和「这份草稿可用」。公开发布仍在 Finch 之外由人完成；Finch 只读、只发现、只学习。
- **分数由代码算。** 选题排序、互动五维 `total`、预算裁剪全部在 Python 里完成；模型不得输出 `total`。
- **加性、不重写。** 不 drop Event / Card / Job / InteractionCandidate 表；不把它们合成一张 `ContentCandidate`。新增的是**投影** `InboxItem` 和收件箱读写，不是新的持久化实体。
- **真正不可逆的是公开发布和重复发帖。** 生成不准确的候选草稿没有实质风险（adapter 只读）。发布幂等（`PublicationIntent.content_hash` + 互动候选稳定 id）保留。

## 1. 范围

本文件是**总设计**。它修订「单一决策点」里「互动队列不变」这一条，并规定其余已批准设计如何接到同一个产品循环上。落地按 §12 四阶段拆开，每阶段各自一份实现计划。

**本次决定（架构）**：

- A. **统一收件箱**：`finch next` / `decide` 同时处理原创 `ContentJob+Draft` 与互动 `InteractionCandidate`。
- B. **先选后写**：`define_jobs` 先用便宜步骤选出 Top K 主题，再只对入选主题 `expand_content_job`。
- C. **立场不完整不再停图**：`position_incomplete` 变成决策卡上的 `must_ask`，Graph 继续出草稿。
- D. **条件 Critic**：确定性检查必跑；LLM 检查器默认只在失败、高风险、或用户准备采用时运行。
- E. **产品 CLI**：日常入口为 `daily` / `draft` / `next` / `decide`。`draft` 是已批准 `idea` 入口的产品名（`idea` 保留为别名）。

**范围外（明确不做）**：

- 不把 `EngineeringEvent` / `EvidenceCard` / `ContentJob` / `InteractionCandidate` 合成一张持久化表。
- 不删除 daily Graph 的节点；不改 `GraphRuntime` 的顺序 / 幂等 / 重放语义。
- 不给 `gh` / `opencli` 写权限；不自动发帖。
- 不把 `ExternalPost` 升级为 `EvidenceCard`（既有 `promote_to_personal` 路径除外）。
- 不在本设计重做发布匹配（见 [[2026-09-06-author-publication-autolink-design]]）。
- 不在本设计重做 idea 评估 / 写稿 prompt（见 [[2026-09-06-idea-lightweight-entry-design]]）；只规定它如何进入收件箱，以及产品命令名。

## 2. 背景与现状（已核实）

- 每日原创图固定 10 个节点：`preflight → extract_events → collect_tweets → recall → match_evidence → define_jobs → position_gate → draft → critique → brief`（`tests/graph/test_daily.py`）。
- `run_daily` 经 `run_dual_track` 并行跑原创图与互动流；互动产出 `InteractionCandidate`（可含草稿），审批在 `finch engagement`，不进 `next` / `decide`（`cli.py`、`engagement/flow.py`）。
- 单一决策点（[[2026-09-06-single-decision-point-design]]，已批准并部分落地）：可推断立场标记 `INFERRED` 后放行；`finch next` / `decide` 已存在；`decide accept` 同时确认立场与批准草稿。**立场字段空仍 `needs_input`，整张图停。** 范围当时明确不含互动队列。
- `define_jobs` 已两阶段（`plan_content_topics` 一次聚类，再 `expand_content_job` 并行展开）。`select_primary_job` 只让 1 条进入 `ready_jobs`。预算（[[2026-09-05-daily-work-budget-design]]）限制提取 group 与规划卡。仍会先把规划集里的主题展开成完整 Job，再从中选 primary。
- Critic Suite 默认 8 个检查器，多数注入 `CodexRunner`；`make_critique_node` 对每篇草稿最多 `max_rewrite_rounds+1` 轮。确定性部分已有：`validate_draft`、密钥扫描；LLM 部分（蕴含、voice、specificity 等）默认全开。
- `finch idea` 已单独设计（[[2026-09-06-idea-lightweight-entry-design]]）：不进 `run_daily`，落库为已确认的 `ContentJob` + `Draft`，`run_id="idea"`。产品对话里的名字是 `draft`，与该 spec 的命令名不一致。
- 发布自动关联已设计（[[2026-09-06-author-publication-autolink-design]]）：Finch 不发帖，只读同步作者历史并匹配已批准草稿。
- 不变量（`CLAUDE.md`）：证据链、不自动发布、外部帖 ≠ 证据、分数由代码算、双轨每轮都跑。本设计**不废除这些规则**；修订的是「证据链是否等于用户流程」以及「双轨是否等于两套产品」。

## 3. 产品循环

用户只走五步。内部工序挂在步骤下面，不单独成为命令。

```text
收集来源 → 选出机会 → 写成草稿 → 你来决定 → 发布并学习
```

| 步骤 | 用户看见 | 内部可以做（用户不必看见） |
| --- | --- | --- |
| 收集来源 | `finch daily` 或 `finch draft "…"` | GitHub 增量摄取、X/Reddit 搜索、想法评估、账号历史同步 |
| 选出机会 | 无（或 brief 里一句「今天 2 条」） | 预算、规划裁剪、主题排序、互动五维分、`select_primary_job` |
| 写成草稿 | 无 | Event 提取、EvidenceCard、expand job、write、确定性检查、按需 LLM critic |
| 你来决定 | `finch next` 一张卡 → `decide` | 立场 fingerprint、DecisionRecord、互动 APPROVED/REJECTED |
| 发布并学习 | 你在站外发出去；偶尔补一句「认不认」 | `author sync` 匹配 URL、指标快照、voice 样例 |

两条收集适配器（原创 / 互动）继续独立跑、故障隔离；汇合点是收件箱，不是 Graph 合并。

## 4. 收件箱投影（A）

不新增业务表。新增只读投影类型，由 `next` / `decide` 使用：

```python
class InboxTrack(StrEnum):
    ORIGINAL = "original"      # ContentJob + Draft（含 idea_ 前缀的 job）
    ENGAGEMENT = "engagement"  # InteractionCandidate

class InboxItem(BaseModel):
    id: str                    # original=job_id；engagement=candidate.id
    track: InboxTrack
    content_type: Literal["original", "reply", "quote"]
    provenance: Literal["personal", "external", "idea"]
    source_refs: list[str]     # commit URL / 帖子 URL / idea 文本摘要
    why_now: str
    score: float               # 原创用 primary 排序键映射到 0–1；互动用 ConversationScore.total
    draft_id: str | None
    draft: str
    position: dict | None      # claim/decision/tradeoff/source；互动可空
    must_ask: bool
    ask_reasons: list[str]
    risks: list[str]
```

**`next` 选择规则（确定性）**：

1. 列出未决策的原创草稿（无 `DecisionRecord` 的 accept/skip；与现逻辑相同）。
2. 列出 `status=proposed` 且带草稿的互动候选。
3. 排序（跨轨道分数不可比，禁止用 `score` 混排）：先 `must_ask=True`；再 `track=original`（含 idea）先于 `engagement`；同一 track 内 `score` 降序、`id` 升序。
4. 返回第一条。空则 `{ "status": "none" }`。

**`decide` 语义**：

| 动作 | original | engagement |
| --- | --- | --- |
| accept | 现有原子落地（DecisionRecord + 立场确认 + ReviewDecision + PublicationIntent） | `InteractionCandidate.status=APPROVED`；仍不执行发布（`guard` 不变） |
| revise | 现有：按 instruction 重写 + 条件 critic，不落 accept | 重写 `revised_draft`（无则写 `draft`），status 仍 proposed |
| skip | `DO_NOT_WRITE` + reject_reason；可递补下一条原创 | `REJECTED` + reject_reason |

`decide` 用 `id` 在两张表里解析：先查 `ContentJob`，没有再查 `InteractionCandidate`；两边都没有 → `typer.Exit(1)`。不要求用户传 `--track`。

**决策卡 JSON** 在现有 `next --json` 上加 `track` / `content_type` / `provenance` / `source_refs` / `score`；缺字段的旧客户端仍能读 `job_id`（original 时 `id` 同时作为 `job_id` 输出以保持兼容）。

互动的 `finch engagement` 命令保留为调试接口，与 `jobs confirm-position` 同级。

## 5. 先选后写（B）

现状：`plan_content_topics` → 对每个主题 `expand_content_job` → `select_primary_job` 只留 1 条。

改为：

1. `plan_content_topics` 仍一次调用（或沿用 flash 拆分），产出主题列表（不含完整 Job）。
2. **确定性排序**主题：沿用 `select_primary_job` 的键（立场是否可写、是否有讨论匹配、证据置信度、稳定 id），不把 LLM `total` 当排序。
3. 只对 Top K 调用 `expand_content_job`。K = `quality_gates.max_daily_original_posts`（默认 1）。其余主题不展开、不写完整 Job；brief 可列「未展开主题标题」供调试，不进收件箱。
4. 展开失败则取排序中的下一个主题，最多递补一次（与现 primary 递补一致）。

互动侧已经是「打分 → 只给高分动作写草稿」；本设计不改互动打分公式，只要求带草稿的候选进入收件箱。

## 6. 立场确认并进审稿（C）

修订 [[2026-09-06-single-decision-point-design]] §3.1：

| 信号 | 旧行为 | 新行为 |
| --- | --- | --- |
| `decision`/`tradeoff` 可推断、未确认 | 放行，`INFERRED` | 不变 |
| 复用门禁命中 | `REUSED` 放行 | 不变 |
| `decision`/`tradeoff` 为空 | Graph `needs_input`，整图停 | **放行**；`ready_jobs` 含该 job；`must_ask=["position_incomplete"]` 写在决策卡上 |
| 安全 / 立场冲突 | `must_ask` 在卡上（Graph 已放行） | 不变；仍不为此停图 |

草稿节点对不完整立场：允许调用写稿；prompt 把空字段写成「立场待作者在审稿时确认」。写稿失败（结构化校验失败）计为该 job 失败并递补，**不**退回 `needs_input`。

`decide accept`：正文存在即可采用。采用 = 以**最终正文**为作者立场（`approved_content_hash` 为准）。若 `decision`/`tradeoff` 仍空：把 `confirmed=True`、`position_source=HUMAN_CONFIRMED` 写上，字段保持空，**不**用占位句去填；fingerprint 只在三字段都非空时写入 `PositionApproval`，否则跳过复用门禁（避免空立场被当成可复用偏好）。

Graph 的 `NEEDS_INPUT` 不再用于立场确认。仍可用于既有「检查器 `requires_human_input` 且无草稿可审」的路径；有草稿可审时一律把问题放到卡上。

## 7. 条件 Critic（D）

把检查器分成两层。分层写在 `default_checker_suite` 旁，不拆掉现有 Checker 协议。

**L0 确定性（每篇草稿必跑，无 LLM）**：

- `validate_draft`（原创、非 idea）：无证据 / 越界主张 → `hard_fail`。
- 密钥扫描（`scan_secrets`）。
- 字数上限（沿用质量门；超限 → rewrite instruction，不调用模型）。
- idea 草稿继续跳过 EvidenceChecker（既有 idea 设计）。

**L1 LLM（默认不跑）**，触发任一即跑**一轮**（不是 8 路再乘 `max_rewrite_rounds` 的全量，除非触发）：

- L0 未通过；或
- 决策卡 `must_ask` 含 `safety_risk` / `position_conflict`；或
- 用户 `decide revise` / `decide accept` 之前（采用前补跑一次 L1，结果进决策卡 `risks`；已在本轮跑过且正文未变则跳过）；或
- `quality_gates.llm_critique_mode = "always"`（调试，默认 `"on_fail_or_gate"`）。

L1 仍用现有 8 检查器里依赖 runner 的部分；`aggregate_checks` 规则不变。`max_rewrite_rounds` 只在 L1 被触发时生效，默认改为 1（配置项，既有测试需同步）。

`make_critique_node` 保留在图里（不删节点）：它执行 L0，按需 L1，写入 `drafts`。这样 replay 仍有 `CRITIQUED` 边界，用户看不见。

## 8. 产品 CLI（E）

日常四条：

```bash
finch daily [--json]
finch draft "一个想法或片段" [--json]    # 实现 = 已批准的 finch idea
finch next [--json]
finch decide <id> --action accept|revise|skip [--instruction ...] [--json]
```

- `finch idea` 保留为 `draft` 的别名，行为与 [[2026-09-06-idea-lightweight-entry-design]] 相同。
- `draft` / `idea` 产出的 job 进入同一 `next` 队列（`provenance=idea`，`track=original`）。
- `run_id` 仍在 `--json` 里返回给编排层，人类默认输出不打印。
- 旧命令保留调试：`run resume` / `run resolve` / `jobs *` / `review *` / `engagement *`。

理想人类输出（非 JSON）只描述收件箱，不描述节点：

```text
今天 2 条待决定。

1. [原创] 综合测试比按标签拆分更能验证 evidence card cap
2. [回复] Agent reliability 不只是增加重试

finch next     # 看第 1 条
```

`$finch` skill 的循环改为：`daily --json` → 直到 `next` 为 `none` 为止 `next` → 渲染卡（含互动）→ `decide`。不再把用户带到 `engagement` 子命令。

## 9. 内部对象怎么放

| 对象 | 持久化 | 用户可见 | 本设计动作 |
| --- | --- | --- | --- |
| Commit / ExternalPost / idea 文本 | 是（既有） | 只作为 `source_refs` | 不变 |
| EngineeringEvent | 是（提取产物） | 否 | **降级**：不进 CLI / 决策卡 |
| EvidenceCard | 是 | 决策卡上可引用 claim + 链接 | **保留**（证据规则的载体） |
| ContentJob | 是 | 不直接；original 的 `InboxItem.id` | 保留为原创决策单元 |
| AuthorPosition | 挂在 Job 上 | 合并进草稿与采用 | 不再单独确认命令 |
| InteractionCandidate | 是 | 不直接；engagement 的 `InboxItem.id` | 保留；进收件箱 |
| Draft | 是 | 卡上的正文 | 不变 |
| DecisionRecord | 是 | 否 | 权威采用记录；互动 accept 另写候选 status，不混进该表 |
| PublicationIntent / Link | 是 | 学习步骤 | 沿用 autolink 设计 |
| InboxItem | 否 | 是 | 新投影 |

Checkpoint（对 replay 有意义、对用户无意义）仍按节点存。本设计不要求「只在四处存档」立刻改 Runtime；先做到用户只在「草稿好了 / 等你审」停下来。将来若要减节点，另开设计，验收标准是 replay 测试全绿。

## 10. 错误处理

- `next` 无项目：`{ "status": "none" }`，退出码 0。
- `decide` 找不到 id、skip 无 `--reason`、revise 无 `--instruction`：现有干净错误，退出码 1。
- 一条轨道失败：`run_dual_track` 仍 `partial_failure`；收件箱只展示成功轨道的项目。
- L1 critic / revise 子进程失败：不覆盖已有草稿，返回结构化 error JSON；编排层可重试或跳过该条。
- `position_incomplete` 不再造成 daily 整段停在 `NEEDS_INPUT`。daily 在有草稿时以 `review_required` 结束。

## 11. 测试

- `InboxItem` 投影：一条原创草稿、一条互动草稿、一条 idea 草稿都能被 `next` 按 §4 规则选中；`must_ask` 优先；无 must_ask 时 original 先于 engagement。
- `decide` 解析：job_id 走原创落地；候选 id 走 APPROVED/REJECTED；冲突 id（不应出现）以 ContentJob 为准并告警。
- `define_jobs`：N 个主题只 expand K 个；K 失败后递补一次；未展开主题不出现在 `ready_jobs`。
- `position_gate`：空 decision/tradeoff **成功**放行 + `must_ask`；不再 `needs_input`。旧测试 `test_position_gate_missing_position_needs_input` 改为断言放行。
- Critic：L0 失败才进 L1；L0 全过且非 gate 时 runner 不被调用；`always` 模式回归旧行为。
- CLI：`draft` 与 `idea` 同行为；人类输出不含 `run_id`；`--json` 含 `track`。
- 回归：证据链单测、互动 `weighted_total`、不自动发布、`promote_to_personal` 门禁、dual_track 故障隔离。

## 12. 分阶段落地

不要一份计划覆盖全部。建议四份实现计划，每份可独立合并：

| 阶段 | 内容 | 用户可感知结果 | 依赖 |
| --- | --- | --- | --- |
| **A** | 收件箱投影 + `next`/`decide` 纳入互动 | 一个列表审完原创和回复 | 无 |
| **B** | 先选后写 + 立场不完整不停图 | daily 更快、更少停住 | 无（可与 A 并行） |
| **C** | 条件 Critic | 默认少一轮或多轮 LLM | 无（可与 A/B 并行） |
| **D** | `draft` 别名 + idea 进 `next` + skill 循环改文案 | 日常四命令 | A（idea 已能 `review`，进 `next` 要 A） |

发布学习继续走 [[2026-09-06-author-publication-autolink-design]]，不阻塞 A–D。

## 13. 迁移

- 无强制 alembic。`InboxItem` 不落库。
- 可选：`QualityGates` 增加 `llm_critique_mode: Literal["on_fail_or_gate", "always"] = "on_fail_or_gate"`，`max_rewrite_rounds` 默认 1。旧配置缺省兼容。
- 不删表、不删旧命令。
- 文档：落地 A 时改 `CLAUDE.md` 互动段「审批队列」为「写入统一收件箱」；证据链句保留，并加一句「对象链是实现，不是用户流程」。

## 14. 与既有设计的关系

- [[2026-09-06-single-decision-point-design]]：本设计是第四层。保留 `DecisionRecord` / `decide accept` 原子语义。**修订**两处：互动进入同一 `next`；`position_incomplete` 不再停图。
- [[2026-09-06-author-position-confirmation-design]] / [[2026-09-06-needs-input-interactive-step-design]]：门面保留给调试；产品路径不再经过它们。
- [[2026-09-05-daily-work-budget-design]]：预算仍在收集 / 规划之前。本设计在预算之后再加「只展开 Top K」。
- [[2026-09-06-idea-lightweight-entry-design]]：服务层不变。产品名增加 `draft`；产出进入 §4 收件箱。
- [[2026-09-06-author-publication-autolink-design]]：即第 5 步「发布并学习」的实现，本文件不重复。
- `CLAUDE.md` 双轨：收集与评分继续两套；**产品面不再是两套审核。**
