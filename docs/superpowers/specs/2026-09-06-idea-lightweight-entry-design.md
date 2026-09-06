# `finch idea`：用户主动提交想法的轻量入口设计文档

> 状态：已获用户批准（2026-09-06）。
> 目标：新增一个独立命令 `finch idea <想法/片段>`，判断「能不能发」，能发时产出一篇进入现有
> `Review` 的样稿。不新增 Graph、不新增数据库表、不做复杂评分系统；复用现有
> `ContentJob → Draft → Critic → Review` 基础设施。

## 0. 结论与原则

- **`idea` 只做两件事：判断 + 写样稿。** 适合的 idea 内部转换成现有对象（`ContentJob` / `Draft`），
  进入现有 Critic 与 Review；不适合则只返回一个最主要原因（`reason_code` + `reason`）。
- **两次结构化 LLM 调用**：第一次「评估」（判断 + 提取 job 语境），第二次「写样稿」（把用户原文
  扩写成完整正文）。样稿再走 Critic，失败按 `max_rewrite_rounds` 做 1–2 轮定向重写。
- **用户主动输入 = 立场已确认。** idea 来自用户本人，`author_position.confirmed=True`、
  `position_source=HUMAN_CONFIRMED`，无需再次询问相同立场。但这**不等于发布批准**——样稿仍进入
  现有 `review approve / revise / skip`（或 `decide`）。
- **允许「个人判断/假设」：idea 草稿跳过 EvidenceChecker。** idea 是用户自己的话，不强制绑定
  证据卡；诚实表述为观点的内容可发。Safety 仍是硬门禁（`UNSAFE_TO_PUBLISH`）。
- **无自动发布**：`gh` / `opencli` 只读；idea 草稿与 daily 草稿一样，发布由人工完成。
- **not_ready 不落库**：未成熟想法不保存，用户改后重交即可（MVP 不做 Idea 生命周期）。
- **确定性边界不变**：Graph 与证据链（`Commit → EngineeringEvent → EvidenceCard → Draft`）不变；
  `idea` 是独立入口，绝不接入 `run_daily`。

## 1. 范围

**本次（一次性实现）**：

- 新增 `finch idea <text> [--json]` 命令。
- 新增 `src/finch/idea/`（`models.py` + `service.py`）与两个 prompt（`assess-idea.md`、`write-idea.md`）。
- 评估服务：判断 `ready/not_ready` + `core_point` + `matched_evidence_ids` + 重复检测 + job 语境。
- 写样稿服务 + Critic（去掉 EvidenceChecker）+ 1–2 轮 rewrite 循环。
- ready 且通过 Critic → 落库 `ContentJob` / `Draft` / 一轮 Critic 报告。
- `cli.py` 新增 `idea` 命令（编排：读证据卡、读最近帖子、两次调用、Critic、落库、输出）。

**范围外（明确不做）**：

- Idea 数据库表与复杂生命周期；`READY / DEVELOP / DROP` 多级状态。
- 0–100 内容评分、多版本样稿、独立 Idea Graph、自动发布。
- 长期风格训练、自动调整 Prompt/权重、大范围外部搜索。
- 修改 `GraphRuntime` / `replay` / 证据链 / 互动轨道。

## 2. 背景与现状（已核实）

- **证据硬门禁**：`content/claims.py:47` 的 `validate_draft` 要求草稿至少 1 条 claim、每条 claim 的
  `evidence_card_id ∈ card_ids` 且置信度 `assertable`；`content/checkers/evidence.py:116` 的
  `EvidenceChecker` 对零证据/越界主张返回 `hard_fail`（不可被平均分掩盖）。
- **写稿器同样硬依赖证据**：`content/writer.py:188` 的 `write_original` 内部调用 `validate_draft`，
  零证据时返回 `None`（不产草稿）；`prompts/draft-original.md` 明写「只依据 Evidence cards 写」。
  因此假设类 idea **不能复用 `write_original`**，必须有自己的写稿 prompt。
