# Finch 主动对话与观点引导 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Finch 在宿主 Codex 会话中先交付任务结果、再主动延伸一个值得讨论的线索，并把讨论摘要薄持久化为可检索的跨会话记忆。

**Architecture:** 行为约定（markdown 策略文件 + 改造后的 topic-dialogue）负责「如何接话」，确定性 Python 领域服务负责状态与持久化。P0 只改 markdown（无 Python）；P1-A 新增 `src/finch/dialogue/`（`DialogueNote` 单 YAML 追加式存储）；P1-B 补齐 JSON 发现入口的新鲜度字段与显式呈现记录入口。

**Tech Stack:** Python 3.12+、Pydantic 2、Typer、文件 Workspace（YAML）、pytest、ruff、mypy。

## Global Constraints

- Python 3.12+；Pydantic 2（`StrEnum`/`Literal`/`Field`）；领域模型序列化到 YAML/Markdown/JSONL，经 `Workspace.atomic_write` 幂等覆盖（见 `src/finch/storage/workspace.py`）。
- 确定性领域服务单线程；不用 `asyncio.gather`；`ThreadPoolExecutor` 仅在服务步骤内做独立 I/O 子进程调用且用 `pool.map`。本次新增 `dialogue/` 不涉及子进程。
- `position_status=user_confirmed` 只描述讨论认同程度，不等于 `ContentJobStatus.CONFIRMED`，更不是事实证实。
- 讨论摘要不得写入 `PeerProfile` / `InteractionRecord` / `ConversationThread`，不改变关系/连接北极星指标。
- Ruff `E,F,I,B,UP`，行长 100，`mypy src`；双语 docstring 可接受。
- CLI 命令由 Skill 使用，不要求用户记命令。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `skills/_shared/dialogue-policy.md`（新） | 共享对话策略：先交付/延伸点选择/挑战下限/授权边界/收束/停止条件 |
| `skills/_shared/agent-presentation.md`（改） | 呈现原则：去业务数量、去固定三段结构、保留 ID 隐藏与失败诚实 |
| `AGENTS.md` / `CLAUDE.md` / `docs/product-contract.md`（改） | 共享策略读取入口 + topic-dialogue 定位调整 |
| `skills/topic-dialogue/SKILL.md` + 3 references（改） | 讨论伙伴改造：先分析、按分歧收束、取消固定轮次与每轮必问 |
| `skills/topic-dialogue/evals/cases.yaml`（新） | 18 个验收场景（行为契约） |
| 各业务 Skill 的 `SKILL.md`（改） | 完成后一次延伸点检查 + 引用 dialogue-policy |
| `README.md`（改） | 自然语言用法：只看结果/继续讨论/查看依据/先不查/别记这段 |
| `src/finch/dialogue/models.py`（新） | `DialogueNote` / `DialogueCheckpoint` / `PositionStatus` |
| `src/finch/dialogue/repository.py`（新） | 一个 note 一个 YAML 的文件仓库 |
| `src/finch/dialogue/service.py`（新） | save（幂等追加 + revision 冲突）/ search / show / forget |
| `src/finch/cli.py`（改） | 注册 `finch dialogue`；`connect daily --json` 补字段；`connect record-presented` |
| `tests/unit/test_dialogue_memory.py`（新） | 模型/仓库/服务单测 |
| `tests/unit/test_cli_dialogue.py`（新） | CLI 单测 |
| `tests/unit/test_cli_connect.py`（改） | JSON 字段 + 呈现记录契约 |

---

## Phase P0 — 对话体验（纯 markdown，无 Python）

P0 完成标准：日常输出与连续讨论行为可用；跨会话仅能引用已有可读记录（`ContentJob.position_revisions`），局限明确。每阶段末尾用「验收 grep」确认冲突规则已消除。

### Task 1: 新增共享对话策略 `skills/_shared/dialogue-policy.md`

**Files:**
- Create: `skills/_shared/dialogue-policy.md`

**Interfaces:**
- Produces: 被后续所有 SKILL.md 以相对路径 `_shared/dialogue-policy.md` 引用；无代码接口。

- [ ] **Step 1: 写文件**

写入以下完整内容：

````markdown
# 主动对话策略（共享原则）

Finch 完成用户任务后，像熟悉用户项目的同行一样接话：先交付、再延伸、按分歧决定追问或收束。
本文件是「如何接话」的共享策略；「如何完成任务」由各业务 Skill 定义。

## 总原则

- 先交付，再延伸。结果与意义优先，延伸不得抢占用户要求的名单 / 草稿 / 报告。
- 每次任务最多选一个延伸点，无价值就自然结束，不硬凑。
- 自然语言、同行语气，默认两三段，需要时展开。不输出内部状态、技术 ID、策略名。

## 延伸点选择

只选满足「与当前任务直接相关；可能改变判断或行动；有材料支持；尚未讨论清楚」的点。
可以是反例、范围变化、历史观点变化、证据不足，不能只有空泛的「值得深入」。

没有证据支持的挑战，提带条件的可能性或自然结束。不推测用户从未表达过的立场。

## 挑战的最低内容

让用户看见：哪条假设薄弱 → 反例如何影响结论 → 建议保留或收窄什么判断 → 一个回答后可能
改变结论的问题。

真实反例必须有可读来源；构造反例写「假设……」，不得伪装成已发生案例。检索到的他人经验
不转成用户个人证据。

## 授权边界

| 情况 | 行为 |
|---|---|
| 原任务明确要求搜索/刷新/读链接 | 按范围完成，不重复询问 |
| 为完成原请求必要的已授权读取 | 正常执行 |
| 新延伸出的争议要追加调查 | 先说明缺口、来源范围、预算、对结论的影响，等用户决定 |
| 用户说「查一下」且唯一计划明确 | 执行该计划 |
| 用户说「有道理」 | 不同时解释为同意观点 + 批准查证 + 批准实验 |
| 用户拒绝/延期/换话题 | 停止待查证，不换工具偷偷继续 |

## 追问与收束

- 每轮最多一个问题；没有关键分歧就零问题收尾。关键 = 回答是否改变结论或下一步。
- 取消固定轮次。出现清楚判断 / 关键分歧 / 证据缺口，或用户要求，就总结。
- 总结三项：用户当前认可的观点、成立条件 / 剩余缺口、一个小验证建议（行动 + 观察信号 +
  什么结果会推翻假设）。未形成观点就说「尚未形成」，不替用户定论。

## 停止条件

- 用户说「只给结果」「先不聊」「停」→ 遵循当轮指令，不追问。
- 用户换任务 → 立即跟随，不追旧问题。
- 用户只赞同一条理由 → 不把整套观点标成确认。
- 失败如实说影响范围，不压缩成假成功。

## 历史关联

- 主动联系过去观点时，读原记录并比较成立条件，再请用户确认变化；不凭同关键词认定矛盾。
- 无历史就明说无法确认，不伪造「你上次说过」。
````

- [ ] **Step 2: 提交**

```bash
git add skills/_shared/dialogue-policy.md
git commit -m "feat(dialogue): add shared dialogue policy"
```

### Task 2: 重写 `skills/_shared/agent-presentation.md`

**Files:**
- Modify: `skills/_shared/agent-presentation.md`（整体替换）

- [ ] **Step 1: 用以下内容整体替换文件**

````markdown
# 向用户呈现（共享原则）

Skill 调用 Finch CLI 之后，目标是帮用户做选择，不是汇报系统过程。完成后按
`_shared/dialogue-policy.md` 做一次延伸点检查（无有效点就自然结束）。

