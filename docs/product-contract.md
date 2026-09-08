# Finch 产品契约

Finch 是一个**同行连接与个人表达系统**：帮助你发现值得长期交流的同行，理解对方正在解决的问题，准备有价值的互动，延续对话并积累关系上下文，最终从实践与交流中形成自己的观点，写出像自己的内容。

这不是一个「证据驱动的内容生成工具」。内容生产是连接的**结果**，不是连接的目的。

## 核心闭环

```text
发现值得连接的同行
→ 理解对方正在解决的问题
→ 准备一次有价值的互动
→ 延续对话并积累关系上下文
→ 从实践与交流中形成观点
→ 写出像自己的内容
→ 吸引更多相关同行
```

两条循环相互咬合，不是两条平行竞争的轨道：

- **连接主循环**（前端）：发现同行 → 准备互动 → 记录关系 → 继续对话。这是 Finch 每天首先呈现的东西。
- **表达复利循环**（后端）：从实践与对话中形成观点 → 写成像自己的内容 → 吸引更多同行。它服从连接目标。

## 北极星指标

> 每周新增或加深多少个「有上下文、可继续」的同行关系。

粉丝数、发帖数、草稿数、点赞、曝光都不是核心成功指标，仅作辅助数据。

## 对象所有权

| 对象 | 归属 | 规则 |
|---|---|---|
| `PeerProfile` | 关系领域 | 记录「这个人是谁、为什么值得继续交流」。外部作者按 `platform + author_id` 幂等归一化。 |
| `InteractionProposal` | 关系领域 | 待批准建议，不是已发生的互动。含 `contribution_type` / `why_this_person` / `why_now` / `expected_conversation_opening`。 |
| `InteractionRecord` | 关系领域 | 单独记录真正发生过的互动事实。不能用 Proposal 状态替代。 |
| `ConversationThread` | 关系领域 | 同一同行、同一主题的多次互动串联。 |
| `AuthorIdea` | 表达领域 | 只表达用户自己的立场。来源：个人实践、已发生的对话、用户输入。 |
| `VoiceProfile` | 表达领域 | 只从用户亲写文本、明确批准样本、用户修改后的最终版本更新。 |

**三条铁律：**

1. **候选 ≠ 事实**：Proposal（建议）、InteractionRecord（已发生互动）、结果反馈是三个不同事实，不得互相替代。
2. **外部 ≠ 个人经验**：搜索到的外部帖子（`ExternalPost`）和他人对话永远不能伪装成用户亲历；只有经验证的 `ConversationEvidence` 才可通过 `promote_to_personal` 提升。
3. **批准 ≠ 已发布**：所有公开回复、引用、私信、原创内容默认需要人工批准。批准只创建发布意图或批准记录，不等于已经发出。

## 硬边界

- Finch 独立运行，不接入、调用或依赖 builderDNA。
- 不共享数据库、状态文件、Python 包、Skill、CLI 或数据契约。
- 不为未来可能的项目集成增加 adapter、handoff、export/import 等抽象。
- 保留 `finch` 统一 CLI，继续采用 Skill + 领域服务架构。
- 不恢复通用 Graph Runtime。

## 职责边界

- **LLM** 只生成候选与语义判断（评分维度及理由、草稿、诊断）。
- **Python** 负责状态机、幂等、去重、评分汇总、发布门禁。LLM 输出永远不携带 `total` 分。
- **发布门禁**：`guard.evaluate_execution` 未经批准与验证返回 `rejected`/`unknown`，永不 success。

## 非目标

- 不是自动化涨粉工具。
- 不是「从 commit 直接发帖」的流水线（证据先行：Commit → EngineeringEvent → EvidenceCard → … 逐级提炼）。
- 不是 CRM / 销售线索库——PeerProfile 不是把同行做成 lead。
- 不追求覆盖所有平台；一个平台失败不取消其他平台结果。

## Skill 边界

**核心闭环 Skill**（默认进入主循环）：`peer-discovery`、`interaction-preparation`、`conversation-follow-up`、`idea-discovery`、`idea-to-draft`、`voice-profile`、`weekly-reflection`。

**独立训练工具**（不进入默认流水线）：`expression-practice`、`writing-style-analysis`、`feynman-practice`、`sticky-message`。其中 `writing-style-analysis` 只读，分析他人风格不得自动写入 `VoiceProfile`。