- **Critic 套件**：`graph/content_nodes.py:177` 的 `default_checker_suite` 返回 8 个检查器
  （evidence/decision/specificity/portability/voice/structure/actionability/safety）；
  `content/checkers/aggregate.py:24` 的 `aggregate_checks` 输出 `pass/rewrite/reject/needs_input`；
  `make_critique_node` 的 rewrite 循环以 `settings.quality_gates.max_rewrite_rounds`（默认 2）为上限。
- **ContentJob 必填字段**：`content/jobs.py:89` 的 `ContentJob` 要求 `source_card_ids`（list）、
  `intended_effect`、`success_criteria`（list）、`recommended_format`、`status`；提案样例遗漏了
  前三个，本设计补齐：`source_card_ids=matched_evidence_ids`（可空）、`intended_effect` 由评估产出、
  `success_criteria` 用固定默认（见 §4）。
- **相关证据检索不存在**：`storage/repositories.py` 的 `EvidenceRepository` 只有 `get_card` /
  `list_cards`，无「按输入取最相关 N 张」的方法。MVP 把全部卡精简后交给评估模型自行选
  `matched_evidence_ids`（≤5），不新增检索/向量。
- **最近帖子无排序/限量**：`AuthorPostRepository.list()` 返回全部帖子。MVP 在服务层按
  `published_at` 降序、`kind ∈ {original, reply}`、取前 25 条。author 同步未跑过（表空）时传空列表，
  评估照常，重复检查退化为「无历史可查」。
- **两套落地命令并存**：`review approve/revise/skip`（按 draft_id）与 `decide accept/revise/skip`
  （按 job_id，原子落地 + fingerprint）。idea 生成的 job 已 `confirmed=True`，`review approve` 足够；
  MVP 沿用提案的 `review` 命令，不改 `decide`。
- **`AuthorPost`**（`author/models.py:18`）含 `kind`（original/reply/quote）、`body`、`url`、
  `published_at`；重复检测命中时 `duplicate_post_url` 取该帖 `url`。

## 3. 架构

```
src/finch/idea/
  __init__.py
  models.py      # IdeaAssessment / AssessIdeaOutput / WriteIdeaOutput / RewriteIdeaOutput
  service.py     # assess_idea / write_idea / run_idea_critic / rewrite_idea / assess(...) 编排
prompts/
  assess-idea.md # 判断 + 提取 job 语境（单次调用）
  write-idea.md  # 把用户原文 + 证据扩写成样稿（单次调用）
src/finch/cli.py # + idea 命令（读仓库 → 两次调用 → Critic → 落库 → 输出）
tests/unit/test_idea_service.py
tests/unit/test_cli_idea.py
```

`idea` 不新增 Node、不接入 `daily_nodes`；`service.py` 是纯函数集合，CLI 负责 IO 与落库
（与 `review` / `engagement` 命令的编排风格一致）。

## 4. 数据模型

```python
class IdeaAssessment(BaseModel):            # 公开 --json 输出（不含内部 job 语境）
    status: Literal["ready", "not_ready"]
    reason_code: str | None = None           # 六值之一；ready 时为 None
    reason: str = ""
    core_point: str | None = None
    matched_evidence_ids: list[str] = Field(default_factory=list)
    duplicate_post_url: str | None = None
    draft_id: str | None = None
    sample: str | None = None

class AssessIdeaOutput(IdeaAssessment):     # 调用①内部输出：ready 时携带构建 ContentJob 的语境
    reader_problem: str | None = None
    audience: str | None = None
    understand: str | None = None
    believe: str | None = None
    action: str | None = None
    claim: str | None = None
    decision: str | None = None
    tradeoff: str | None = None
    change_mind_if: str | None = None

class WriteIdeaOutput(BaseModel):           # 调用②内部输出
    body: str

class RewriteIdeaOutput(BaseModel):         # rewrite 内部输出（只回传正文，claims 恒为空）
    body: str
```

`reason_code` 六值：`NO_CLEAR_POINT / NO_NEW_VALUE / INSUFFICIENT_EVIDENCE / DUPLICATE_CONTENT /
TOO_BROAD / UNSAFE_TO_PUBLISH`。

**构建 ContentJob（ready 时）**：

