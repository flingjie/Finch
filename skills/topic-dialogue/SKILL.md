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

## CLI

讨论摘要用 `finch dialogue`，不要直接写 workspace YAML。

```bash
finch dialogue save --file note.json --expected-revision N --json
finch dialogue search "<query>" --limit 3 --json
finch dialogue show <id> --json
finch dialogue forget <id> --json
```

- 检索关键词是位置参数，不是 `--query`。
- `save` 成功输出 DialogueNote JSON；错误是 `{"ok": false, "error": "..."}`。
- `--expected-revision`：`0` 创建；大于 0 时必须与当前 revision 一致，不符则冲突，先 `show` 再保存。
- `position_status=user_confirmed` 必须带 `confirmation_quote`（用户原话）。空引用会被拒绝。这不是 ContentJob 的已确认观点；已确认观点走 `finch ideas`。
- 用户说「别记这段」：跳过保存；已经保存则 `finch dialogue forget <id>`。
- 讨论不得写入关系记录。

## 参考

- `references/dialogue-contract.md` — 来源标识、结束检查、结束输出契约、关系隔离
- `references/turn-strategies.md` — 七种推进动作与反例
- `references/presentation.md` — 对话文案与结束小结形状
- `_shared/dialogue-policy.md` — 延伸点选择、授权边界、收束与停止条件
- `_shared/agent-presentation.md` — 完成后如何对用户说话
