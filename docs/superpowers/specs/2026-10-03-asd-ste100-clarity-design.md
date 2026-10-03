# ASD-STE100 清晰表达：草稿编辑、训练与文章视角

日期：2026-10-03  
状态：待用户审阅  
来源计划：`~/Downloads/Finch-ASD-STE100-Optimization-Plan.md`  
对照：`skills/_shared/expression-contract.md`、`idea-to-draft`、`expression-practice`、`article_analysis`

## 1. 背景与问题

Finch 需要把「清晰、具体、容易理解」变成可执行的编辑要求，而不是口头偏好。官方 [ASD-STE100](https://www.asd-ste100.org/) 是技术文档的受控英语标准；本设计借鉴其降歧义、统一术语、明确动作的原则，并做中文适配。不声称严格符合官方版本。

仓库现状：

- `finch drafts revise --instruction` 按自然语言重写正文，只回写 `Draft.body`，无规则 ID / 语义检查 / 关键修改说明。
- Critic 八检查器管证据与安全，不是清晰度编辑语言。
- `expression-practice` 已有「用户先写」训练环；`article analyze` 已有表达任务分析；`sticky-message` 找核心信息，不改句子清晰度。

## 2. 决策记录

| # | 决策点 | 结论 |
|---|--------|------|
| D1 | 入口 | **不新增 CLI**；沿用 `finch drafts revise` |
| D2 | 何时应用规则 | **每次 `revise` 默认** `asd-ste100-inspired`；指令点名 technical 时切换 |
| D3 | 与用户指令冲突 | **分预设**：默认下指令可改手法；technical 下主体/动作/前置条件不被放宽；事实与原意始终优先 |
| D4 | 关键修改交付 | **扩展 `revise` 输出**（正文 + `ClarityReview`）；挂 Draft frontmatter，不新建表 |
| D5 | 交付范围 | **P0–P3**：规则落地、revise 扩展、短训练、文章清晰视角 |
| D6 | 架构形态 | **扩展现有 revise 契约**（一次结构化调用）；不建旁路 ClarityService、不多轮自评 |
| D7 | 共享规则位置 | **`skills/_shared/asd-ste100-inspired.md`**（技能与提示词唯一来源） |
| D8 | 训练 | 显式触发 → 现有 `expression-practice` / `practice`；不新建训练库 |
| D9 | 文章 | `ArticleReport.clarity_cost_reductions`（0–3）；不落新库、不重建采集 |

## 3. 目标与非目标

**目标**

- 共享规则（CL01–CL08、两预设、中文边界）可被技能与改写提示词引用。
- README / 相关技能 / 编辑提示词可检索到 `ASD-STE100`。
- 每次 `drafts revise` 产出改稿 + 最多三条关键修改（规则 ID）+ 语义检查 + 信息缺口，并写入 Draft。
- 显式「练习清晰表达」时走训练环：用户先改 → 反馈 → 参考版本。
- 文章分析可选呈现最多三种「降低理解成本」写法（片段 + 作用 + 练习）。

**非目标**

- 新 CLI 子命令、Graph Runtime、独立评分服务、Review 表、多轮自评循环。
- 官方 ASD-STE100 合规认证或完整受控词典。
- 中文字符数硬阈值；补造未提供的事实、数字、主体。
- 改 Critic 检查器组合、自动确认立场、自动发布。
- 无草稿时的旁路纯文本编辑命令（粘贴即改不在本次；须先有 draft）。

## 4. 预设与规则

| 预设 | 场景 | 行为 |
|---|---|---|
| `asd-ste100-inspired`（默认） | 中文草稿、社交帖、解释 | 局部编辑；保留故事/语气/有效比喻；修歧义、抽象与术语漂移 |
| `asd-ste100-technical` | 技术说明、步骤、指南 | 主体/动作/前置条件明确；一句操作优先一个指令；这些要求不被用户指令放宽 |

预设解析（确定性代码）：默认 inspired；`--instruction` 含 `asd-ste100-technical`、或明确「技术操作说明 / 操作步骤」类触发语时 → technical。不得输出「符合 ASD-STE100」声明。

### CL01–CL08（项目规则，非官方编号）

| ID | 规则 | 编辑行为 |
|---|---|---|
| CL01 | 一句话优先一个主要意思 | 拆开独立判断，保留必要因果 |
| CL02 | 主体、动作、对象明确 | 说明谁对什么做了什么；未知主体不编造 |
| CL03 | 同一概念一致名称 | 统一同义称呼，保留真实概念区别 |
| CL04 | 抽象判断有具体支撑 | 指出缺口，不凭空补数字 |
| CL05 | 前置条件靠近建议 | 先条件后动作/结论 |
| CL06 | 操作步骤可执行 | technical 下拆分指令，写明对象与必要检查 |
| CL07 | 一段一个主题 | 删重复铺垫，整理跳转 |
| CL08 | 简化后保持原意 | 保留否定、数字、范围、概率、条件、归属与不确定性 |

优先级：**事实与原意 > 清晰度 > 个性化语气 > 文案整齐**。VoiceProfile 可调语气节奏，不得重新引入歧义或强化证据不足的结论。

## 5. 组件与数据流

### 5.1 共享规则

唯一文件：`skills/_shared/asd-ste100-inspired.md`  
内容：出处链接、两预设、CL01–CL08、中文适配、禁止合规声称、安全改写示例。  
引用方（只链不抄）：`idea-to-draft`、`expression-practice`、`article_analysis`、`feynman-practice`、`sticky-message`、`expression-contract`（必要时一句指针）、README。

### 5.2 改稿路径

```
finch drafts revise <draft_id> --instruction "..."
  → 解析 preset（代码）
  → 一次 LLM：共享规则 + 冲突策略 + instruction
  → ClarityEditResult { body, clarity_review }
  → Draft.clarity_review = ...; upsert_draft（frontmatter）
  → CLI 打印 / --json 含 clarity_review
```

语义复核是同一次输出步骤，不是第二次请求。不触发 sources sync、证据分析或发现。

### 5.3 数据模型

挂在现有 `Draft`（frontmatter 已序列化除 `body` 外字段），可选字段，旧稿缺省兼容：

```yaml
clarity_review:
  preset: asd-ste100-inspired   # | asd-ste100-technical
  rules_version: finch-clarity-v1
  changes:                      # max 3
    - rule_id: CL03
      before: "..."
      after: "..."
      reason: "..."
  meaning_check: passed         # | needs_review
  missing_information: []
```

`meaning_check=passed` 仅表示本次复核未发现原意变化，不表示官方合规或事实已独立验证。  
`needs_review` 时：风险句恢复原句或列入缺口；其余安全修改仍可交付，不整篇作废。

`rules_version` 常量由代码维护；规则文档重大变更时递增。若日后有编辑结果缓存，指纹须含：原稿内容/版本、preset、`rules_version`、所用 VoiceProfile 版本。无现成缓存则不新建。

### 5.4 短训练（P2）

触发：用户明确说「帮我练习清晰表达」等。  
路由：`expression-practice`（`finch practice …`）。  
流程：从当前稿/尝试选一处问题 → 说明问题并请作者先改一句 → 比较后反馈一个改进点 + 一个剩余问题 → 再给参考版本与适用条件。  
诊断维度在 `diagnosis-rules.md` 增加「清晰度（引用 CL01–CL08）」；仍每次只挑最大一个问题。不先给完整范文。不新建 Practice 字段除非现有 `diagnosis` 文本不足（默认文本诊断足够）。

### 5.5 文章清晰视角（P3）

在 `ArticleReport` 增加判断字段 `clarity_cost_reductions`（0–3 条；无则空列表），每条：`excerpt`、`method`、`reader_effect`、`mini_exercise`、可选 `rule_id`（CL*）。分析提示词要求从原文识别最多三种降低理解成本的写法；CLI 呈现为独立一段。  
读不到正文 → 请用户提供文本，不据标题推断。仍即算即打印、不落库。不自动进入 rewrite 或 practice。

## 6. 提示词要求

改写路径须加载共享规则，并包含：

```text
Apply the Finch ASD-STE100-inspired clarity rules.
For Chinese content, use the project's Chinese adaptations.
Do not claim strict ASD-STE100 compliance.

Keep the author's claims, evidence, numbers, conditions,
negation, uncertainty, and source attribution unchanged.
Prefer local edits. Do not invent actors, examples, or measurements.
Preserve useful narrative and metaphors in the default preset.
Use explicit actions and prerequisites in the technical preset.
Explain at most three high-value edits with their rule IDs.
If clarity requires missing facts, identify the gap instead of filling it.
Check the final wording for changes in meaning.
```

安全示例：缺指标的「显著提升」→ 指出缺口，不补造数字；作者补充具体改动与单次耗时后，可改为限定「这次运行」的句子，不得扩成「总是」。

用户只要改稿时，不自动附加练习或长篇教学。

## 7. 关键字与发现

须在下列位置出现 `ASD-STE100`（及必要时 Simplified Technical English / 简化技术英语 / 清晰表达）：

- README 表达优化说明 + 触发示例
- `idea-to-draft`、`expression-practice`、`article_analysis` 的 description / 正文引用
- 共享规则文档与编辑提示词
- `feynman-practice`、`sticky-message` 指向共享规则（不复制全文）

触发示例（对话/instruction）：

- 「用 ASD-STE100 的原则优化这段话，保留我的语气。」
- 「按 ASD-STE100-inspired 检查这篇草稿，说明最重要的三处修改。」
- 「把这段内容改成清晰的技术操作说明。」

关键字不强制写入生成的文章/帖子/回复正文。

## 8. 错误处理与边界

| 情况 | 行为 |
|---|---|
| draft 不存在 | 现有 CLI 退出码，不旁路编辑 |
| 信息缺口阻碍具体表达 | `missing_information` 列出；不编造 |
| 语义风险 | `needs_review`；恢复风险句或提问；其余安全修改可保留 |
| URL 无正文 | 文章路径请用户贴文本 |
| Critic / 发布 | 不改生命周期；revise 仍不发布、不确认立场 |

## 9. 测试与验收

**自动化**

- 预设解析单元测试
- `ClarityReview` 写入/读出 Draft frontmatter；旧稿无字段仍可读
- `drafts revise --json` 含 `clarity_review`；默认 preset；technical 触发
- 现有 drafts / practice / article 回归不因可选字段失败

**人工语义样例（必测）**

- 「可能减少误报」不能改成「减少误报」
- 「这次运行」不能改成「系统总是」
- 「如果已有人工标注集」不能删去前置条件
- 未知操作主体不能改成作者亲自执行
- 有效比喻在默认模式可保留

语义质量以固定样稿人审为准，不用模型自评分数当通过条件。

**验收表**

| 项目 | 方式 |
|---|---|
| 关键字落地 | `rg ASD-STE100` 命中 README、技能、提示词、共享规则 |
| 可触发 | 自然语言 instruction 进入清晰编辑；输出体现规则 |
| 原意保持 | 样稿人审无新增事实/数字变化/否定丢失/条件范围变化 |
| 缺口处理 | 「显著提升」指出缺口，不补造量化 |
| 风格保留 | 默认保留有效故事/比喻；technical 明确动作与前置条件 |
| 教学克制 | 编辑最多三条关键修改；训练先等作者尝试 |
| 状态正确 | 不确认立场、不发布；沿用 Draft 生命周期 |
| 耗时 | 每次最多一次编辑请求；不承诺固定改善百分比 |

## 10. 实施顺序

1. **P0**：核实路径（本设计已对齐仓库）；确认共享规则唯一位置。
2. **P1**：新增共享规则；更新 README/技能/提示词；扩展 revise 模型与 CLI；单元/CLI 测试。
3. **P2**：expression-practice 诊断维度 + 触发说明；会话交互验证。
4. **P3**：`ArticleReport.clarity_cost_reductions` + 提示词与 CLI 呈现；读失败 fail-closed。
5. **验证**：五篇旧草稿人审；收集保留/撤回的关键修改理由。

## 11. MVP 完成定义

用户对已保存草稿执行 `finch drafts revise`（任意 instruction，默认已加载清晰规则；或显式 ASD-STE100 / 技术说明）后，Finch 交付保留原意与个人语气的改稿、最多三条带规则 ID 的关键修改、真实信息缺口；`clarity_review` 写入 Draft；关键字与规则进入文档与技能。显式训练与文章清晰视角按同一规则可用。不自动确认立场、不发布。
