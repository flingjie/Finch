# Learning Loop：把学习过程变成交流的起点

日期：2026-10-05
状态：已确认（待写实现计划）

## 1. 背景与问题

Finch 的北极星是「每周新增/加深多少条可接续的同行关系」，不是推荐人数。当前四个缺口让「学习」
与「交流」彼此割裂：

1. **发现端不看「当前问题」**：`interests.current_questions` 已经作为 `user_context` 流进机会评估，
   但它只是 finch.yaml 里的静态列表——没有「最多 3 个」的生命周期，也没有「这条内容能否**解释 /
   挑战 / 验证**某个活跃问题」的判定门。热度高但泛泛的趋势帖，和能推进当前问题的失败案例，被同等对待。
2. **idea 不从实践里找素材**：`idea-discovery` 四种来源（commit / fragment / conversation / signals）
   没有一条吃「我自己的失败与修复 / 前后比较 / 被推翻的判断 / 待请教的问题」。`PracticeItem` 是「已确认、
   可引用」的证据，`PracticeSession` 是表达训练，都不是「问题 / 尝试 / 观察 / 未知」这类**原始素材**。
3. **草稿不呈现「判断怎么变」**：writer 上下文只渲染 `observation / claim / decision / tradeoff`，
   不渲染 `facts / interpretation / evidence_status / boundaries / position_revisions`——而「区分实际观察
   与待验证猜想、避免把读到的方法写成已完成的实践」恰恰要靠这几列。
4. **互动不回接学习任务**：`reply-crafting` / `conversation-follow-up` 已有 `NextAction(ask/offer/try/observe)`
   雏形，但回复只表达观点，没有「补案例 / 提具体疑问 / 报告验证结果 / 整理成可复用笔记」的闭环，
   也没有追踪「反馈是否改变判断 / 下一次验证是否完成 / 是否再次与同一个人交流」。

核心洞察（Learning in Public）：**把学习过程变成交流的起点**。每次发现内容，都能接到正在研究的问题，
再通过分享和反馈推动下一次实践。

## 2. 目标与非目标

**目标（Phase 1，本轮实施）**

- 建立「活跃问题」一级实体 `ActiveProblem`：最多 3 个 open、有生命周期，作为发现 / idea / 互动三处的脊柱。
- 建立「实践尝试」原始素材实体 `PracticeAttempt`：问题 / 尝试 / 观察 / 未知 / 下一步 / 结果，充当
  practice → idea 的桥，不混入「已确认证据」的 `PracticeItem`。
- `idea-discovery` 新增 `attempt` 来源：把 `PracticeAttempt` 提炼为待审核的 `ContentJob`，证据分列。
- 草稿新增 `JUDGMENT_SHIFT` 内容类型 + writer 上下文补渲染，呈现「判断怎么变」骨架，区分观察与猜想。
- `finch weekly` 读三个验收信号（见 §9），使四周试点「每周两条真实学习输出」有据可查。

**目标（Phase 2，本轮只写 spec、不动工）**

- 发现端「优先推进当前问题」判定门（§7.1）。
- 互动接回学习任务的 skill 指引与回链（§7.2）。

**非目标（YAGNI）**

- 不新增 `learning-loop` 编排服务自动串 attempt→idea→draft→互动。闭环由用户驱动，Finch 只提供
  实体 + 回链 + 只读投影，维持「Skill + 确定性 domain service，不是 LLM agent loop」的不变量。
- 不自动 confirm 任何 `PracticeAttempt` 为 `PracticeItem`；不自动发布任何内容（`gh`/`opencli` 只读不变）。
- 不改 50 人分层、不改 `opportunity_assess_limit`、不新增外发能力。
- Phase 2 两块不在本轮实施计划内；发现端判定门是「强化 fit 的信号」，不是新硬门（沿用热度≠推荐门的既有原则）。

## 3. 数据模型

### 3.1 `ActiveProblem`（新 `src/finch/problems/`）

```python
class ActiveProblem(BaseModel):
    id: str                    # problem_<sha8>（title 内容寻址，幂等）
    title: str                 # 一句话问题
    why_it_matters: str = ""   # 为什么值得追
    status: Literal["open", "closed"] = "open"
    attempt_ids: list[str] = []  # append-only 回链 PracticeAttempt
    closed_reason: str = ""
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
```

**不变式**：`open` 数量 ≤ 3，在 `ProblemService.add` 强制（超 3 抛错，提示先 close 一个）。
`close` 时置 `status=closed` + `closed_reason` + `closed_at`；`attempt_ids` 只在 `attempts add
--problem-id` 时追加，不回退。

### 3.2 `PracticeAttempt`（新 `src/finch/practice/attempts.py`）

模块名用 `attempts.py` 复用 `practice/` 域，但**独立于**表达训练的 `PracticeSession` 与
证据库 `PracticeItem`。CLI 用 `finch attempts`（概念上是「实践尝试」，字面上与 `finch practice`
= 表达训练分开）。

