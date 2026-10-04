# 表达方法库：选中 → 练一次 → 留下反馈

日期：2026-10-03
状态：已确认（实现计划见 docs/superpowers/plans/2026-10-03-expression-methods-library.md）
对照：`2026-10-02-article-analysis-design.md`（报告即算即打印 → 本设计改为默认落库）；
`2026-10-01-practice-profile-design.md`（真实实践清单，与本方法库分离）；
`2026-09-08-writing-style-analysis-design.md`（观察→实验→Voice，方法库从未落地）

## 1. 背景与问题

`article_analysis` 已能产出可借鉴方法（`method / why_effective_here / when_to_use / mini_exercise`）
与风格观察（嵌套 `StyleBlock`）。价值在于让用户下一次写得更好；保存很多完整报告，
未必形成这个变化。

当前缺口：

- 报告默认不落库（`D3`：即算即打印），无法可靠引用「第 N 个方法」。
- 没有「只保存选中方法」的文件方法库；相似方法无法合并多来源。
- `expression-practice` 不挂方法；练完没有「是否值得再用」的结构化反馈。
- `practice-profile.yaml` 记录「我做过什么」（连接/贡献用），不宜混入「我正在学怎样表达」。
- 风格观察若直接写成 Voice 规则，容易把单篇任务写法当成长期偏好。

## 2. 决策记录

| # | 决策点 | 结论 |
|---|--------|------|
| D1 | 报告持久化 | **每次** `article analyze` 默认落库；`--no-save` 可跳过 |
| D2 | 保存粒度 | 完整报告按需回看；**方法库只含用户选中的方法**；风格先留在报告内 |
| D3 | 领域边界 | 独立包 `expression_methods`；不进 `practice-profile` / 不自动写 VoiceProfile |
| D4 | 合并 | 保存时 LLM 给 0–3 个同义候选；裸 save 只打印候选不落库；须 `--merge` 或 `--as-new` 才写 |
| D5 | 练习接入 | 沿用现有 `expression-practice` 诊断；方法卡仅作 context；`finish` 记三选一 |
| D6 | 反馈形状 | `worth_reuse` / `practice_again` / `not_for_me` + 可选一句原话 |
| D7 | 推荐 | MVP **不做**默认按任务自动推荐；**显式**方法辅助 idea-discovery 见 `2026-10-04-methods-idea-discovery` |
| D8 | 非目标 | 无自动评分、向量库、掌握等级、独立学习 Agent |

## 3. 目标与非目标

**目标**

- 分析报告可回看（带 id），便于按序号选中方法。
- 建立很小的表达方法库：可迁移动作 + 来源 + 适用边界 + 自己的练习记录。
- 闭环：选中方法 → 用自己的素材练一段 → 留下三选一反馈。
- 相似方法可合并，保留多个来源，避免同义膨胀。

**非目标（YAGNI）**

- 不默认把完整报告或方法库注入每次 `drafts` / `connect` / 写作。
- 不从单篇风格观察自动更新 VoiceProfile；风格须经看懂作用 → 亲自试用 → 确认偏好。
- 不做按任务的自动方法推荐（后续扩展）。
- 不改 `practice-profile` 语义；不建独立问题库或学习 Agent。
- 不引入向量检索、掌握等级、自动打分。

## 4. 架构

四层职责互不越界：

| 层 | 职责 | 不负责 |
|---|---|---|
| `article` | 分析 + 默认落库 `ArticleReport` | 不写方法库、不改 Voice |
| `expression_methods` | 选中、合并、列表、挂练习反馈 | 不分析文章、不代写 |
| `practice` | 现有练习会话；可选 `method_id`；`finish` 记 verdict | 不拥有方法库 |
| `voice` | 仅用户确认的长期偏好 | 不从报告/方法自动更新 |

模块布局（实现时）：

```text
src/finch/article/          # 既有；analyze 默认 persist；新增 show
src/finch/expression_methods/
  models.py                 # ExpressionMethod + MethodSource + MethodPracticeLog
  service.py                # save / merge suggest / append log
  repository.py             # Workspace collection: expression_methods
src/finch/practice/         # PracticeSession 小扩展
```

CLI：`article analyze|show`；新 `methods` typer 子应用（领域包名仍为 `expression_methods`）；
`practice start/finish` 增参。

## 5. 数据模型

### 5.1 ArticleReport（行为变更）

- Workspace collection：`article_reports`（`var/…` 下按 id 原子写 YAML）。
- `analyze` 默认 `upsert`；`--no-save` 跳过写入。
- id 规则与现有 `ArticleReport.id` / `content_hash` 对齐：同内容覆盖同一报告（幂等）。
- `StyleBlock` 仍嵌在报告内，不单独落成「风格规则」实体。

### 5.2 ExpressionMethod（新）

保存的是可迁移的**动作**，不是「开头有冲突感」这类标签。

从报告 `transferable_methods[index-1]` 抄入时的字段映射（MVP **不另跑 LLM 扩写**）：

| ExpressionMethod | TransferableMethod |
|---|---|
| title | method |
| why_effective | why_effective_here |
| when_to_use | when_to_use |
| mini_exercise | mini_exercise |
| boundaries | `""`（空；后续若人工编辑另开能力） |

```text
id: str
title: str                         # 可迁移动作名
why_effective: str                 # 对读者为何有效
when_to_use: str                   # 适用场景
boundaries: str = ""               # 不适用边界（MVP 默认空）
mini_exercise: str = ""
sources: list[MethodSource]        # 可多个来源
practice_logs: list[MethodPracticeLog]
created_at / updated_at
```