## 原则

- 先给结果与意义，再分析一个值得讨论的线索；讲解默认两三段，不截断用户要求的交付物。
- 点名首选一条，理由落到关系价值、可贡献空间或产品方向。
- 技术 id（`peer_*` / `opp_*` / `proposal_*` / `idea_*` / `thread_*` / `snapshot_*`）不写在
  标题里；agent 内部保留「序号 → id」映射，换一批后不能选错人。
- 失败只说改变了结果的失败与重试；工具读写成功不必报。
- 本文件只定原则；具体形状与动作词写在各 skill 的 `references/presentation.md`。

## 不再规定

- 不规定共享层的业务数量（首页人数、浏览人数、准备人数由各领域命令与其 Skill 契约决定）。
- 不规定固定「结论 / 决策卡 / 操作」三段结构；列表、卡片、报告等交付物保留各自需要的结构。
````

- [ ] **Step 2: 验收 grep** —— 旧数量词必须消失：

```bash
grep -nE "8–12|8-12|最多 3|最多 10|主视觉" skills/_shared/agent-presentation.md || echo "clean"
```

预期：`clean`

- [ ] **Step 3: 提交**

```bash
git add skills/_shared/agent-presentation.md
git commit -m "refactor(presentation): de-quantify shared presentation rules"
```

### Task 3: 根入口文档读取路径（AGENTS.md / CLAUDE.md / product-contract.md）

**Files:**
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `docs/product-contract.md`

- [ ] **Step 1: AGENTS.md** —— 在「核心原则」之后新增一节：

在 `AGENTS.md` 的「## 核心原则」列表之后、`## 命令` 之前插入：

```markdown
## 交互约定（产品使用）

使用 Finch 帮用户发现、连接、讨论、表达时，先读 `skills/_shared/agent-presentation.md`
与 `skills/_shared/dialogue-policy.md`，再读目标 Skill。完成用户任务后做一次延伸点检查
（无价值就自然结束）。代码维护、测试日志不套用产品话术。
```

- [ ] **Step 2: CLAUDE.md** —— 在「## Architecture」小节的 skills 列表后补一句定位说明：

在 `skills/` 代码块的 `_shared/` 行之后追加一行注释说明（不改代码块内容）：

```text
交互行为：业务 Skill 完成任务后按 `skills/_shared/dialogue-policy.md` 主动延伸一个讨论点；
topic-dialogue 是独立可唤起、且可从任务结果衔接的连续讨论入口（不进入默认流水线）。
```

- [ ] **Step 3: docs/product-contract.md** —— 定位「topic-dialogue 不进入默认流程」并补「主动邀请讨论 ≠ 自动执行领域流程」：

读 `docs/product-contract.md`，找到描述 topic-dialogue 的段落，追加一句：

```markdown
主动邀请讨论（任务后延伸、连续讨论）是当前会话内的行为约定，不自动执行任何领域流程
（不自动外发、不自动确认立场、不写关系事实）。
```

（若该文件没有单独 topic-dialogue 段落，则在「Skills」相关章节补上述句子即可。）

- [ ] **Step 4: 提交**

```bash
git add AGENTS.md CLAUDE.md docs/product-contract.md
git commit -m "docs: wire shared dialogue policy read-path"
```

### Task 4: 改造 `skills/topic-dialogue/SKILL.md`

**Files:**
- Modify: `skills/topic-dialogue/SKILL.md`（整体替换）

- [ ] **Step 1: 用以下内容整体替换文件**

````markdown
---
name: topic-dialogue
description: >
  围绕一个话题，通过连续对话帮用户说清自己的判断、理由、适用边界和证据缺口。
  用于「回应 Finch 上轮延伸问题」「继续刚才的判断讨论」「围绕这个话题跟我聊聊」
  「我有个想法还没想清楚，陪我讨论一下」「先别帮我写，挑战一下这个判断」
  「读完这个帖子后，我想和你讨论其中的观点」「从有线上实践经验的 Builder 角度跟我聊」。
  也可在业务 Skill 完成任务后沿延伸点衔接进入。核心结果是更清楚的判断，不是文章。
  单纯解释概念 → feynman-practice；已有明确观点且直接要求写草稿 → idea-to-draft。
  讨论不得写入 InteractionRecord、ConversationThread、PeerProfile 或关系指标。
---

# topic-dialogue

按需调用的讨论伙伴：帮用户形成或修正判断。练习上下文，不是真实互动；只有用户明确确认的
判断才能进入观点/内容链路。

## 默认定位

| 项 | 默认 |
|---|---|
| 角色 | 有实践经验、好奇、愿意提出不同角度的 Builder 同行 |
| 方式 | 已有材料先分析，再沿用户回答推进；真正缺少对象时才澄清 |
| 强度 | 先理解，再挑战影响结论的关键假设 |
| 节奏 | 按分歧决定追问或收束，不设固定轮数 |
| 资料 | 先讨论；事实争议会影响判断时，再建议或调用检索 |
| 结束产物 | 当前判断、成立条件/剩余缺口、下一步小实验 |
| 集成 | 独立可唤起；也可从任务结果衔接；用户明确选择后才转观点候选/草稿/真实互动准备 |

## 开场

话题足够清楚时直接开始，不先展示配置表。

- 已有材料（任务结果、帖子、片段）：先做一轮有价值分析，指出其中可动摇的判断或假设。
- 尚未给出判断：邀请用户给出当前判断或具体经历（只问一个具体问题，不问泛泛的「你怎么看」）。
- 已给出明确判断：用一句话复述理解，再追问最关键的依据。
- 缺少必要材料时才询问补充信息。

## 每轮规则

1. 用 1–3 句话回应用户刚才的内容，指出其中具体的判断、经验、矛盾或变化。
2. 需要追问时只问**一个**能推进讨论的问题；没有关键分歧就零问题收尾（见 `_shared/dialogue-policy.md`）。

从 `references/turn-strategies.md` 的七种推进动作中选一个。不得机械轮换，不得为显得有深度而强行反驳。

## challenge 触发

challenge 不再只由用户显式要求才启用：用户给出明确观点且存在能改变结论的有效反例时，可直接
指出薄弱假设 + 反例 + 建议收窄的范围，再问一个会改变结论的问题。没有用户立场时不能编造被挑战对象。

## 会话状态（仅推理用）

在当前 Codex 会话内维护，**不展示给用户**：

```yaml
topic: 当前话题
goal: explore | challenge
current_judgment: 用户明确说出的当前判断
confirmed_reasons: 用户提供的理由或经历
candidate_assumptions: 尚未确认的推测
open_gap: 当前最重要的缺口
last_extension: 上轮延伸点
```

## 节奏与结束

- 出现清楚判断 / 关键分歧 / 证据缺口，或用户说「总结一下」「先到这里」，立即结束并整理。
- 用户换方向时保留当前判断，切换到新角度；用户给新任务则直接执行新任务，不追旧问题。
- 连续两轮没有新增信息时，指出卡点并建议总结、查证或设计小实验。

结束输出为自然语言总结（当前观点 + 成立条件/剩余缺口 + 一个小验证建议），见
`references/dialogue-contract.md`。若用户没有形成判断，写明「尚未形成」，不替用户补结论。

## 外部资料与真实人物

事实争议会显著改变结论时：

1. 先指出争议是什么；
2. 说明查证范围、预算与它能解决的分歧，询问用户是否查证；
3. 查证后区分外部证据和用户个人经验；
4. 带着查证结果恢复原问题。