```python
class PracticeAttempt(BaseModel):
    id: str                    # attempt_<sha8>（problem+attempt+observation 内容寻址，幂等）
    problem_id: str | None = None   # 回链 ActiveProblem（可选：素材可能还没归到某个问题）
    problem: str               # 当前问题（一句话）
    attempt: str               # 尝试了什么
    observation: str           # 实际观察到了什么
    unknown: str = ""          # 未知 / 卡点，适合请教同行
    next_step: str = ""        # 下一步准备验证
    result: str = ""           # 有结果时回填（≠ 已证明普遍成立）
    status: Literal["open", "verified", "closed"] = "open"
    source_refs: list[str] = []  # 溯源，可选
    created_at: datetime
    updated_at: datetime
```

核心四字段「问题 / 尝试 / 观察 / 未知」对应提案原文；「下一步」作前向链接，「结果」在 `verify` 时回填。
`observation` 是你的实际观察（可 `observed`）；`unknown` / `next_step` 明确是待验证（`unverified`）——
这条证据边界是 §5 提炼时「不把读到的方法写成亲历」的依据。

### 3.3 对既有模型的最小扩展（不新造表）

- `content/jobs.py::SourceKind` += `"attempt"`。
- `ideas/models.py::SourceRef.type`（Literal） += `"attempt"`。
- `content/jobs.py::ContentJob` += `attempt_id: str | None = None`（干净回链，供 weekly 遍历）、
  `problem_id: str | None = None`（可选，供按问题聚合）。

## 4. CLI

### 4.1 `finch problems`

| 命令 | 行为 |
|---|---|
| `add --title "…" [--why "…"]` | 新建；open 数 ≥ 3 时抛错（提示先 `close` 一个）。幂等（title 内容寻址）。 |
| `list [--status open|closed|all]` | 列出，默认 open。 |
| `show <id>` | 显示单条 + 其 attempt_ids。 |
| `close <id> [--reason "…"]` | 置 closed + closed_reason + closed_at。 |

### 4.2 `finch attempts`

| 命令 | 行为 |
|---|---|
| `add --problem-id <id> --problem "…" --attempt "…" --observation "…" [--unknown "…"] [--next-step "…"] [--ref URL]...` | 新建；`--problem-id` 可选，给了就追加到对应 ActiveProblem.attempt_ids。幂等（problem+attempt+observation 内容寻址）。 |
| `list [--status open|verified|closed|all]` | 列出，默认 open。 |
| `show <id>` | 显示单条。 |
| `verify <id> --result "…"` | open → verified，回填 result。 |
| `close <id>` | 置 closed（弃置一条素材）。 |

`verify` 是唯一把 `open` 改为 `verified` 的路径，代码不得把未验证标为已验证（对齐
`Artifact.execution_status` 的既有纪律）。

## 5. idea-discovery 新增 `attempt` 来源

沿用现有四种来源的模式，加第五种：

- CLI：`finch ideas create --attempt <id>`，读一条 `PracticeAttempt` → 产出 `IdeaCandidate`，
  `origin=practice`、`source_kind=attempt`，`attempt_id` 落进 `ContentJob`。
- 新增 `skills/idea-discovery/references/attempt-signals.md`：判据是「有没有**真实观察** +
  一个**非显然的未知**」；机械操作 / 纯情绪 / 无观察 → 空（对齐 `commit-signals.md` 的判据风格）。

**证据映射（关键，避免把读到的方法写成亲历）**

| PracticeAttempt | IdeaCandidate |
|---|---|
| `observation` | `facts`（`evidence_status=observed`） |
| `attempt` | 上下文事实（`facts` / `source_refs`） |
| `unknown` + `next_step` | `boundaries.unknown` |
| 你的判断 | `interpretation`（与 `facts` 分列） |

**「判断怎么变」的 prior 从哪来**：`PracticeAttempt` 不设显式 `prior_belief` 字段（提案四字段不含它）。
「原先以为」来自两处之一，由 idea-discovery 检测到「prior 假设 vs 实际观察」对照时置 `content_type=JUDGMENT_SHIFT`：

1. **新建路径**：prior 隐含在 attempt 的 `problem` / `attempt` 叙述里（如「我以为重试能解决，但……」），
   idea-discovery 提炼时把 prior 写进 `interpretation`，observation 进 `facts`。
2. **修订路径**：对已存在的 `ContentJob` 执行 `revise_position`（如因同行反馈改变判断），
   prior = 上一个 `author_position.claim` / `PositionRevision`，实际 = 新 claim + `observation`。
   此路径复用既有 `position_revisions`（append-only），不需要 PracticeAttempt。

两条路径都收敛到：`JUDGMENT_SHIFT` = 一个「prior 判断 → 观察 → 修订后的适用边界 → 下一步验证」的故事，
writer 读 `position_revisions` + `facts` + `boundaries` 渲染（见 §6）。

## 6. 草稿呈现「判断怎么变」

### 6.1 新内容类型

- `content/models.py::ContentType` += `JUDGMENT_SHIFT = "judgment_shift"`。
- `content/checkers/suites.py::checker_suite_for` 的 `type_specific` 加
  `ContentType.JUDGMENT_SHIFT: [StructureChecker(runner)]`（沿用现有检查器，不新造）。
