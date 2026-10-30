# Problem Discovery 筛选规则收紧 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `skills/problem-discovery` 的筛选从"凭直觉挑重点"收紧为"正向筛选 + 三分离 + 证据收紧四条 + 主题加权"，并把每个重点问题的呈现收成五件（含三个新字段）。

**Architecture:** 纯文档改动，落在 `skills/problem-discovery/` 三个文件：新增 `references/screening.md`（筛选真源），重写 `references/presentation.md`（逐项五件 + 判断边界 + 尚未支持），改 `SKILL.md`（流程引用筛选规则 + 边界收紧 + 参考补一行）。

**Tech Stack:** 无（纯 Markdown 文档）。

## Global Constraints

- 范围只限 `skills/problem-discovery/`；不动 `src/finch/problem_discovery/`、`prompts/problem-discovery.md`、CLI、任何 Python 模型。
- 只读；不引入排序分数、配置项、主题加权参数。
- 文档为中文；证据收紧四条话术与 spec 一致（"声称已修复，解决状态未证实" 等）。
- 逐项五件固定为：受阻场景 / 已知事实 / 尚未证实什么 / 下一步最小验证 / 验证后的去向。

---

### Task 1: 新增 `references/screening.md`

**Files:**
- Create: `skills/problem-discovery/references/screening.md`

**Interfaces:**
- Consumes: 无（首个任务）。
- Produces: `references/screening.md`，含五个段落：正向筛选 / 三分离 / 不作为主要排序依据 / 证据收紧 / 优先观察主题。Task 2、Task 3 引用其中的措辞。

- [ ] **Step 1: 写入 screening.md**

创建 `skills/problem-discovery/references/screening.md`，内容如下：

```markdown
# problem-discovery 筛选规则

本文件是 Skill 挑「≤3 个重点问题」的筛选真源。呈现形状见 `presentation.md`，共享呈现原则见
`_shared/agent-presentation.md`。

## 正向筛选

优先发现**真实任务中的失效**，记录**可观察后果**：谁在什么任务上卡住、卡在哪一步、后果是什么。
能通过技术验证推进的问题优先；含糊、无法验证、纯观点的问题不优先。

## 三分离

对每个候选分别判断三件事，**不合成一个"综合分"**：

- **证据强度**：失效有多少独立来源支持、后果是否可观察、根因是否已确认。
- **个人匹配程度**：用户（你）是否具备验证 / 修复所需的上下文与能力。
- **当前可参与方式**：修 bug / 补测试 / 协助已有 PR / 继续观察，哪一条现在可行。

三件事各自独立给出判断，不因为"证据强"就默认"我能修"，也不因为"无人认领"就默认"值得先看"。

## 不作为主要排序依据

`good first issue`、无人认领、近期活跃——可作参考，但不作为主要排序键。

## 证据收紧（硬约束）

1. 有人声称已修复 ≠ 已合并 / 已发布 / 有效 → 写「有人声称已修复，解决状态未证实；优先验证最新版本」。
2. AI agent 补充的 triage / acceptance criteria ≠ 维护者人工确认；可观察标签（如 `priority:high`）不等于人工确认方案。
3. 贡献者报告在某版本复现 ≠ 已验证 → 自己运行前写「贡献者报告在指定版本复现」。
4. 多人讨论 / 多人认领 ≠ 多人独立复现。

## 优先观察主题

可增补的短清单，不落评分、不落配置项：

- **Agent 异常后能否正确停止、保留状态、并恢复执行。** 优先纳入该主题的问题；同时保留其他真实使用障碍的探索空间。
```

- [ ] **Step 2: 验证文件落盘且关键措辞在**

Run: `grep -n "声称已修复，解决状态未证实" skills/problem-discovery/references/screening.md`
Expected: 命中一行（证据收紧第 1 条）。

Run: `grep -c "## " skills/problem-discovery/references/screening.md`
Expected: `5`（五个二级标题：正向筛选 / 三分离 / 不作为主要排序依据 / 证据收紧 / 优先观察主题）。

- [ ] **Step 3: Commit**

```bash
git add skills/problem-discovery/references/screening.md
git commit -m "docs(problem-discovery): add screening rules reference"
```

---

### Task 2: 重写 `references/presentation.md`

**Files:**
- Modify: `skills/problem-discovery/references/presentation.md`（全文件重写）