模拟某位真实 Builder：只允许基于用户提供或已检索的公开材料构造「可能提出的角度」。输出须
标明这是练习假设，不代表本人；**不得**写入该 Builder 的 `PeerProfile`、`InteractionRecord`
或 `ConversationThread`。

## 转交（须用户明确意图）

| 用户说 | 转交 |
|---|---|
| 保存这个观点 / 形成观点候选 | `idea-discovery`（仍为 `proposed`；来源可标 `practice`） |
| 生成草稿 / 帮我写 | 先确认判断，再 `idea-to-draft`；完成讨论 ≠ 立场已确认 |
| 准备回复这条真实帖子 | `interaction-preparation` |
| 我好像没真正理解某个概念 | `feynman-practice` |
| 判断清楚了，把核心信息讲清楚 | `sticky-message` |

未明确要求时，只输出结束小结，不自动进入观点、草稿或互动流程。

## 硬约束

- 只把用户明确说出或明确认可的内容写入 `current_judgment`。
- 不得把 AI 论点写成用户已确认立场（见 `_shared/author-position.md`）。
- 讨论不得写入 `InteractionRecord`、`ConversationThread`、`PeerProfile` 或关系指标。
- 每轮最多一个问题；无关键分歧可零问题收尾。

## 参考

- `references/dialogue-contract.md` — 来源标识、结束检查、结束输出契约、关系隔离
- `references/turn-strategies.md` — 七种推进动作与反例
- `references/presentation.md` — 对话文案与结束小结形状
- `_shared/dialogue-policy.md` — 延伸点选择、授权边界、收束与停止条件
- `_shared/agent-presentation.md` — 完成后如何对用户说话
````

- [ ] **Step 2: 验收 grep** —— 旧约束消失：

```bash
grep -nE "4–6 轮|4-6 轮|每轮必须|每轮.*问题" skills/topic-dialogue/SKILL.md || echo "clean"
```

预期：`clean`

- [ ] **Step 3: 提交**

```bash
git add skills/topic-dialogue/SKILL.md
git commit -m "refactor(dialogue): rework topic-dialogue for analysis-first, fork-driven closure"
```

### Task 5: 改造 topic-dialogue 三个 references

**Files:**
- Modify: `skills/topic-dialogue/references/dialogue-contract.md`
- Modify: `skills/topic-dialogue/references/turn-strategies.md`
- Modify: `skills/topic-dialogue/references/presentation.md`

- [ ] **Step 1: `dialogue-contract.md`** —— 保留「三种内容来源」「何时可以说用户已经形成判断」「关系隔离」，把「结束输出契约」从「默认返回 YAML」改为「自然语言为默认，YAML 仅为内部整理/后续存储」。整体替换为：

````markdown
# topic-dialogue 对话契约

## 三种内容来源

讨论中出现的内容必须可区分，不得混写：

| 标签 | 含义 | 可写入 `current_judgment`？ |
|---|---|---|
| **用户观点** | 用户明确说出或明确认可的判断、理由、边界 | 可以 |
| **AI 推测** | Skill 提出的假设、反例、可能后果；尚未获用户认可 | 不可以 |
| **外部事实** | 检索或用户提供的公开材料、数据、他人陈述 | 不可以冒充用户亲历 |

呈现时：用户观点用用户原话或贴近原话复述；AI 推测用「如果…」「有一种可能是…」；外部事实标明
来源，并与个人经验分开。

## 何时可以说用户已经形成判断

仅当满足其一：

1. 用户主动说出可复述的判断句；或
2. Skill 复述后，用户明确认可（「对」「就是这个意思」「可以这样写」等）。

以下情况**不算**已形成判断：

- 用户只抛出疑问或「我还不确定」；
- 只有 AI 提出的候选结论，用户未表态；
- 用户认可了某个理由，但未认可整体结论。

未形成时，结束总结中写明「尚未形成」，并保留真正的分歧或开放问题；**不得**替用户补结论。

## 结束检查触发时机

出现以下任一情况时，进入结束整理：

- 已有清楚判断、关键分歧或证据缺口；
- 用户说「总结一下」「先到这里」「形成观点」「保存下来」等；
- 连续两轮没有新增信息（卡点）；
- 用户切换话题并要求先收束当前讨论。

用户要求换方向但未要求结束：保留当前判断字段，切换 `topic` / 角度，不强制总结。

## 结束输出契约

**默认给自然语言总结**（当前观点 + 成立条件/剩余缺口 + 一个小验证建议），保持简短，尽量沿用
用户原话。结构化的内部整理（供 P1 摘要存储）才用以下 YAML 形状，不默认展示给用户：

```yaml
current_judgment: 用户现在认可的判断  # 或「尚未形成」
reasoning:
  - 关键理由或亲身经历
changed_during_dialogue: 讨论中修正、收窄或强化的地方
open_gap:
  type: evidence | boundary | concept | none
  detail: 仍不确定的关键问题
next_step:
  type: continue | verify | experiment | save_idea | draft | prepare_interaction | stop
  detail: 可选下一步
```

## 关系隔离（硬约束）

| 禁止 | 允许 |
|---|---|
| 写入 `InteractionRecord` | 会话内推理状态（P1 起落盘到 `dialogue/`） |
| 写入 `ConversationThread` | 结束自然语言总结展示给用户 |
| 更新 `PeerProfile` | 用户确认后，由用户触发 `idea-discovery` 等 |
| 计入关系 / 连接北极星指标 | 标明练习假设的模拟角度讨论 |

模拟某位真实 Builder 的讨论角度：基于用户提供或已检索的公开材料；输出须标明「练习假设，不代表本人」。

## 与作者立场

`current_judgment` 是练习上下文，不是已确认的 `AuthorIdea`。进入观点链路前：

1. 用户明确说保存 / 形成观点；
2. 走 `idea-discovery`（或现有观点入口），状态仍为 `proposed`；
3. 用户再 `finch ideas confirm` 后才算确认立场。

完成讨论 ≠ 立场已确认；不得把 AI 论点写入用户确认立场（见 `_shared/author-position.md`）。
````

- [ ] **Step 2: `turn-strategies.md`** —— 只改「选择提示」里 `challenge` 一行与「三类坏体验」里「过早总结」一处，去掉机械轮次暗示。用 Edit 做两处替换：

替换 A（`challenge` 行，原文在「选择提示」列表第 5 条）：

```markdown
5. 用户要求挑战，或关键假设可被反例动摇 → 提出反例
```
→
```markdown
5. 用户给出明确观点且存在能改变结论的反例，或用户要求挑战 → 提出反例
```

替换 B（`过早总结` 行，去掉「默认讨论到有判断/分歧/缺口」的轮次暗示，改为按分歧收束）：

```markdown
| 过早总结 | 第 1–2 轮就输出结束 YAML 或替用户下结论 | 默认讨论到有判断/分歧/缺口；用户要求才提前结束；未形成则写「尚未形成」 |
```
→
```markdown
| 过早总结 | 刚开场就替用户下结论或抛完整结构化总结 | 有清楚判断/分歧/缺口才收束；用户要求才提前结束；未形成则写「尚未形成」 |
```

（末尾两行 `explore` / `challenge` 的模式说明保持不变。）

- [ ] **Step 3: `presentation.md`** —— 把「结束小结」从「仅结构化 YAML」改为「默认自然语言，YAML 仅内部整理」。用 Edit 替换「## 结束小结」一节：

原文：

````markdown
## 结束小结

仅在结束检查触发时使用结构化输出（YAML）。对话中途不要提前甩出完整契约块。