- `content_type_for` 显式字段优先，故 idea 侧设了 `content_type` 即生效，无需改推断逻辑。

### 6.2 writer 上下文补渲染

`content/writer.py::_render_job_context` 现在只渲染 `observation / claim / decision / tradeoff`，
**不渲染** `facts / interpretation / evidence_status / boundaries(known/inferred/unknown) /
position_revisions`。补上这几列（对所有内容类型都受益，不只判断转变）：
`facts`、`interpretation`、`evidence_status`、`boundaries.known/inferred/unknown`、
`position_revisions`（按序，含 `change_reason` / `counterexample` / `scope`）。

### 6.3 骨架指令

`prompts/draft-from-job.md` 加「判断怎么变」骨架指引：引导 writer 优先找「原以为… / 实际尝试后发现… /
目前这个判断适用于… / 下一步准备验证…」，但**骨架是引导不是强制四段**（提案原文「成稿不必固定成四段」）。
`JUDGMENT_SHIFT` 类型要求保留真实细节、区分实际观察与待验证猜想。

三层兜底：`evidence_status` 分列 → writer 上下文显式呈现 → Critic 现有检查器（Specificity/Safety）继续把关。

## 7. Phase 2（本轮只写 spec、不动工）

### 7.1 发现端「优先推进当前问题」

- 新 `render_active_problems()`（镜像 `render_user_practices`，`profile/render.py`），把 ≤3 个 open 的
  `ActiveProblem` 渲染成 prompt 文本块。
- `opportunities/assess.py::assess_opportunity` 加 `active_problems: str = ""`；`discover.py` / `daily.py` 传入。
- `prompts/opportunity.md` 加「## User active problems」一节 + 第 4 条信号 **problem-advance**
  （这条内容能解释 / 挑战 / 验证某个活跃问题 → fit 更强，优于泛泛主题相关）。
- `opportunities/models.py::Fit` 加 `problem_refs: list[str]`（结构化回链，像 `practice_refs` 一样可追溯）。

### 7.2 互动接回学习任务

主要靠 skill 指引 + 接线现有 CLI，几乎不需新领域模型：

| 发生了什么 | Finch 建议 | 落点 |
|---|---|---|
| 同行给了一个方法 | 生成最小验证行动 | `conversation-follow-up` 读到方法/`possible_experiments` 时建议 `finch attempts add`（`NextAction.type=try` 已覆盖） |
| 出现不同判断 | 找双方分歧的前提 | `reply-crafting` 围绕 `ConversationThread.disagreements` 出澄清问题（`type=ask` 已覆盖） |
| 你完成了验证 | 起草回访回复补结果 | `finch attempts verify` → `reply-crafting` 用 `result` 起草回访；`connections follow-up` 登记再次联系 |
| 同一问题反复出现 | 整理成可复用笔记/工具 | 多个 attempt/conversation 共享同一 `problem_id` → 走 idea-discovery `signals` 或 weekly 汇总 |

唯一可选新回链：`PracticeAttempt ↔ ConversationThread`（记「这个尝试源于哪次对话」）。

## 8. 验收追踪（finch weekly）

三个验收信号都有**确定性事件源**，只加薄只读投影在 `WeeklyReflectionService`（`src/finch/learn/`）里呈现，
不建新 metrics：

1. **因反馈改变判断** → `IdeaService.revise_position` 追加 `PositionRevision` 且 `change_reason`
   引用了对话/同行；按 `ContentJob.problem_id` 聚合。
2. **完成下一次验证** → `PracticeAttempt.status: open → verified`（`finch attempts verify`）。
3. **再次与同一人交流** → `connections` 域已有 `InteractionRecord` + follow-up，同 `person_id` 两次互动直接可数。

`WeeklyReflectionService` 扩一段：本周新增/验证了几个 attempt、几次「反馈改变判断」、几次同人再交流。
「每周两条真实学习输出」由此可查。

## 9. 验收（Phase 1 完成标准）

1. `finch problems add` 建 3 个后第 4 个被拒；`finch attempts add --problem-id` 正确回链 `attempt_ids`。
2. `finch ideas create --attempt <id>` 产出的 `ContentJob` 带 `source_kind=attempt`、`attempt_id`、
   `evidence_status=observed`（observation 进 `facts`，unknown/next_step 进 `boundaries.unknown`）。
3. `JUDGMENT_SHIFT` 类型走 `checker_suite_for` 命中 `StructureChecker`；writer 上下文含
   `facts / boundaries / position_revisions`。
4. `finch weekly` 呈现「反馈改变判断 / 下一次验证 / 同人再交流」三信号。
5. `uv run pytest` / `uv run ruff check .` / `uv run mypy src` 全绿。

## 10. 后续（不在本 spec）

- Phase 2 两块（§7.1 发现端判定门、§7.2 互动闭环 skill 指引）各自单独成实现计划。
- 四周试点后，据 §8 三信号回看「活跃问题」是否真被推进，决定是否保留/收紧「最多 3 个」的约束。
