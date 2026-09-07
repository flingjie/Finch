# 草稿可决策界面：scoping 式 Critic + 决策化 CLI 返回

日期：2026-09-07
状态：已确认（待写实现计划）

## 问题

`finch drafts create` 现在输出一段「任务执行报告」：先给 `draft_id`，再甩正文，把
Critic 轮次、调试信息、以及无关的系统现状混在一起。用户真正要审核的是草稿本身，但机器
运行日志压过了它。更糟的是，草稿内容本身也被 Critic 逼成了重复、保守、带论文式免责声明
的样子。

本设计把 Finch 的草稿产出从「执行报告」改成「可决策界面」，并修正导致草稿变形的三个根因。

## 根因（来自代码 + DB 现场取证）

1. **绝对结论来自已确认的 `AuthorPosition.tradeoff`，不是 writer 编的。**
   `idea_283d2989` 的 DB 记录里 `tradeoff` 原文为「…但稳定后仍需代码化」。而
   `draft-patterns.md` 要求 claim/decision/tradeoff **原样表达**，于是草稿忠实复刻了绝对
   判断。修草稿不改立场，根因仍在数据层。

2. **PortabilityChecker 让 writer「锚定到证据」，但 idea 草稿没有证据卡。**
   `idea_checker_suite` 去掉 `EvidenceChecker`，`write_original_from_job`/`rewrite_idea` 恒
   产出 `claims=[]`、`cards=[]`。PortabilityChecker 的修复指令「anchor the claim to a
   concrete detail from the evidence」指向不存在的证据，writer 只能反复重申项目名
   （「Graph 切到 Skill」出现三次）+ 追加免责声明来「锚定」。DB 三轮 Critic 证实：round 0
   把免责声明判为 generic，round 1 把主句判为 generic，round 2 才 pass。

3. **`claims=[]` 是设计而非 bug。** idea 流不绑定证据卡，`claims` 无下游消费者。提取
   claim 是新能力，不是修复项 → **本次暂缓**。

4. **`ideas list` 崩溃来自旧数据。** `job_tp1` 的 `status="ready"` 不在当前
   `ContentJobStatus` 枚举里，`ContentJob.model_validate_json` 抛错 → `list_jobs()` 炸掉。
   `job_tp*` 是 graph/旧 content 编排时代的产物，带 `confirmed`/`intended_effect`/
   `success_criteria` 等废弃字段。

5. **（新发现）draft 幂等键只 hash `core_message`，改了立场也不会重生成草稿。**
   `draft_generation_key` 用 `_idea_fingerprint(job)` = `content_fingerprint`（仅
   `core_message` 的 sha256）。但 writer 实际读 7 个字段（`reader_problem`/`core_message`/
   `why_now` + `claim`/`decision`/`tradeoff`/`change_mind_if`），且 `revise_position` 不更新
   `content_fingerprint`。于是「改 tradeoff → 重生成」命中同一 `draft_id`，返回旧的绝对
   草稿，静默吞掉修改。

## 已确认决策

| 决策点 | 结论 |
|---|---|
| scoping 是否允许 | **Both**：缩小适用范围 = 允许的表达调整（系统化），同时修正当前这条 tradeoff 文本 |
| claims 提取 | **暂缓**（`position_claims`/`factual_claims` 另立 spec） |
| `job_tp*` 旧数据 | **删除** + `list_jobs` 容错读 |

## 设计

### A. 内容质量（writer + PortabilityChecker + 不变量 carve-out）

**A1. writer 首稿 prompt（`prompts/draft-from-job.md`）** — 增加三条 scoping 规则，并把
「立场原样表达」改为允许 scoping-only 收窄：

- 绝对结论 → 条件结论：`「稳定后仍需代码化」` 改写为 `「稳定后，再把需要确定性/幂等/状态
  持久化的部分代码化」`（只收窄适用范围，不推翻决策方向）。
- 项目背景（「Finch 从 Graph 切到 Skill」）至多出现一次，作为锚点，不反复重申。
- 不追加 `「这个判断只限定于…不作为普遍结论」` 式免责声明——用条件化措辞本身完成限定。

**A2. PortabilityChecker（`src/finch/content/checkers/portability.py`）** — 根因是它
one-size 的「anchor to evidence」指令在无证据场景下失效。改为按句分类、按类给修复指令：

- 输出 schema：`generic_sentences: list[str]` → `findings: list[{sentence, kind}]`，
  `kind ∈ {overgeneralized, boilerplate, disclaimer}`。
- 修复指令按 `kind`（并区分 `ctx.cards` 是否非空）：
  - `overgeneralized` → 「conditionalize：改写为条件结论（限定触发条件/适用范围），不追加
    免责声明」。
  - `boilerplate` → 有证据：「anchor to a concrete evidence detail」；无证据：「remove or
    make specific to this project」。
  - `disclaimer` → 「remove the meta-disclaimer; scope the underlying claim instead」。
- 检测 prompt 增加分类步骤；原 prompt 已能正确 *标记* 免责声明和主句，坏的是 *修复指令*。

指令经 `_render_failed_checks` 流入 `rewrite_idea`/`rewrite`，无需改管道。

