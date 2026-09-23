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

## CLI

讨论摘要走 `finch dialogue`（`save` / `search` / `show` / `forget`），不要直接写 `dialogue/` YAML。命令见 `../SKILL.md`。`user_confirmed` 必须带用户原话 `confirmation_quote`。