```text
MethodSource:
  report_id: str
  method_index: int                # 1-based，对应 transferable_methods
  excerpt: str = ""                # 可选短摘录
  source_ref: str | None           # 报告的 source_ref（url/file 等）

MethodPracticeLog:
  session_id: str
  verdict: worth_reuse | practice_again | not_for_me
  note: str = ""
  at: datetime
```

与 `practice-profile.yaml`、`voice-profile.yaml` **无共享写入路径**。

### 5.3 PracticeSession（小扩展）

```text
method_id: str | None = None
method_verdict: Literal["worth_reuse","practice_again","not_for_me"] | None = None
method_verdict_note: str = ""
```

有 `method_id` 时，`finish` **要求** `--verdict`；无 `method_id` 时行为与今日完全一致。

## 6. 命令流与闭环

```text
article analyze → 报告落库并打印（含 id 与方法序号）
       ↓
methods save --report <id> --index N
       ↓  （合并候选；确认 merge 或 create）
方法入库
       ↓
practice start --method <id> --attempt "..."
       ↓  diagnose / save（规则不变；方法卡进 context）
practice finish --verdict … [--note]
       ↓
practice_logs 追加；session 记下 method_verdict
```

| 命令 | 作用 |
|---|---|
| `finch article analyze …` | 默认落库；`--no-save`；打印 report id 与方法 1..k |
| `finch article show <report-id>` | 回看已存报告 |
| `finch methods save --report <id> --index <n>` | 保存；先出合并建议 |
| `finch methods save … --as-new` | 强制新建 |
| `finch methods save … --merge <method-id>` | 直接并入 |
| `finch methods list` / `show <id>` | 浏览库与练习记录 |
| `finch practice start --method <id> --attempt "…"` | idea 仍可选；方法卡写入 diagnose context |
| `finch practice finish … --verdict … [--note]` | 有 method 时强制 verdict |

**Skill**：`article_analysis` 报告末尾提示可 `methods save`；`expression-practice` 支持「用方法 X 练一段」并映射 CLI。不新增学习 Agent。

## 7. 合并建议

1. 读取库内现有方法（title + when_to_use + boundaries）。
2. 一次 LLM 结构化调用：输入待保存方法 + 现有列表 → 0–3 个 `{method_id, reason}`（可空列表）。
3. MVP **不做** `typer.confirm` 交互：裸 `methods save --report … --index …` 只打印候选并以非零退出码表示「待确认」；**必须**再带 `--merge <id>` 或 `--as-new` 才写入。
4. 合并 = 保留目标 `ExpressionMethod`，追加 `sources[]`；不删除历史来源；标题等字段默认保留目标侧（MVP 不自动改写文案）。
5. 同一 `(report_id, method_index)` 已存在于某方法的 `sources` 时：幂等跳过，不重复追加。

## 8. 练习与反馈

- 诊断沿用现有维度（理解 / 判断 / 因果 / 具体 / 像自己 / 清晰等）。
- 方法卡仅作为 context，**不**把「有没有用上技巧」当作成功标准。
- `finish` 三选一优先表达「是否值得再用」，而非技巧打卡。
- 风格 → Voice：本设计不建自动通道；用户多次观察并试用后，仍走现有 `finch voice` 确认流程。

## 9. 错误与边界

- `--index` 越界 → 明确失败。
- 报告不存在（或当时 `--no-save`）→ `methods save` 失败，提示重新 analyze。
- 合并目标 id 不存在 → 失败。
- 有 `method_id` 缺 `--verdict` → `finish` 失败。
- 不写 VoiceProfile / practice-profile；不注入 drafts/connect 默认流水线。

## 10. 测试与验收

**自动化**

1. `analyze` 默认写入；`--no-save` 不写；同 `content_hash` 幂等覆盖。
2. `methods save --index N` 字段与 `sources` 正确；越界失败。
3. `--as-new` 新建；`--merge` 只追加 sources。
4. 合并建议 mock：无 `--merge`/`--as-new` 时不落库。
5. `practice start --method` 带 `method_id`；diagnose context 含方法卡。
6. 有 method 时 `finish` 缺 verdict 失败；有 verdict 时 logs 与 session 一致。
7. 无 method 的旧 practice 流程不变。
8. save method 不触碰 `voice-profile.yaml` / `practice-profile.yaml`。

**手工一条龙**

分析 → 保存方法（新建）→ 相似文再保存（合并）→ `practice` 练一次 → `finish --verdict worth_reuse` → `methods show` 可见记录；Voice / practice-profile 未改。

**成功标准**：至少一条选中方法被练过一次并留下三选一反馈，且可在 `methods list/show` 找回——不是「报告很多」。

## 11. 后续扩展（本 spec 不做）

- 按「目的、读者、当前卡点」推荐 1–2 个方法并说明为何适用。
- 风格观察多次试用后的 Voice 候选提案（仍须人工确认）。
- 完整报告的检索/标签；方法掌握等级。

## 12. 与现有契约的关系

- 更新产品叙述：`article_analysis` 从「只读不落库」变为「默认落库报告；方法库另存选中项」。
- 独立训练工具集合不变：仍不进入 connect/drafts 默认流水线。
- Evidence First / No auto-publish / Confirmed practices only / Deterministic totals 均不受影响。