```yaml
current_judgment: …
reasoning:
  - …
changed_during_dialogue: …
open_gap:
  type: evidence | boundary | concept | none
  detail: …
next_step:
  type: continue | verify | experiment | save_idea | draft | prepare_interaction | stop
  detail: …
```

小结后用一两句口语说明可选下一步，例如：「若要保存为观点候选，直接说；若先停在这里也可以。」不要自动调用其他 Skill 的 CLI。
````

替换为：

````markdown
## 结束小结

默认给自然语言总结（当前观点 + 成立条件/剩余缺口 + 一个小验证建议），对话中途不要提前抛出
完整契约块。结构化 YAML（见 `dialogue-contract.md`）仅在内部整理、或 P1 摘要存储时使用，
不默认展示给用户。

小结后用一两句口语说明可选下一步，例如：「若要保存为观点候选，直接说；若先停在这里也可以。」
不要自动调用其他 Skill 的 CLI。
````

- [ ] **Step 4: 验收 grep**：

```bash
grep -rnE "4–6 轮|4-6 轮|每轮必须" skills/topic-dialogue/ || echo "clean"
```

预期：`clean`

- [ ] **Step 5: 提交**

```bash
git add skills/topic-dialogue/references/
git commit -m "refactor(dialogue): natural-language closure, keep YAML as internal structure"
```

### Task 6: 新增验收用例 `skills/topic-dialogue/evals/cases.yaml`

**Files:**
- Create: `skills/topic-dialogue/evals/cases.yaml`

- [ ] **Step 1: 写文件**（18 个场景；`prohibited` 为禁止行为，`expected` 为必须观察到的结果）：

```yaml
# topic-dialogue 行为验收：人工回放或模型辅助评审，不靠字符串测试断言体验。
# 每例：scenario（场景）/ context（上下文）/ expected（必须出现）/ prohibited（禁止出现）。
cases:
  - scenario: 有效推荐任务
    context: 用户要求 peer-discovery 跑连接主循环并给出推荐
    expected: 先完整交付名单，再分析最多一个延伸点
    prohibited: 内部技术 ID 出现在前台标题；一次抛多个延伸点
  - scenario: 用户要求 50 人浏览
    context: 用户要求 connect daily --view browse 看 50 人
    expected: 交付完整 50 人列表；讲解简洁
    prohibited: 因「两三段」省略交付；生成 50 个讨论问题
  - scenario: 明确观点且有反例
    context: 用户说出明确判断，且存在能改变结论的反例
    expected: 指出具体假设与反例关联，再提一个会影响结论的问题
    prohibited: 只说「也有人反对」而无关联
  - scenario: 只有设想反例
    context: 没有真实反例，只有构造情境
    expected: 明确标注「假设……」的假设性
    prohibited: 把构造情境说成已发生案例
  - scenario: 没有价值延伸
    context: 任务完成后无值得讨论的线索
    expected: 自然结束
    prohibited: 机械问「还需要什么帮助」
  - scenario: 连续讨论已无分歧
    context: 两轮后已无关键分歧
    expected: 两轮即可收束
    prohibited: 硬凑 4–6 轮
  - scenario: 用户只赞同一条理由
    context: 用户只认可其中一个理由
    expected: 只把该理由记为已认可
    prohibited: 把整套观点标成用户确认
  - scenario: 新争议需要检索
    context: 延伸出的争议需要外部查证
    expected: 提出具体查证计划（缺口/范围/预算/影响）
    prohibited: 用户同意前发起新的网络调用
  - scenario: 原任务明确要求搜索
    context: 原请求就是要搜索/刷新
    expected: 直接完成授权范围内搜索
    prohibited: 重复问许可
  - scenario: 用户同意查证
    context: 用户对查证计划说「查一下」
    expected: 只执行该查证范围
    prohibited: 失败时编造证据或声称已解决
  - scenario: 用户拒绝/换话题
    context: 用户拒绝查证或转移话题
    expected: 停止待查证
    prohibited: 换工具继续或纠缠旧问题
  - scenario: 历史观点似乎矛盾
    context: 过去观点与现在不同
    expected: 引用真实记录，先比较成立条件，再请用户确认变化
    prohibited: 凭同关键词认定自相矛盾
  - scenario: 新会话没有历史
    context: 没有可读历史
    expected: 明说无法确认
    prohibited: 虚构「你上次说过」
  - scenario: 保存讨论摘要
    context: 讨论收束后落盘摘要
    expected: 摘要进入 dialogue/，来源区分
    prohibited: 写入 PeerProfile/InteractionRecord/ConversationThread 或改变关系指标
  - scenario: 建议最小实验
    context: 收束时给出小验证建议
    expected: 有行动 + 观察信号 + 什么会推翻假设
    prohibited: 用户未选择就执行或记录成功
  - scenario: 机器读取与展示
    context: JSON 读取 vs 实际展示
    expected: JSON 读取不计曝光；实际展示只记对应项目；换一批不重复
    prohibited: 把所有 JSON 读取都算曝光
  - scenario: 过期或失败结果
    context: 结果过期或部分失败
    expected: 新鲜度/失败范围保留
    prohibited: 因语言润色隐藏新鲜度或失败
  - scenario: 重试与修订冲突
    context: 保存重试或旧版本覆盖
    expected: 同 checkpoint 幂等；旧版本拒绝覆盖；坏文件不静默当无历史
    prohibited: 重复追加同一 checkpoint；静默覆盖旧版本
```

- [ ] **Step 2: 提交**

```bash
git add skills/topic-dialogue/evals/cases.yaml
git commit -m "test(dialogue): add behavioral acceptance cases"
```

### Task 7: 业务 Skill 接线（第一批 4 个）

**Files（各在其 SKILL.md 做同一处插入）：**
- Modify: `skills/peer-discovery/SKILL.md`
- Modify: `skills/community-scout/SKILL.md`
- Modify: `skills/idea-discovery/SKILL.md`
- Modify: `skills/weekly-reflection/SKILL.md`

- [ ] **Step 1: 对每个文件做两处小插入**

每个 SKILL.md 现有正文里都有一行类似 `见 `_shared/agent-presentation.md` 与 `references/presentation.md``。在该行之后紧接插入一句：

```markdown
完成后按 `_shared/dialogue-policy.md` 做一次延伸点检查（无有效点就自然结束），延伸不得抢占交付物。
```

并在该文件末尾「参考」列表里补一行：

```markdown
- `_shared/dialogue-policy.md` — 任务后延伸点选择、授权边界与收束
```

（以 `skills/peer-discovery/SKILL.md` 为例：正文第 43 行「见 `_shared/agent-presentation.md` 与 `references/presentation.md`。」之后插入上述句子；末尾「参考」区第 70–71 行处补一行。其余三文件依各自对应行号照做。）

- [ ] **Step 2: 验收 grep**：

```bash
grep -l "_shared/dialogue-policy.md" skills/peer-discovery/SKILL.md skills/community-scout/SKILL.md skills/idea-discovery/SKILL.md skills/weekly-reflection/SKILL.md
```

预期：列出全部 4 个文件。

- [ ] **Step 3: 提交**

```bash
git add skills/peer-discovery/SKILL.md skills/community-scout/SKILL.md skills/idea-discovery/SKILL.md skills/weekly-reflection/SKILL.md
git commit -m "feat(dialogue): wire extension-check into first batch of business skills"
```

### Task 8: 业务 Skill 接线（第二批 4 个）+ README

**Files:**
- Modify: `skills/interaction-preparation/SKILL.md`
- Modify: `skills/conversation-follow-up/SKILL.md`
- Modify: `skills/idea-to-draft/SKILL.md`
- Modify: `skills/voice-profile/SKILL.md`
- Modify: `README.md`