- `job.id = "idea_" + sha256(text)[:8]`（确定性，幂等 merge）；`draft.id = "draft_" + job.id`。
- `source_card_ids = matched_evidence_ids`（可为空）。
- `reader_problem / audience / intended_effect / author_position` 来自调用①语境；缺失字段用
  空串兜底，`author_position` 缺 `decision/tradeoff` 时仍可出稿（idea 不走 `position_gate`）。
- `author_position.confirmed = True`、`position_source = PositionSource.HUMAN_CONFIRMED`。
- `recommended_format = DraftKind.ORIGINAL`、`status = ContentJobStatus.READY`、
  `scope = ContentScope.BOUNDED_LESSON`（默认）、`why_now` / `core_message` 留空。
- `success_criteria`：固定一条 `SuccessCriterion(id="idea_human_review", description="人工审核确认
  是否发布", measurement="human")`，不让模型生成。

**构建 Draft（ready 时）**：`kind=ORIGINAL`、`language="zh"`、`body=调用②的 body`、`claims=[]`、
`content_job_id=job.id`、`position_statement=decision`、`run_id="idea"`（常量，不污染 daily 摘要）。

## 5. 流程（两次调用 + rewrite 循环）

```
text
 → 读证据卡：EvidenceRepository.list_cards() 精简为 {id, claim, topics}，cap 50 张
 → 读最近帖子：AuthorPostRepository.list() 按 published_at 降序、kind∈{original,reply}、取 ≤25
 → 调用① assess_idea(text, cards, recent_posts)
      ├─ not_ready → 返回 reason_code + reason，结束（不落库）
      └─ ready    → 用 matched_evidence_ids 过滤出证据卡
                     → 调用② write_idea(text, core_point, job 语境, matched 证据卡) → body
                     → run_idea_critic(draft, job, cards)：7 检查器 + aggregate + rewrite 循环
                         ├─ pass → 落库 job / draft / 一轮 critic 报告 → ready + draft_id + sample
                         └─ 其它 → not_ready（映射 reason_code），不落库
```

**Critic rewrite 循环**（`run_idea_critic`，上限 `quality_gates.max_rewrite_rounds`）：

```
for round in 0..max_rewrite_rounds:
    checks = _run_checks(idea_suite, CheckContext(draft, cards, job))   # 7 个检查器
    outcome = aggregate_checks(checks)
    if pass:     return 落库
    if reject:   return not_ready(映射 Safety→UNSAFE_TO_PUBLISH；其余→NO_NEW_VALUE)
    if needs_input: return not_ready(NO_NEW_VALUE，reason 摘引 issues)
    if round == max_rewrite_rounds: return not_ready(NO_NEW_VALUE，reason 摘引 issues)
    draft = rewrite_idea(runner, draft, failed_checks, job)             # 只回传新正文，claims 恒空
```

`rewrite_idea` 用独立 prompt（内联常量，仿 `writer._REWRITE_PROMPT` 但**去掉证据绑定两行**：
「Only use evidence cards… / Every claim must carry…」），只要求「保持 id/kind/language，只修
Failed checks 列出的失败项，不整体润色」；返回 `Draft.model_copy(update={"body": ...})`。

## 6. Critic 复用

- 检查器套件 = `default_checker_suite(...)` 过滤掉 `checker.name == "evidence"`，其余 7 个原样复用
  （decision/specificity/portability/voice/structure/actionability/safety）。**只有 Safety 是硬门禁。**
- `CheckContext(draft, cards=matched 证据卡, job=ContentJob)`；`_run_checks` 复用
  `graph/content_nodes.py` 的并行执行（`pool.map` 保序）。
- 落库时写一条 `CriticReportRepository.upsert_report(draft_id, round=0, checks, outcome="pass")`，
  使 `review show` 的 Critic 区块正常渲染（`rewrite_count=0`、无失败项）。
- reason_code 映射（Critic 失败侧）：`safety` 的 `hard_fail` → `UNSAFE_TO_PUBLISH`；其余任何失败
  （reject/needs_input/rewrite 用尽）→ `NO_NEW_VALUE`，`reason` 摘引失败检查器的 `issues`。

