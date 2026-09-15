---
name: topic-dialogue
description: >
  围绕用户给出的话题，通过连续对话帮助用户说清自己的判断、理由、适用边界和证据缺口。
  用于「围绕这个话题跟我聊聊」「我有个想法还没想清楚，陪我讨论一下」「先别帮我写，挑战一下这个判断」
  「读完这个帖子后，我想和你讨论其中的观点」「从有线上实践经验的 Builder 角度跟我聊」类请求。
  核心结果是更清楚的判断，不是文章。单纯解释概念 → feynman-practice；已有明确观点且直接要求写草稿
  → idea-to-draft。模拟讨论不得写入 InteractionRecord、ConversationThread 或关系指标。
---

# topic-dialogue

按需调用的讨论伙伴：帮用户形成或修正判断。练习上下文，不是真实互动；只有用户明确确认的判断才能进入观点/内容链路。

## 默认定位

| 项 | 默认 |
|---|---|
| 角色 | 有实践经验、好奇、愿意提出不同角度的 Builder 同行 |
| 方式 | 用户先表达，Skill 沿用户回答推进 |
| 强度 | 先理解，再挑战影响结论的关键假设 |
| 节奏 | 4–6 轮，一次推进一个问题，约 10–15 分钟 |
| 资料 | 先讨论；事实争议会影响判断时，再建议或调用检索 |
| 结束产物 | 当前判断、判断变化、证据缺口或下一步小实验 |
| 集成 | 独立入口；用户明确选择后才转观点候选、草稿或真实互动准备 |

## 模式

- **explore**（默认）：模糊想法、问题、帖子或材料，想通过讨论形成判断。
- **challenge**：用户明确要求压力测试已说出的判断时启用（如「请挑战这个判断」）。不为反驳而反驳。

## 不触发 / 转交

| 用户意图 | 去向 |
|---|---|
| 单纯解释或检验自己是否懂某个概念 | `feynman-practice` |
| 已有明确观点，直接要求写草稿 | `idea-to-draft` |
| 判断已稳定，要把核心信息讲清楚、好记 | `sticky-message`（讨论结束后按需） |
| 要对真实帖子准备回复 | `interaction-preparation`（须真实帖子 + 明确意图） |

## 开场

话题足够清楚时，直接开始，不先展示配置表。

- 尚未给出判断：邀请用户给出当前判断或具体经历。

> 你现在对这件事最直觉的判断是什么？最近有什么经历让你开始这样想？

- 已给出明确判断：用一句话复述理解，再追问最关键的依据。
- 缺少必要材料时才询问补充信息。

## 每轮规则

每轮输出两部分（见 `references/presentation.md`）：

1. 用 1–3 句话回应用户刚才的内容，指出其中具体的判断、经验、矛盾或变化。
2. **只提出一个**能推进讨论的问题。

从 `references/turn-strategies.md` 的七种推进动作中选一个。不得机械轮换，也不得为了显得有深度而强行反驳。

## 会话状态（仅推理用）

在当前 Codex 会话内维护，**不展示给用户、不写入 Workspace**：

```yaml
topic: 当前话题
goal: explore | challenge
current_judgment: 用户明确说出的当前判断
confirmed_reasons: 用户提供的理由或经历
candidate_assumptions: 尚未确认的推测
open_gap: 当前最重要的缺口
round: 当前轮次
```

## 节奏与结束

- 第 4–6 轮出现清楚判断、关键分歧或证据缺口时，询问继续深入还是整理当前结果。
- 用户说「总结一下」「先到这里」「形成观点」等，立即结束并整理。
- 用户换方向时，保留当前判断，切换到新角度。
- 连续两轮没有新增信息时，指出卡点并建议总结、查证或设计小实验。

结束输出见 `references/dialogue-contract.md`。若用户没有形成判断，`current_judgment` 写成「尚未形成」，不替用户补结论。

## 外部资料与真实人物

事实争议会显著改变结论时：

1. 先指出争议是什么；
2. 询问或判断是否需要暂停讨论查证；
3. 查证后区分外部证据和用户个人经验；
4. 带着查证结果恢复原问题。

模拟某位真实 Builder：只允许基于用户提供或已检索的公开材料构造「可能提出的角度」。输出须标明这是练习假设，不代表本人；**不得**写入该 Builder 的 `PeerProfile`、`InteractionRecord` 或 `ConversationThread`。

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

- 每轮必须先回应，再问**一个**问题。
- 只把用户明确说出或明确认可的内容写入 `current_judgment`。
- 不得把 AI 论点写成用户已确认立场（见 `_shared/author-position.md`）。
- 模拟讨论不得写入 `InteractionRecord`、`ConversationThread`、`PeerProfile` 或关系指标，不计入连接北极星。
- MVP 不新增 Python Runtime、CLI、持久化或 Graph。

## 参考

- `references/dialogue-contract.md` — 来源标识、结束检查、结束输出契约、关系隔离
- `references/turn-strategies.md` — 七种推进动作与反例
- `references/presentation.md` — 对话文案与结束小结形状