- [ ] **Step 1: 对 4 个 SKILL.md 做与 Task 7 完全相同的两处插入**（正文 presentation 行后 + 末尾参考区）。

- [ ] **Step 2: README.md** —— 增加「自然语言用法」短节（放在 CLI 命令列表之后）：

```markdown
## 自然语言用法

- 「只看结果」——完成当前任务，不做延伸讨论。
- 「继续讨论」——回应 Finch 上轮延伸问题，进入连续讨论。
- 「查看依据」——要求展示某条结论的来源。
- 「先不查」——停止待查证动作。
- 「别记这段」——跳过当前讨论摘要的保存。
```

- [ ] **Step 3: 验收 grep**：

```bash
grep -l "_shared/dialogue-policy.md" skills/interaction-preparation/SKILL.md skills/conversation-follow-up/SKILL.md skills/idea-to-draft/SKILL.md skills/voice-profile/SKILL.md
```

预期：列出全部 4 个文件。

- [ ] **Step 4: 提交**

```bash
git add skills/interaction-preparation/SKILL.md skills/conversation-follow-up/SKILL.md skills/idea-to-draft/SKILL.md skills/voice-profile/SKILL.md README.md
git commit -m "feat(dialogue): wire extension-check into second batch + README usage"
```

### Task 9: 安装路径可达性验收

**Files:**
- 无代码改动；验证共享文件在仓库与安装后两条路径都可读。

- [ ] **Step 1: 仓库内相对路径可达性**

```bash
cd /Users/lingjiefan/underway/Finch && for f in skills/_shared/dialogue-policy.md skills/_shared/agent-presentation.md; do test -f "$f" && echo "ok: $f" || echo "MISSING: $f"; done
```

预期：两行 `ok:`。

- [ ] **Step 2: 确认安装方式会同步 `_shared`**（若 Finch 用目录复制安装到 Codex 的 skills 目录，则 `_shared` 必须一并复制）。检查当前安装说明：

```bash
grep -rn "_shared\|install\|安装\|skills/" README.md | head -20
```

若 README/安装脚本未包含 `_shared`，在 README 的安装说明补一句「`skills/_shared/` 须与各 skill 一并安装（相对路径 `_shared/...` 以 skill 所在目录为基准解析）」。

- [ ] **Step 3: 提交（若有 README 改动）**

```bash
git add README.md && git commit -m "docs: note _shared must ship with installed skills" || echo "no change"
```

---

## Phase P1-A — 讨论摘要与历史关联（TDD）

P1-A 完成标准：`finch dialogue save/search/show/forget` 可用，幂等 + revision 冲突 + 来源隔离都正确。

### Task 10: `dialogue/models.py`

**Files:**
- Create: `src/finch/dialogue/__init__.py`
- Create: `src/finch/dialogue/models.py`
- Test: `tests/unit/test_dialogue_memory.py`

**Interfaces:**
- Produces: `PositionStatus`（`TENTATIVE/USER_CONFIRMED/UNRESOLVED`）、`OriginRef(type, ref)`、`SuggestedValidation(action, signal, falsifies)`、`DialogueCheckpoint`、`DialogueNote`。后续 task 依赖这些类型名。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_dialogue_memory.py` 开头：

```python
"""dialogue 记忆：模型 / 仓库 / 服务（幂等 + revision 冲突 + 来源隔离）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from finch.dialogue.models import (
    DialogueCheckpoint,
    DialogueNote,
    PositionStatus,
)
from finch.dialogue.repository import DialogueRepository
from finch.dialogue.service import DialogueService, DialogueServiceError
from finch.storage.workspace import Workspace


def _checkpoint(checkpoint_id: str, *, position: str = "先做 Skill 再代码化") -> DialogueCheckpoint:
    return DialogueCheckpoint(
        checkpoint_id=checkpoint_id,
        user_position=position,
        position_status=PositionStatus.TENTATIVE,
        conditions=["流程稳定之前"],
    )


def _note(note_id: str = "dlg_test", topic_key: str = "skill-to-code") -> DialogueNote:
    return DialogueNote(
        id=note_id,
        topic="skill 代码化",
        topic_key=topic_key,
        checkpoints=[_checkpoint("cp_1")],
    )


def test_note_serializes_to_json_with_enum():
    note = _note()
    payload = json.loads(note.model_dump_json())
    assert payload["id"] == "dlg_test"
    assert payload["checkpoints"][0]["position_status"] == "tentative"
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/unit/test_dialogue_memory.py::test_note_serializes_to_json_with_enum -v
```

预期：FAIL（`ModuleNotFoundError: finch.dialogue`）

- [ ] **Step 3: 写实现**

`src/finch/dialogue/__init__.py`：

```python
"""讨论摘要记忆（薄持久化）：区分用户立场 / AI 推测 / 未解决问题。"""
```

`src/finch/dialogue/models.py`：

```python
"""讨论摘要记忆领域模型（薄持久化，区分用户立场 / AI 推测 / 未解决问题）。

一个 ``DialogueNote`` 对应一个讨论主题；``checkpoints`` 追加不覆盖，当前立场由最后一条
checkpoint 表达。``position_status=user_confirmed`` 只描述「讨论中的认同程度」，不等于
``ContentJobStatus.CONFIRMED``，也不等于观点被事实证实；写草稿继续走 idea 链路门禁。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class PositionStatus(StrEnum):
    """讨论中的立场认同程度（非内容发布语义）。"""

    TENTATIVE = "tentative"
    USER_CONFIRMED = "user_confirmed"
    UNRESOLVED = "unresolved"


class OriginRef(BaseModel):
    """讨论主题的来源引用（任务 / 社区卡 / Opportunity / 已有 idea）。"""

    type: str
    ref: str


class SuggestedValidation(BaseModel):
    """建议的最小验证行动（仅建议，不自动执行）。"""

    action: str
    signal: str
    falsifies: str


class DialogueCheckpoint(BaseModel):
    """一次有意义的收束摘要。``checkpoint_id`` 是调用方重试的幂等键。"""

    checkpoint_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    user_position: str = ""
    position_status: PositionStatus = PositionStatus.UNRESOLVED
    confirmation_quote: str = ""
    conditions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    assistant_hypotheses: list[str] = Field(default_factory=list)
    suggested_validation: SuggestedValidation | None = None
    change_reason: str = ""
    supersedes_checkpoint_id: str | None = None


class DialogueNote(BaseModel):
    """一个讨论主题的记录。``id`` 由宿主首次保存时给出（opaque），``topic_key`` 是
    稳定的检索键（只召回，不自动合并相似观点）。"""

    id: str
    schema_version: int = 1
    revision: int = 1
    topic: str
    topic_key: str
    origin_refs: list[OriginRef] = Field(default_factory=list)
    checkpoints: list[DialogueCheckpoint] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/unit/test_dialogue_memory.py::test_note_serializes_to_json_with_enum -v
```

预期：PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/dialogue/__init__.py src/finch/dialogue/models.py tests/unit/test_dialogue_memory.py
git commit -m "feat(dialogue): add DialogueNote model"
```

### Task 11: `dialogue/repository.py`

**Files:**
- Create: `src/finch/dialogue/repository.py`
- Test: `tests/unit/test_dialogue_memory.py`（追加）

**Interfaces:**
- Consumes: `DialogueNote`（Task 10）
- Produces: `DialogueRepository(workspace)`，方法 `get(note_id) -> DialogueNote | None`、`save(note) -> None`、`delete(note_id) -> bool`、`list_all() -> list[DialogueNote]`。

