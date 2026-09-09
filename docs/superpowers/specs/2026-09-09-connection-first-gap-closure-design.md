# 连接优先补差设计（Connection-First Gap Closure）

日期：2026-09-09
状态：待评审

## 背景与结论

Finch 的核心闭环已完成从「内容优先」到「连接优先」的倒置（见 `docs/product-contract.md` 与
`38377bd Merge refactor/connection-first`）。对照目标愿景逐字段核对后，剩余差距分为四类：

1. **功能差距**（愿景有、实现无）：`signals-to-idea`（社区信号聚合）缺失；每日首页缺
   「今日聚焦」top-N 排序截断。
2. **字段对齐**：`Idea.origin` 枚举命名、`PeerProfile`/`InteractionRecord` 若干字段形态。
3. **清理**：一批内容优先时代遗留的过时 docstring、一处死引用、一个死枚举值。
4. **命名差异**（明确不做，见「非目标」）。

本设计覆盖前三类。

## 范围

**In：**

- 设计 1：`finch connect daily` 今日聚焦投影（确定性排序 + top-N）。
- 设计 2：`signals-to-idea`——扩展 `idea-discovery` 加第 4 个来源 `signals`，新命令
  `finch ideas signals`，产出 `origin="synthesis"`。
- 设计 3：字段对齐——`Idea.origin` 枚举改为三值 `practice/conversation/synthesis`；
  `PeerProfile` 加 `possible_next_actions`；`InteractionRecord` 加 `follow_up_at`。
- 设计 4：清理过时 docstring / 死引用 / 死枚举。

**Out（非目标）：**

- 不重命名 `ContentJob` → `AuthorIdea`（纯 churn，另行决策）。
- 不重命名 Skill（`discover-peers` 等）。
- 不恢复 Graph Runtime、不改存储层、不新增通用抽象。

---

## 设计 1：今日聚焦投影（today-focus）

### 现状

`src/finch/cli.py` 的 `_render_daily` 分四段输出，但全量、无排序、无截断：

```text
## 需要继续的对话
## 今天最值得连接的同行
## 可贡献的具体内容
## 从近期交流产生的观点候选
```

### 目标

`finch connect daily` 的非 JSON 输出改为「今日聚焦」，四段各按确定性键排序 + top-N：

| 段落 | 排序键（确定性） | top-N |
|---|---|---|
| 需要继续的对话 | 超期天数降序 → `open_questions` 数降序 | 2 |
| 今天最值得连接的同行 | `peer_value`（`result.peers` 已有排序）降序 | 3 |
| 可贡献的具体内容 | `candidate.score.total` 降序 | 3 |
| 从交流形成的观点候选 | 立场完整度（有 decision+tradeoff 优先）→ 最近创建 | 1 |

### 设计要点

- 排序与截断是**纯 Python 确定性逻辑**，不调用 LLM。
- 全量仍可通过 `--json` 或 `finch peers list` / `finch conversations list` /
  `finch review list` 等命令查看，今日聚焦不丢信息，只做「先看什么」的聚焦。
- 新增纯函数 `src/finch/projections.py::build_today_focus(...)`，接收各投影源列表，
  返回 `dict[str, list]`（四段，已排序 + 截断）。`_render_daily` 改为消费该函数，
  便于单测。
- 截断时各段补一行省略提示（如 `… 还有 5 个对话`），避免误以为只剩 top-N。

---

## 设计 2：signals-to-idea（社区信号聚合）

### 方案

采用**方案 A：扩展 `idea-discovery` 加第 4 个来源 `signals`**，复用现有
`IdeaCandidate` 契约与证据策略，不新建 Skill。

### 输入（全部只读，确定性汇总后交给 LLM 做单一语义判断）

- `PeerProfile.shared_topics` / `current_interests`：同行反复讨论的主题。
- `ConversationThread.open_questions` / `disagreements`：未解问题与分歧。
- 最近 commit 证据（个人实践侧，复用 `commit_service` 已有的证据采集）。
- 可选：一次只读 `finch twitter search`（新论文 / 工具信号），失败不影响结果。

### 产出

- **一个** `IdeaCandidate`（`origin="synthesis"`），或空列表（无「读者值得知道的真实
  决策 / 分歧」时）。
- 判断与现有三种来源一致：有真实决策/分歧 → 产出；新闻/纯情绪/机械变化 → 空。

### 实现路径

1. `src/finch/ideas/fragment_service.py` 新增 `FragmentService.from_signals(...)`，
   `origin="synthesis"`，`source_refs` 引用参与聚合的 peer/thread/commit。
2. 新增 `skills/idea-discovery/references/signals-signals.md`：社区信号 → Idea 的判据
   （什么算「反复出现的问题」「未解决的分歧」，与新闻/情绪的区分）。
3. `src/finch/cli.py` 的 `ideas_app` 新增独立命令 `finch ideas signals`（与
   `ideas commit` 平行的独立来源命令；signals 聚合多个信号源，不适合塞进
   `ideas create` 的「恰好一个 --text/--conversation」约束）。
4. `skills/idea-discovery/SKILL.md` 增补第 4 个来源与命令说明。

### 边界（复用现有铁律）

- 外部信号 ≠ 个人证据：`origin="synthesis"` 的候选 `author_position.status` 一律
  `proposed`，不得写成亲历（见 `_shared/evidence-policy.md`）。
- 不生成草稿（→ `idea-to-draft` / `expression-practice`）。
- 只读，不自动发布。

---

## 设计 3：字段对齐

### 3.1 `Idea.origin` 枚举（三值）