## 7. 判断标准与 reason_code

评估 prompt（`assess-idea.md`）只查四件事，二元结论：

1. 有没有明确观点（`NO_CLEAR_POINT` / `TOO_BROAD`）；
2. 对具体读者有没有新增价值（`NO_NEW_VALUE`）；
3. 是否有证据，或诚实表述为个人判断/假设（`INSUFFICIENT_EVIDENCE`）；
4. 是否与最近发布高度重复（`DUPLICATE_CONTENT`，带 `duplicate_post_url`）。

`UNSAFE_TO_PUBLISH` 只从 Critic 的 Safety 硬门禁产出（评估阶段不做安全判断，安全由 Critic 兜底）。
相关证据由模型在评估时从精简卡列表中自选（≤5），不做确定性关键词匹配。

## 8. CLI 契约

```bash
finch idea "我觉得 Agent Graph 真正的价值不是画流程，而是支持失败恢复"
finch idea "..." --json
```

`--json` 输出 `IdeaAssessment.model_dump_json(indent=2)`（`status/reason_code/reason/core_point/
matched_evidence_ids/duplicate_post_url/draft_id/sample`）；非 JSON 按提案文案渲染「适合发」/
「暂时不适合发」，并给出 `review approve/revise/skip <draft_id>` 的可复制命令。

编排步骤（`cli.py` 的 `idea` 函数）：

1. `load_settings()` + `Store.init()`。
2. `EvidenceRepository(store).list_cards()` 精简（id/claim/topics，cap 50）。
3. `AuthorPostRepository(store).list()` 过滤排序取 ≤25。
4. 三个 runner：`assess_idea` / `write_idea` 用 `create_runner(settings.llm, "<node>") or CodexRunner()`；
   Critic 检查器复用 `create_runner(settings.llm, "critique") or CodexRunner()`。
5. `idea.service.assess(...)` → `IdeaAssessment` +（ready 时）落库 `ContentJob` / `Draft` / 报告。
6. 输出文本或 JSON。

## 9. 错误处理

- 评估/写稿/rewrite 的 runner 子进程失败或结构化校验失败（`StructuredOutputError` / `RuntimeError`）
  → 干净报错，不落库，`--json` 时输出 `{"status":"error","message":...}`，`typer.Exit(1)`。
- 空输入 / 纯空白文本 → 提示后 `typer.Exit(1)`（不调 LLM）。
- 无证据卡、无最近帖子均可运行（传空列表），不视为错误。
- not_ready 路径不写任何仓储记录。

## 10. 测试

- `assess_idea` 纯函数：ready/not_ready 六 reason_code 映射；job 语境解析；`confirmed=True` +
  `position_source=HUMAN_CONFIRMED`；`source_card_ids` 为空时不报错。
- `run_idea_critic`：去掉 EvidenceChecker 后 7 检查器；Safety `hard_fail` → `UNSAFE_TO_PUBLISH`；
  其余失败 → `NO_NEW_VALUE`；rewrite 循环在 `max_rewrite_rounds` 后停止；pass 时落报告。
- `write_idea` / `rewrite_idea`：只回传正文；rewrite 不引入 claim（claims 恒空）。
- 落库：ready+pass 写 `ContentJob` / `Draft` / 一轮 critic 报告；not_ready 不写。
- CLI：`--json` 契约形状；非 JSON 文案；错误路径。

## 11. 迁移

无新增表、无 alembic 迁移。只复用现有 `ContentJobRecord` / `DraftRecord` / `CriticReportRecord`。

## 12. 与既有设计的关系

本设计是 [[2026-09-06-single-decision-point-design]] 之外的**独立入口**：后者把 daily 轨道的
「确认立场 + 批准草稿」合并为一次 `decide`；本设计给用户一个**主动提交想法**的通道，落地后产出
的 `ContentJob`（`confirmed=True`）与 `Draft` 直接进入同一套 Review，`decide` / `review` 命令照常
可用。二者共享 `ContentJob → Draft → Critic → Review` 后半段，不共享上游证据链与 position_gate。