- [ ] **Step 1: 写失败测试**（追加到 `test_dialogue_memory.py`）：

```python
def test_repository_roundtrip_and_list(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = DialogueRepository(ws)

    repo.save(_note("dlg_a", "a"))
    repo.save(_note("dlg_b", "b"))

    assert repo.get("dlg_a").topic_key == "a"
    assert {n.id for n in repo.list_all()} == {"dlg_a", "dlg_b"}


def test_repository_delete(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = DialogueRepository(ws)

    repo.save(_note("dlg_a", "a"))
    assert repo.delete("dlg_a") is True
    assert repo.get("dlg_a") is None
    assert repo.delete("dlg_a") is False
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/unit/test_dialogue_memory.py::test_repository_roundtrip_and_list -v
```

预期：FAIL（`ImportError: cannot import name 'DialogueRepository'`）

- [ ] **Step 3: 写实现**

`src/finch/dialogue/repository.py`：

```python
"""讨论摘要仓库（文件工作区；一个 note 一个 YAML，原子写）。"""

from __future__ import annotations

from pathlib import Path

from finch.dialogue.models import DialogueNote
from finch.storage.workspace import Workspace


class DialogueRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("dialogue")

    def _path(self, note_id: str) -> Path:
        return self._dir / f"{self.ws.safe_filename(note_id)}.yaml"

    def get(self, note_id: str) -> DialogueNote | None:
        return self.ws.read_yaml(self._path(note_id), DialogueNote)

    def save(self, note: DialogueNote) -> None:
        self.ws.write_yaml(self._path(note.id), note)

    def delete(self, note_id: str) -> bool:
        path = self._path(note_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def list_all(self) -> list[DialogueNote]:
        out: list[DialogueNote] = []
        for path in sorted(self._dir.glob("*.yaml")):
            note = self.ws.read_yaml(path, DialogueNote)
            if note is not None:
                out.append(note)
        return out
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/unit/test_dialogue_memory.py::test_repository_roundtrip_and_list tests/unit/test_dialogue_memory.py::test_repository_delete -v
```