将 `Literal["commit", "search", "user", "conversation"]` 改为
`Literal["practice", "conversation", "synthesis"]`：

| 旧值 | 新值 | 说明 |
|---|---|---|
| `commit` | `practice` | 个人实践（git commit 证据） |
| `user` | `practice` | 个人实践（用户零散片段） |
| `conversation` | `conversation` | 同行交流（不变） |
| `search` | （删除） | 死值，从无生产路径 |
| — | `synthesis` | 新增，`signals-to-idea` 产出 |

**触碰点：**

- `src/finch/content/jobs.py:61` `ContentJob.origin`
- `src/finch/ideas/models.py:49` `IdeaCandidate.origin`
- `src/finch/ideas/fragment_service.py`（`_to_candidate` 的 `origin` 参数类型、
  `from_text` → `practice`、`from_conversation`/`from_thread` → `conversation`）
- `src/finch/ideas/commit_service.py`（`origin="commit"` → `"practice"`）
- `src/finch/cli.py` 的 `origin` 展示（`_render_idea_list` / `_render_idea_detail` /
  `ideas_create` JSON）

**迁移：** `origin` 只作溯源标签，被 CLI 展示，不驱动门禁逻辑。旧工作区中已存的
`commit`/`user`/`search` 值在读取时需兼容：读取侧接受 legacy 值并归一化为新值
（一次性，或在 `ContentJob` 加载时做 alias 映射）。工作区数据是本地 scratch 数据，
优先级低，但必须避免读取时 Pydantic 校验失败导致命令崩溃。

### 3.2 `PeerProfile`

- 新增 `possible_next_actions: list[str] = Field(default_factory=list)`。
- 保留 `next_context: str`（自由文本上下文）。
- `interaction_history` **不冗余存储**，通过 `ConversationThread.interaction_ids` /
  `InteractionRecord.peer_id` join 派生；由 `finch peers show` 在展示层聚合。

### 3.3 `InteractionRecord`

- 新增 `follow_up_at: datetime | None = None`（时间戳，何时该跟进）。
- 保留 `follow_up_status: str`（none/pending/replied/closed），二者互补：前者是
  调度时间，后者是跟进状态机。

---

## 设计 4：清理（删除低价值/死代码）

代码库整体很紧（无遗留 `Opportunity`、`dual_track`、Graph Runtime、builderDNA、TODO）。
清理项是内容优先时代遗留的过时痕迹：

1. `src/finch/__init__.py:1` — docstring `"evidence-driven builder companion"` →
   改为连接优先定位（`peer-connection and personal-expression system`）。
2. `src/finch/content/critic.py:4` — docstring `"供 graph 的 write 节点"` →
   去掉 Graph Runtime 引用。
3. `skills/expression-practice/SKILL.md:16` — 删除死引用「或关联一个 Opportunity」。
4. `prompts/critique-draft.md:15` — `"the builder's voice"` → `"your voice"`。
5. `IdeaCandidate.origin` / `ContentJob.origin` 中的 `"search"` 死值 → 随 3.1 删除。

---

## 数据流与组件（触碰文件）

| 文件 | 改动 |
|---|---|
| `src/finch/projections.py` | 新增 `build_today_focus`（设计 1） |
| `src/finch/cli.py` | `_render_daily`/`connect_daily` 用今日聚焦；`ideas create`/新 `signals` 命令；`origin` 展示（设计 1/2/3） |
| `src/finch/ideas/fragment_service.py` | 新增 `from_signals`；`origin` 改名（设计 2/3） |
| `src/finch/ideas/commit_service.py` | `origin="practice"`（设计 3） |
| `src/finch/ideas/models.py` | `origin` 枚举三值（设计 3） |
| `src/finch/content/jobs.py` | `ContentJob.origin` 枚举三值 + legacy 兼容（设计 3） |
| `src/finch/peers/models.py` | 加 `possible_next_actions`（设计 3） |
| `src/finch/engagement/models.py` | `InteractionRecord.follow_up_at`（设计 3） |
| `src/finch/__init__.py`、`src/finch/content/critic.py` | docstring 清理（设计 4） |
| `skills/idea-discovery/SKILL.md` + `references/signals-signals.md` | 第 4 来源（设计 2） |
| `skills/expression-practice/SKILL.md`、`prompts/critique-draft.md` | 清理（设计 4） |

## 错误处理与边界

- `signals` 来源：任一信号源（如 twitter search）失败不取消其余；无信号或无效信号 →
  空列表，不报错。
- `origin` legacy 兼容：读取旧值不抛校验错误，归一化为新值。
- 今日聚焦：任一投影源为空时该段输出 `(none)`，其余段照常。

## 测试策略

- 单元测试 `build_today_focus`：四段排序键、top-N 截断、空列表、省略提示。
- 单元测试 `FragmentService.from_signals`：有信号 → 一个 `IdeaCandidate`
  （origin=synthesis、position 一律 proposed）；无信号 → 空；单一信号源失败不崩。
- 单元测试 `origin` 枚举迁移：旧值 `commit`/`user`/`search` 归一化读取。
- 回归：现有 `finch connect daily` / `finch ideas` / `finch peers` 测试不破。
- 全量 `uv run pytest` + `uv run ruff check .` + `uv run mypy src`。

## 非目标（YAGNI）

- 不把 `signals-to-idea` 做成独立 Skill。
- 不重命名 `ContentJob`、Skill、CLI 命令名。
- 不把「今日聚焦」做成 LLM 排序（确定性排序即可，避免引入不稳定性）。
- 不新增跨来源去重之外的复杂幂等（复用 `IdeaService.create_candidate` 的
  `sha256(core_point)` 幂等）。
