# Finch 产品契约

Finch 是一个**同行连接与个人表达系统**：帮助你发现值得长期交流的同行与真实工具使用者，理解对方情境与具体阻碍，准备有价值的互动，延续对话并积累关系上下文，最终从实践与交流中形成自己的观点，写出像自己的内容。

连接对象包括技术同行，以及正在使用或部署工具、遭遇具体工作阻碍的人。Finch 记录对话事实与明确承诺，不替用户做产业战略（Quinn）或跨来源痛点聚类 / 产品机会判断（builderDNA）。

这不是一个「证据驱动的内容生成工具」。内容生产是连接的**结果**，不是连接的目的。

## 核心闭环

```text
发现值得连接的同行与使用者
→ 理解对方正在解决的问题与当前办法
→ 准备一次有价值的互动
→ 延续对话并积累关系上下文
→ 从实践与交流中形成观点
→ 写出像自己的内容
→ 吸引更多相关同行
```

两条循环相互咬合，不是两条平行竞争的轨道：

- **连接主循环**（前端）：发现同行/使用者 → 准备互动 → 记录关系 → 继续对话。这是 Finch 每天首先呈现的东西。
- **表达复利循环**（后端）：从实践与对话中形成观点 → 写成像自己的内容 → 吸引更多同行。它服从连接目标。

问题摘要、workaround 与试用反馈作为 **ConversationThread 内笔记**（带来源），不建立独立问题库或工具收款后台。工具运行与收款权威记录属于具体工具项目。

## 北极星指标

> 每周新增或加深多少个「有上下文、可继续」的同行关系。

粉丝数、发帖数、草稿数、点赞、曝光都不是核心成功指标，仅作辅助数据。点赞、愿意试用、实际使用、获得价值、付款分别表述，不合成商业总分。

## 对象所有权

| 对象 | 归属 | 规则 |
|---|---|---|
| `PeerProfile` | 关系领域 | 记录「这个人是谁、为什么值得继续交流」。外部作者按 `platform + author_id` 幂等归一化。可含 `current_work`、实践证据引用与证据状态（sourced / author_stated / pending_review）。**不是 lead。** |
| `Opportunity` | 关系领域 | 轻量**交流**机会。发现结果，无审批状态机；每日浏览 8–12，不含完整回复草稿。浏览卡固定呈现：正在做什么、为何相关、可贡献什么、下一步、时间与不确定性（附来源）。含 `shared_problem`、`contribution_basis_refs`、`next_action`、`estimated_minutes`。**不是商业机会。** |
| `InteractionProposal` | 关系领域 | 用户选中后深度准备的待批准建议，不是已发生的互动。默认每次最多 10 位选中机会；可含最小贡献（试用/复现/观察）而无公开回复草稿。含 `contribution_type` / `why_this_person` / `why_now` / `expected_conversation_opening`。 |
| `InteractionRecord` | 关系领域 | 单独记录真正发生过的互动事实（可无 proposal_id）。不能用 Proposal 状态替代。 |
| `ConversationThread` | 关系领域 | 同一同行、同一主题的多次互动串联；问题/workaround/使用反馈以线程笔记形式挂在线索上（必填 `source_ref`）；跟进由新回复/承诺到期/新证据/相关更新触发，时间陈旧 alone 不触发对外联系。重要关系周期回顾默认关闭、用户主动开启。 |
| `AuthorIdea` | 表达领域 | 只表达用户自己的立场（修订历史 append-only；草稿 ≠ 观点已证实）。来源：个人实践、已发生的对话、用户输入。 |
| `VoiceProfile` | 表达领域 | 只从用户亲写文本、明确批准样本、用户修改后的最终版本更新；不因发现或试用反馈自动调整。 |

**三条铁律：**

1. **候选 ≠ 事实**：Proposal（建议）、InteractionRecord（已发生互动）、结果反馈是三个不同事实，不得互相替代。礼貌兴趣 ≠ 已开始试用 ≠ 付款。
2. **外部 ≠ 个人经验**：搜索到的外部帖子（`ExternalPost`）和他人对话永远不能伪装成用户亲历；只有经验证的 `ConversationEvidence` 才可通过 `promote_to_personal` 提升。对方反馈不是作者本人取得效果的证明。
3. **批准 ≠ 已发布**：所有公开回复、引用、私信、原创内容默认需要人工批准。批准只创建发布意图或批准记录，不等于已经发出。

## 硬边界

- Finch 独立运行，不接入、调用或依赖 builderDNA。
- 不共享数据库、状态文件、Python 包、Skill、CLI 或数据契约。
- 不为未来可能的项目集成增加 adapter、handoff、export/import 等抽象。
- 与 Quinn、builderDNA、独立工具项目之间只按需手动传递普通文本。
- 保留 `finch` 统一 CLI，继续采用 Skill + 领域服务架构。
- 不恢复通用 Graph Runtime。
- 维持 `gh` / `opencli` 只读；不新增外发能力。

## 职责边界

- **LLM** 只生成候选与语义判断（评分维度及理由、草稿、诊断、笔记摘要候选）。
- **Python** 负责状态机、幂等、去重、评分汇总、发布门禁、笔记校验与写入。LLM 输出永远不携带 `total` 分。
- **发布门禁**：`guard.evaluate_execution` 未经批准与验证返回 `rejected`/`unknown`，永不 success。
- Finch 负责找人、准备沟通、记录回应与承诺、提示跟进与表达；痛点是否值得构建由 builderDNA/用户判断；收款由工具项目维护。

## 非目标

- 不是自动化涨粉工具。
- 不是「从 commit 直接发帖」的流水线（证据先行：Commit → EngineeringEvent → EvidenceCard → … 逐级提炼）。
- 不是 CRM / 销售线索库——PeerProfile 不是 lead；Opportunity 不是商业机会看板。
- 不建设独立问题证据库、产品适配评分或工具付费漏斗。
- 不追求覆盖所有平台；一个平台失败不取消其他平台结果。

## 连接发现约定（Value Discovery）

- **目的**：持续交流与相互帮助；排序与复盘关注可继续的对话，不以商业价值筛人。
- **Builder 证据**：项目产物、具体实践分享、业务问题解决记录任一成立即可；不把公开仓库、职位或提交数当硬门槛。仅有 bio/转发时标记待了解。
- **召回预算**：Agent 同行约 50%、相邻领域约 30%、场景实践者约 20%——用于查询槽位，不是最终名单配额；不足不凑数。
- **每日结构**：8–12 位去重作者；已有对话独立呈现；选中后最多深度准备 3 位。
- **行动**：有真实贡献或具体问题才准备互动；否则先了解/试用。`next_action` 由 Python 从模式与实践依据派生，不为所有候选强行生成回复。
- **时间预算**：默认约 20 分钟（忙时 10）；建议行动须给出预计分钟，可跳过或缩小。
- **自动程度**：自动发现、证据整理与排序；选中后准备；用户实际发送。批准、草稿均不算已发生互动。
- **跟进**：新回复、本人承诺到期、新证据、相关更新；重要关系可主动开启周期回顾（只提醒审阅，不自动问候）。

## Skill 边界

**核心闭环 Skill**（默认进入主循环）：`peer-discovery`、`interaction-preparation`、`conversation-follow-up`、`idea-discovery`、`idea-to-draft`、`voice-profile`、`weekly-reflection`。不新增 monetization / problem-discovery 核心 Skill。

**独立训练工具**（不进入默认流水线）：`expression-practice`、`writing-style-analysis`、`feynman-practice`、`sticky-message`。其中 `writing-style-analysis` 只读，分析他人风格不得自动写入 `VoiceProfile`。