预期：PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/dialogue/repository.py tests/unit/test_dialogue_memory.py
git commit -m "feat(dialogue): add DialogueRepository"
```

### Task 12: `dialogue/service.py`（保存/检索/删除 + 幂等 + 冲突）

**Files:**
- Create: `src/finch/dialogue/service.py`
- Test: `tests/unit/test_dialogue_memory.py`（追加）

**Interfaces:**
- Consumes: `DialogueNote`（Task 10）、`DialogueRepository`（Task 11）
- Produces: `DialogueService(workspace)`，方法 `save(incoming, *, expected_revision) -> DialogueNote`（0=创建，>0=要求当前 revision 匹配；按 `checkpoint_id` 去重追加）、`show(note_id)`、`forget(note_id) -> bool`、`search(query, *, limit=3) -> list[DialogueNote]`；异常 `DialogueServiceError`。

- [ ] **Step 1: 写失败测试**（追加）：

```python
def test_save_creates_then_appends_bumping_revision(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = DialogueService(ws)

    created = svc.save(_note("dlg_a", "a"), expected_revision=0)
    assert created.revision == 1

    update = DialogueNote(
        id="dlg_a", topic="skill 代码化", topic_key="a",
        checkpoints=[_checkpoint("cp_2", position="改：流程稳定前先不代码化")],
    )
    updated = svc.save(update, expected_revision=1)
    assert updated.revision == 2
    assert [c.checkpoint_id for c in updated.checkpoints] == ["cp_1", "cp_2"]


def test_save_idempotent_on_same_checkpoint(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = DialogueService(ws)

    created = svc.save(_note("dlg_a", "a"), expected_revision=0)
    retry = svc.save(_note("dlg_a", "a"), expected_revision=1)
    assert retry.revision == 1
    assert len(retry.checkpoints) == 1


def test_save_revision_conflict_raises(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = DialogueService(ws)

    svc.save(_note("dlg_a", "a"), expected_revision=0)
    with pytest.raises(DialogueServiceError):
        svc.save(_note("dlg_a", "a"), expected_revision=0)


def test_save_create_conflict_when_id_exists(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = DialogueService(ws)

    svc.save(_note("dlg_a", "a"), expected_revision=0)
    with pytest.raises(DialogueServiceError):
        svc.save(
            DialogueNote(
                id="dlg_a", topic="另一个主题", topic_key="a",
                checkpoints=[_checkpoint("cp_9")],
            ),
            expected_revision=0,
        )


def test_search_matches_topic_key_and_limits(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = DialogueService(ws)

    svc.save(_note("dlg_a", "skill-to-code"), expected_revision=0)
    svc.save(_note("dlg_b", "community-entry"), expected_revision=0)

    hits = svc.search("skill", limit=3)
    assert [n.id for n in hits] == ["dlg_a"]


def test_show_and_forget(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = DialogueService(ws)

    svc.save(_note("dlg_a", "a"), expected_revision=0)
    assert svc.show("dlg_a").topic_key == "a"
    assert svc.show("missing") is None
    assert svc.forget("dlg_a") is True
    assert svc.forget("dlg_a") is False
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/unit/test_dialogue_memory.py::test_save_creates_then_appends_bumping_revision -v
```

预期：FAIL（`ImportError: cannot import name 'DialogueService'`）

- [ ] **Step 3: 写实现**

`src/finch/dialogue/service.py`：

```python
"""DialogueService：保存（幂等追加 + revision 冲突）/ 检索 / 删除。

单用户串行写入假设：revision 检查只防「重读后覆盖」，不提供多进程事务保证。
``save`` 失败只影响记忆保存，不阻断当前对话（由调用方告知「尚未保存」并重试）。
"""

from __future__ import annotations

from datetime import datetime

from finch.dialogue.models import DialogueNote
from finch.dialogue.repository import DialogueRepository
from finch.storage.workspace import Workspace


class DialogueServiceError(ValueError):
    """保存冲突或校验失败。"""


class DialogueService:
    def __init__(self, workspace: Workspace) -> None:
        self.repo = DialogueRepository(workspace)

    def save(self, incoming: DialogueNote, *, expected_revision: int) -> DialogueNote:
        """创建（``expected_revision==0``）或更新一条 note。

        - 创建：``id`` 已存在则冲突。
        - 更新：``revision`` 不符则冲突；按 ``checkpoint_id`` 去重追加，
          不重复追加已存在的 checkpoint（幂等重试返回现状，不 bump revision）。
        - 更新时身份字段（topic / topic_key / origin_refs）沿用已有值，忽略入参。
        """
        existing = self.repo.get(incoming.id)
        if expected_revision == 0:
            if existing is not None:
                raise DialogueServiceError(
                    f"note already exists: {incoming.id} "
                    f"(current revision {existing.revision})"
                )
            note = incoming.model_copy(update={"revision": 1})
            self.repo.save(note)
            return note
        if existing is None:
            raise DialogueServiceError(
                f"note not found: {incoming.id} "
                f"(expected revision {expected_revision})"
            )
        if existing.revision != expected_revision:
            raise DialogueServiceError(
                f"revision conflict: expected {expected_revision}, "
                f"current {existing.revision}"
            )
        known = {c.checkpoint_id for c in existing.checkpoints}
        new_checkpoints = [c for c in incoming.checkpoints if c.checkpoint_id not in known]
        if not new_checkpoints:
            return existing
        note = existing.model_copy(
            update={
                "checkpoints": [*existing.checkpoints, *new_checkpoints],
                "revision": existing.revision + 1,
            }
        )
        self.repo.save(note)
        return note

    def show(self, note_id: str) -> DialogueNote | None:
        return self.repo.get(note_id)

    def forget(self, note_id: str) -> bool:
        return self.repo.delete(note_id)

    def search(self, query: str, *, limit: int = 3) -> list[DialogueNote]:
        """关键词召回：query 分词匹配 topic / topic_key / checkpoint 文本，按
        （命中数降序，created_at 降序）返回最多 ``limit`` 条。读取不调用网络。"""
        tokens = [t for t in query.lower().split() if t]
        if not tokens:
            return []
        scored: list[tuple[int, datetime, DialogueNote]] = []
        for note in self.repo.list_all():
            haystack = f"{note.topic} {note.topic_key}".lower()
            for cp in note.checkpoints:
                haystack += f" {cp.user_position.lower()}"
                haystack += f" {' '.join(cp.conditions).lower()}"
                haystack += f" {' '.join(cp.open_questions).lower()}"
            score = sum(1 for t in tokens if t in haystack)
            if score > 0:
                scored.append((score, note.created_at, note))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [note for _, _, note in scored[:limit]]
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/unit/test_dialogue_memory.py -v
```

预期：全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/dialogue/service.py tests/unit/test_dialogue_memory.py
git commit -m "feat(dialogue): add DialogueService with idempotent save and revision conflict"
```

### Task 13: CLI `finch dialogue`

**Files:**
- Modify: `src/finch/cli.py`（注册 sub-app + 4 个命令 + 导入）
- Test: `tests/unit/test_cli_dialogue.py`

**Interfaces:**
- Consumes: `DialogueService`、`DialogueNote`（Task 10/12）
- Produces: `finch dialogue save/search/show/forget`。`save --file note.json --expected-revision N --json`；JSON 成功返回 note 全文，冲突返回 `{"ok": false, "error": ...}` 且 exit code 1。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_cli_dialogue.py`：

```python
"""Unit tests for the finch dialogue CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from finch import cli
from finch.cli import app
from finch.settings import Paths, Settings


def _settings(tmp_path) -> Settings:
    return Settings(paths=Paths(var_dir=tmp_path))


def _note_json(tmp_path, note_id: str = "dlg_a", topic_key: str = "skill-to-code") -> Path:
    p = tmp_path / "note.json"
    p.write_text(
        json.dumps(
            {
                "id": note_id,
                "topic": "skill 代码化",
                "topic_key": topic_key,
                "checkpoints": [
                    {
                        "checkpoint_id": "cp_1",
                        "user_position": "先做 Skill 再代码化",
                        "position_status": "tentative",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return p


def test_cli_save_show_search_forget(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    note = _note_json(tmp_path)

    r = CliRunner().invoke(app, ["dialogue", "save", "--file", str(note), "--expected-revision", "0", "--json"])
    assert r.exit_code == 0, r.output
    saved = json.loads(r.output)
    assert saved["id"] == "dlg_a"
    assert saved["revision"] == 1

    r = CliRunner().invoke(app, ["dialogue", "show", "dlg_a", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["checkpoints"][0]["checkpoint_id"] == "cp_1"

    r = CliRunner().invoke(app, ["dialogue", "search", "skill", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)[0]["id"] == "dlg_a"

    r = CliRunner().invoke(app, ["dialogue", "forget", "dlg_a", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["ok"] is True


def test_cli_save_conflict_returns_error(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_settings", lambda: _settings(tmp_path))
    note = _note_json(tmp_path)

    assert CliRunner().invoke(app, ["dialogue", "save", "--file", str(note), "--expected-revision", "0"]).exit_code == 0
    r = CliRunner().invoke(app, ["dialogue", "save", "--file", str(note), "--expected-revision", "0", "--json"])
    assert r.exit_code == 1
    assert json.loads(r.output)["ok"] is False
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/unit/test_cli_dialogue.py::test_cli_save_show_search_forget -v
```

预期：FAIL（`No such command 'dialogue'`）

- [ ] **Step 3: 写实现**

（1）`src/finch/cli.py` 顶部导入区，在 `.content.voice` 导入之后新增：

```python
from .dialogue.models import DialogueNote
from .dialogue.service import DialogueService, DialogueServiceError
```

（2）在 `community_app` 注册（第 168–169 行）之后新增 sub-app：

```python
dialogue_app = typer.Typer(help="讨论摘要记忆（薄持久化，可检索；命令由 Skill 使用）")
app.add_typer(dialogue_app, name="dialogue")
```

（3）在文件末尾 `if __name__ == "__main__":` 之前新增 4 个命令：

```python
@dialogue_app.command("save")
def dialogue_save(
    file: Path = typer.Option(..., "--file", help="note.json（完整 DialogueNote）"),
    expected_revision: int = typer.Option(0, "--expected-revision", help="0=创建，>0=要求当前 revision 匹配"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """创建或追加一条讨论摘要（薄持久化；幂等 + revision 冲突）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    incoming = DialogueNote.model_validate_json(file.read_text(encoding="utf-8"))
    try:
        note = DialogueService(ws).save(incoming, expected_revision=expected_revision)
    except DialogueServiceError as exc:
        if as_json:
            typer.echo(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            typer.echo(f"error: {exc}")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(json.dumps(note.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"saved {note.id} revision={note.revision}")


@dialogue_app.command("search")
def dialogue_search(
    query: str = typer.Argument(..., help="检索关键词（topic_key / 主题 / 摘要文本）"),
    limit: int = typer.Option(3, "--limit", help="最多返回条数"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """关键词召回讨论摘要（最多 limit 条，读取不调用网络）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    notes = DialogueService(ws).search(query, limit=limit)
    if as_json:
        typer.echo(json.dumps([n.model_dump(mode="json") for n in notes], ensure_ascii=False, indent=2))
    else:
        for n in notes:
            latest = n.checkpoints[-1] if n.checkpoints else None
            typer.echo(f"{n.id}\t{n.topic_key}\t{latest.position_status.value if latest else 'unresolved'}")


@dialogue_app.command("show")
def dialogue_show(
    note_id: str = typer.Argument(..., help="note id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """读取单条讨论摘要。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    note = DialogueService(ws).show(note_id)
    if note is None:
        if as_json:
            typer.echo(json.dumps({"ok": False, "error": "not found"}, ensure_ascii=False))
        else:
            typer.echo("not found")
        raise typer.Exit(code=1)
    if as_json:
        typer.echo(json.dumps(note.model_dump(mode="json"), ensure_ascii=False, indent=2))
    else:
        typer.echo(f"{note.topic}\n  topic_key={note.topic_key} revision={note.revision}")


@dialogue_app.command("forget")
def dialogue_forget(
    note_id: str = typer.Argument(..., help="note id"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """删除单条讨论摘要。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    ok = DialogueService(ws).forget(note_id)
    if as_json:
        typer.echo(json.dumps({"ok": ok}, ensure_ascii=False))
    else:
        typer.echo("deleted" if ok else "not found")
    if not ok:
        raise typer.Exit(code=1)
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/unit/test_cli_dialogue.py -v
```

预期：全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/cli.py tests/unit/test_cli_dialogue.py
git commit -m "feat(dialogue): add finch dialogue CLI"
```

### Task 14: `connect daily --json` 补新鲜度字段

**Files:**
- Modify: `src/finch/cli.py`（`connect_daily` 的 `as_json` 分支）
- Test: `tests/unit/test_cli_connect.py`（追加）

**Interfaces:**
- Consumes: `_snapshot_fresh`（cli.py 内已有）
- Produces: `connect daily --json` 新增 `snapshot_created_at`（ISO 字符串或 null）、`stale`（bool）、`refresh_status`（`refreshed|stale|fresh`）。既有键保持不变。

- [ ] **Step 1: 写失败测试**（追加到 `test_cli_connect.py`）：

```python
def test_connect_daily_json_includes_freshness(monkeypatch, tmp_path):
    from finch import cli

    monkeypatch.setattr(cli, "load_settings", lambda: Settings(paths=Paths(var_dir=tmp_path)))
    # 无快照时 snapshot_created_at 为 None、stale 为 False、refresh_status 为 fresh
    r = CliRunner().invoke(app, ["connect", "daily", "--json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    assert "snapshot_created_at" in payload
    assert "stale" in payload
    assert "refresh_status" in payload
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/unit/test_cli_connect.py::test_connect_daily_json_includes_freshness -v
```

预期：FAIL（`KeyError: 'snapshot_created_at'` 或断言失败）

- [ ] **Step 3: 写实现** —— 在 `connect_daily` 的 `as_json` 分支（约 `src/finch/cli.py:2448` 的 `typer.echo(json.dumps({...}))`）的 dict 里，`"schema_version": 2,` 之后新增三个键：

```python
            "snapshot_created_at": (
                snapshot.created_at.isoformat() if snapshot else None
            ),
            "stale": stale,
            "refresh_status": (
                "refreshed" if need_refresh else ("stale" if stale else "fresh")
            ),
```

（注意：`stale` 与 `need_refresh` 变量已在函数体前段定义，无需新增。）

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/unit/test_cli_connect.py::test_connect_daily_json_includes_freshness -v
```

预期：PASS

- [ ] **Step 5: 提交**

```bash
git add src/finch/cli.py tests/unit/test_cli_connect.py
git commit -m "feat(connect): expose snapshot freshness in daily --json"
```

### Task 15: 显式呈现记录入口 `connect record-presented`

**Files:**
- Modify: `src/finch/cli.py`（新增命令，复用 `_record_presentations` / `_record_person_presentations`）
- Test: `tests/unit/test_cli_connect.py`（追加）

**Interfaces:**
- Consumes: `_record_presentations(ws, snapshot_id, opportunity_ids)`、`_record_person_presentations(ws, snapshot_id, entries, surface=...)`（cli.py 内已有）、`DiscoverySnapshotRepository(ws).latest()`。
- Produces: `finch connect record-presented --snapshot-id <id> --surface home|browse [--person-id ...] [--opportunity-id ...] --json`。只记录实际展示的项目；校验 snapshot 为最新；JSON 读取路径（`connect daily --json`）仍无曝光副作用。

- [ ] **Step 1: 写失败测试**（追加到 `test_cli_connect.py`）：

```python
def test_connect_record_presented_rejects_nonlatest_snapshot(monkeypatch, tmp_path):
    from finch import cli

    monkeypatch.setattr(cli, "load_settings", lambda: Settings(paths=Paths(var_dir=tmp_path)))
    r = CliRunner().invoke(
        app,
        ["connect", "record-presented", "--snapshot-id", "snap_nonexistent", "--json"],
    )
    assert r.exit_code == 1
    assert json.loads(r.output)["ok"] is False
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/unit/test_cli_connect.py::test_connect_record_presented_rejects_nonlatest_snapshot -v
```

预期：FAIL（`No such command 'record-presented'`）

- [ ] **Step 3: 写实现** —— 在 `connect_app` 命令区（`connect_person` 之后）新增：

```python
@connect_app.command("record-presented")
def connect_record_presented(
    snapshot_id: str = typer.Option(..., "--snapshot-id", help="快照 id"),
    surface: str = typer.Option("home", "--surface", help="home|browse"),
    person_ids: list[str] = typer.Option([], "--person-id", help="实际展示的 person_id（可重复）"),
    opportunity_ids: list[str] = typer.Option([], "--opportunity-id", help="实际展示的 opportunity_id（可重复）"),
    as_json: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """显式记录「已输出到答复」的 person/opportunity（JSON 驱动流；文本路径已自动记录）。"""
    settings = load_settings()
    ws = Workspace(settings.paths.var_dir)
    ws.ensure()
    snapshot = DiscoverySnapshotRepository(ws).latest()
    if snapshot is None or snapshot.id != snapshot_id:
        if as_json:
            typer.echo(json.dumps({"ok": False, "error": "snapshot not found or not latest"}, ensure_ascii=False))
        else:
            typer.echo("snapshot not found or not latest")
        raise typer.Exit(code=1)
    wanted = set(person_ids)
    entries = [e for e in snapshot.recommendations if e.person_id in wanted]
    _record_person_presentations(ws, snapshot_id, entries, surface=surface)
    _record_presentations(ws, snapshot_id, opportunity_ids)
    payload = {"ok": True, "persons": len(entries), "opportunities": len(opportunity_ids)}
    if as_json:
        typer.echo(json.dumps(payload, ensure_ascii=False))
    else:
        typer.echo(f"recorded {payload['persons']} persons, {payload['opportunities']} opportunities")
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/unit/test_cli_connect.py -v
```

预期：全部 PASS（含既有曝光契约测试）。

- [ ] **Step 5: 提交**

```bash
git add src/finch/cli.py tests/unit/test_cli_connect.py
git commit -m "feat(connect): add explicit record-presented entry for JSON-driven flows"
```

### Task 16: 全量校验

- [ ] **Step 1: 运行完整测试、lint、类型**

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

- [ ] **Step 2: 区分基线失败与新增失败** —— 若出现失败，确认是否在 P0/P1 改动引入（对比 `git diff` 与失败文件）。本次改动不得引入新失败。

- [ ] **Step 3: 提交剩余遗漏**（若有）：

```bash
git status --short
```

---

## 执行顺序与提交边界

- P0（Task 1–9）为纯 markdown，每 task 一个 commit，P0 完成即可先跑一轮真实使用验证体验。
- P1-A（Task 10–13）与 P1-B（Task 14–15）为 Python，TDD 每 task 一个 commit。
- 若 P1-B 的 Task 15 在实现中与既有曝光契约测试冲突且无法完整落地，**保持原文本调用路径**，不把 Task 15 当作已完成优化（回退：不注册该命令即可，其他任务不受影响）。
- 回退 P1：停止 `finch dialogue` 入口与自动写摘要，保留 `dialogue/` 文件供恢复，不删除用户记录。

## Self-Review 记录

- **Spec 覆盖**：§2 十项决策 → Task 1（dialogue-policy）；§3 数量冲突 → Task 2；§4 读取路径 → Task 3、7、8、9；§5 每轮规则 → Task 1、4、5；§6 记忆模型 → Task 10–13；§7 CLI 呈现 → Task 14–15；§8 文件清单 → 各 Task 的 Files；§9 验收 → Task 6（cases.yaml）+ 各 TDD 测试；§10 回退 → 末尾执行顺序节。
- **Placeholder**：无 TBD/TODO；P0 markdown 给出全文，P1 给出完整代码。
- **类型一致性**：`DialogueNote`/`DialogueCheckpoint`/`PositionStatus`/`DialogueService`/`DialogueServiceError` 在 Task 10/12/13 命名一致；`save(incoming, *, expected_revision)` 签名一致；CLI 测试的 `monkeypatch.setattr(cli, "load_settings", ...)` 与现有 `test_cli_community.py` 模式一致。