**Interfaces:**
- Consumes: Task 1 的证据收紧四条话术（逐字复用）。
- Produces: 逐项五件结构 + 判断边界 + 尚未支持的"执行验证由用户做"；Task 3 的"逐项呈现"对齐此五件。

- [ ] **Step 1: 用以下内容整体替换 presentation.md**

````markdown
# problem-discovery 呈现配方

见 `_shared/agent-presentation.md` 的共享原则。本文件是形状真源与命令映射。

## 默认返回结构

四部分，不逐条铺开卡片：

1. **结果概览**：共几张卡片、来源范围（`summary.source_scope`：`single_repo` / `mixed` /
   `none`）、分析是否完成。问题组数量仅在 `summary.groups_available=true` 时出现（当前为
   `false`，就明说「尚未合并」，不要编造组数）。
2. **重点问题**（最多 3 个），每项五件事：
   - 受阻场景：谁想完成什么、在哪一步卡住；缺失写「未知」。
   - 已知事实：只写原始讨论支持的行为 / 环境 / 结果，关联来源链接。
   - 尚未证实什么：根因 / 影响范围 / 当前版本状态，各自可写「未证实 / 待核实」；与事实分开。
   - 下一步最小验证：哪一个实验能减少关键不确定性 + 理由。
   - 验证后的去向：修复 / 补测试 / 协助已有 PR / 继续观察，据证据强度与个人匹配度给出。
3. **判断边界**：单仓库公开 issue 的覆盖限制；不称「普遍需求」「商业机会」；区分历史报告与当前仍存在（未核对写「当前状态待核实」）。
   证据收紧四条：声称已修复 ≠ 已修复；AI triage ≠ 维护者确认；贡献者报告复现 ≠ 已验证；
   多人讨论 / 认领 ≠ 多人独立复现。另：制造页面崩溃 ≠ 复现原报告场景（分别记录）。
4. **继续入口**：2–3 个与本轮内容相关的自然语言示例，如「展开第二个」「看看哪些缺证据」。

## 形状

```text
从同一项目的公开讨论里整理出 17 张问题卡片。建议先看影响首次使用和连续操作的线索。

**多轮操作可能丢失标签页上下文**
讨论报告跨轮次标签页无法保留、会话结束状态不一致；当前版本状态待核实，根因（释放行为 / 缺陷）尚未证实。
下一步最小验证：先查维护者解释与修复记录，再复现最小多轮场景；验证后去向：视结果修 bug 或补测试。

另外两条可选：2. 安装后无法连接 … 3. 工具调用静默失败 …

你可以继续说：「展开第二个」「看看剩下的」或「哪些缺证据」。
```

（数量与措辞为示例；正式返回以 `summary --json` 与真实卡片为准。）主文不贴 `prob_*` id、不贴
`uv run finch ...` 命令、不写保存目录。

## 隐藏规则

- 不展示 CLI 命令、代码块、保存目录、内部字段名、内部 ID（`prob_*` / `disc_*` / `run_*` / `pd_*`）。
- agent 内部保留「序号 → problem_id」映射（用 `present` 落库），重排 / 换一批后不能选错。
- 失败只说改变结果的失败与重试；工具读写成功不必报。
- 技术细节（文件位置 / 命令 / id）仅在用户明确索取时提供。

## 用户下一轮 → CLI

- `展开第 N 个` → `uv run finch problem-discovery last-presented --json` 取最近映射 →
  `problem_ids[N-1]` → `show {id} --json`，补证据 / 假设 / 未知 / 来源。
- `看剩下的` / `继续看` → `list --json` 减去已展示的 id，挑下一批（不重复前一批），
  展示后 `present <新有序 id...>` 覆盖映射。
- `换一批` → 从本轮未展示的里重挑，同「看剩下的」。
- `哪些缺证据` → `summary --json` 的 `cards_without_evidence`，点名并说明影响。
- 上下文不足或多个候选同样匹配 → 只提一个必要澄清问题，不要求输入内部 ID，不擅自选择。

## 尚未支持（明确说明，不擅自代替）

- `哪些已修复` / `合并这两个` / `找更多类似反馈` 属 P2（修复状态核对 / 问题组 / 独立反馈去重）。
  当前如实说「这一版还不能核对修复状态 / 合并卡片」，并给可替代的下一步（如展开该卡看证据）。
- Finch 给出「下一步最小验证」动作，但**执行验证**（跑复现、看合并记录）由用户做；Finch 只记录
  「未证实」与「待核实」，不擅自声称已验证。