**A3. 不变量 carve-out** — 在 `skills/idea-to-draft/references/draft-patterns.md`（「立场
原样表达，不改变」）与 `skills/_shared/author-position.md`（「不改变作者立场」）各加一条：
**缩小适用范围 / 把绝对结论改写为条件结论（scoping-only）是允许的表达调整；不得推翻或反向
改写 decision/tradeoff 方向。**

### B. 数据

**B1. 旧数据清理 + 容错读。**
`Store.prune_legacy_content_jobs()` 删除 `contentjobrecord` 中 `payload_json` 无法
`ContentJob.model_validate_json` 的行（即 `job_tp*`），幂等，清理后 no-op。`init --prune`
调用它并回报删除数。`ContentJobRepository.list_jobs()` 改为容错：跳过不可解析行、绝不
raise，暴露 `skipped_legacy_job_ids` 供 CLI 渲染系统警告。

**B2. draft 幂等键修复（根因 #5 的阻塞项）。**
`_idea_fingerprint(job)` 从「仅 `core_message`」改为「writer 实际读取的 7 字段」的 sha256。
立场修改 → 指纹变化 → 新 `draft_id` → 正确重生成。
副作用（已接受）：旧键下产出的草稿在新键下重生成一次——这正是修 `draft_9ac52c0fd796b67f`
所需要的。

**B3. 修正 tradeoff 文本 + 重生成。**
`finch ideas revise-position idea_283d2989` 把 tradeoff 改为：

> skill 形式前期验证快、成本低；稳定后，再把需要确定性、幂等或状态持久化的部分代码化；
> 过早代码化会抬高维护成本

`claim`/`decision` 已正确，只改绝对化的 tradeoff。B2 就位后 `finch drafts create
idea_283d2989` 即重生成 scoped 草稿。

### C. 输出界面

**C1. `DraftService.create` 返回 `DraftCreateResult`**（`draft + critic_rounds + outcome +
adjustments_summary`）。`critic_rounds`/`outcome` 从 `CriticReportRepository`
派生（缓存命中与新建路径都可用）。`adjustments_summary` 由**确定性映射**派生：对每轮
fail→final pass 的 checker 映射一句人话（`portability` → 「收紧了观点的适用边界」等），
**不新增 LLM 字段、LLM 不携带总分**。

**C2. `drafts create` / `drafts show` 人类输出改为可决策界面**（默认）：

```
草稿已生成并通过质量检查，当前等待你的审核。

> {body}

质量检查：通过
主要调整：收紧了观点的适用边界
状态：未发布

下一步：
- 采用并进入发布意图：finch review approve {draft_id}
- 继续修改：finch drafts revise {draft_id} --instruction "…"
- 放弃草稿：finch review skip {draft_id} --reason "…"
```

`质量检查` 由 `outcome` 映射：`pass` → 「通过」；rewrite 用尽仍未 pass → 「未通过（重写
N 轮后仍未满足）」，此时 `下一步` 保留但草稿需人工判读。「主要调整」仅在存在 fail→pass 的
checker 时显示。

「运行详情」（draft_id / idea_id / critic_rounds / outcome）移到 `--verbose`。`--json` 保持
机器可读、不变。

**C3. 系统警告通道。** 末尾独立的 `系统警告` 块，仅在存在非致命诊断时渲染，永远在可决策
界面之后、不混入成功结果。`ideas list` 在其中报告跳过的旧行（计数 + 迁移提示）。

## 触碰文件

- `prompts/draft-from-job.md` — scoping 规则（A1）
- `src/finch/content/checkers/portability.py` — findings schema + 按类修复指令（A2）
- `skills/idea-to-draft/references/draft-patterns.md`、`skills/_shared/author-position.md` — carve-out（A3）
- `src/finch/drafts/service.py` — `_idea_fingerprint` 全字段 hash + `DraftCreateResult`（B2/C1）
- `src/finch/storage/database.py` — `prune_legacy_content_jobs`（B1）
- `src/finch/storage/repositories.py` — `list_jobs` 容错读（B1）
- `src/finch/cli.py` — `drafts create/show` 决策界面 + `--verbose` + 系统警告 + `init --prune` 清理（C）
- tests（见下）

## 测试

- `_idea_fingerprint`：任一 7 字段变化即变；`core_message` 不变但 `tradeoff` 变 → 变。
- `list_jobs`：跳过不可解析行 + 报告 skip id，不 raise。
- `prune_legacy_content_jobs`：只删不可解析行，幂等。
- `PortabilityChecker`：无证据时 `overgeneralized`→conditionalize、`disclaimer`→remove、
  `boilerplate`→remove/make-specific。
- `DraftCreateResult`：`adjustments_summary` 由 fail→pass 报告确定性派生。
- CLI 渲染函数：决策界面 + 系统警告顺序的 golden 输出。
- 全量 `uv run pytest`、`uv run ruff check .`、`uv run mypy src` 通过。

## 成功判据

1. 修正 tradeoff 后 `finch drafts create idea_283d2989` 产出 scoped、不重复、无免责声明的
   草稿，且默认输出是可决策界面，无混入警告。
2. `finch ideas list` 遇旧行不崩，改在系统警告里提示。
3. `adjustments_summary`/总分均由代码确定性计算，LLM 输出不携带 `total`。

## 暂缓（另立 spec）

- `claims` 提取（`position_claims` / `factual_claims`）：当前 idea 流 `claims` 无消费者。