````

- [ ] **Step 2: 验证五件结构与关键话术落地**

Run: `grep -n "尚未证实什么\|下一步最小验证\|验证后的去向" skills/problem-discovery/references/presentation.md`
Expected: 三行都命中（逐项五件里的三个新字段）。

Run: `grep -n "执行验证" skills/problem-discovery/references/presentation.md`
Expected: 命中"尚未支持"段的那一行。

- [ ] **Step 3: Commit**

```bash
git add skills/problem-discovery/references/presentation.md
git commit -m "docs(problem-discovery): restructure per-problem presentation into five items"
```

---

### Task 3: 改 `SKILL.md`

**Files:**
- Modify: `skills/problem-discovery/SKILL.md`

**Interfaces:**
- Consumes: Task 1 的 `references/screening.md`（流程第 3 步引用）；Task 2 的逐项五件（向用户呈现对齐）。
- Produces: 无（末任务）。

- [ ] **Step 1: 改流程第 3 步，引用 screening.md**

将原文：

```markdown
3. 从卡片里挑最多 3 个重点问题（定性、有解释，不写总分，不生成精确无依据的排名）。
```

改为：

```markdown
3. 按 `references/screening.md` 挑最多 3 个重点问题（定性、有解释，不写总分，不生成精确无依据的排名）。
```

- [ ] **Step 2: 在"边界"段补证据收紧两条**

将"边界"段中已有的这一条：

```markdown
- 单仓库公开 issue 覆盖有限：不自动称「普遍需求」「商业机会」；issue 关闭 ≠ 已修复，
  当前状态未核对时写「待核实」。
```

之后紧跟新增一条（即在该 bullet 之后、下一 bullet 之前插入）：

```markdown
- 声称修复 / 报告复现 / AI triage 不等同于已修复 / 已验证 / 维护者确认；多人讨论 / 认领
  不等同于多人独立复现。
```

- [ ] **Step 3: 在"参考"段补 screening.md 一行**

将"参考"段：

```markdown
## 参考

- `references/presentation.md` — 默认返回结构与「展开 / 看剩下的 / 换一批」映射。
```

改为：

```markdown
## 参考

- `references/screening.md` — 挑重点问题的筛选真源（正向筛选 / 三分离 / 证据收紧 / 主题）。
- `references/presentation.md` — 默认返回结构与「展开 / 看剩下的 / 换一批」映射。
```

- [ ] **Step 4: 改 frontmatter `description` 的旧五件事为新五件**

将 `description` 中的旧五件事：

```markdown
  再由本 Skill 挑最多 3 个重点问题，给出受阻场景 / 已知事实 / 可能影响 / 关键未知 /
  建议动作，并支持「展开第二个」「看剩下的」等自然语言连续查看。
```

改为：

```markdown
  再由本 Skill 挑最多 3 个重点问题，给出受阻场景 / 已知事实 / 尚未证实什么 / 下一步最小验证 /
  验证后的去向，并支持「展开第二个」「看剩下的」等自然语言连续查看。
```

- [ ] **Step 5: 验证四处改动落地**

Run: `grep -n "references/screening.md" skills/problem-discovery/SKILL.md`
Expected: 命中两行（流程第 3 步 + 参考段）。

Run: `grep -n "多人讨论 / 认领" skills/problem-discovery/SKILL.md`
Expected: 命中边界段新增的那一行。

Run: `grep -n "尚未证实什么 / 下一步最小验证" skills/problem-discovery/SKILL.md`
Expected: 命中 frontmatter `description` 那一行。

- [ ] **Step 6: Commit**

```bash
git add skills/problem-discovery/SKILL.md
git commit -m "docs(problem-discovery): wire screening rules into skill flow and boundaries"
```

---

## Self-Review

- **Spec coverage**：screening.md 五段（Task 1）→ 覆盖 spec 3.1；逐项五件 + 判断边界 + 尚未支持（Task 2）→ 覆盖 spec 3.2；流程引用 + 边界 + 参考（Task 3）→ 覆盖 spec 3.3。spec 第 4、5 节（非目标 / 验证）体现在 Global Constraints 与各验证步骤，无遗漏。
- **Placeholder scan**：无 TBD / TODO / "类似上一步"；每个文档改动都给了完整替换内容。
- **Type consistency**：证据收紧四条话术在 screening.md、presentation.md、SKILL.md 三处一致；逐项五件字段名在 presentation.md 与 spec 一致。
